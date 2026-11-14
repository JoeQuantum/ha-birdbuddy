"""Real atomic storage boundaries and replay behavior, without cloud calls."""

import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from birdbuddy.exceptions import AuthenticationFailedError
from birdbuddy.feed import FeedBatch, FeedCheckpoint, FeedNode
from homeassistant.core import CoreState
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.util.file import WriteError
import pytest

from custom_components.birdbuddy.intakestore import (
    ActionAlreadyRecordedError,
    DurableIntake,
    IntakeCapacityError,
    IntakePermanentError,
    IntakePersistenceError,
)

STAMP = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)


@pytest.fixture
def hass_storage():
    """Use the real Store writer: an in-memory storage mock cannot prove durability."""
    yield {}


@pytest.fixture
def hass_config_dir(tmp_path):
    return str(tmp_path)


@pytest.fixture(autouse=True)
def isolated_storage_directory(hass, tmp_path):
    """The minimum harness predates hass_config_dir; isolate its real writes too."""
    hass.config.config_dir = str(tmp_path)


def client():
    result = MagicMock()
    result.export_feed_checkpoint.return_value = FeedCheckpoint().to_dict()
    return result


def batch(*ids, previous=None, extra=None):
    nodes = [
        FeedNode(
            {
                "id": item_id,
                "__typename": "FeedItemNewPostcard",
                "createdAt": STAMP.isoformat(),
                **(extra or {}),
            }
        )
        for item_id in ids
    ]
    return FeedBatch.create(nodes, (previous or FeedCheckpoint()).advance(nodes))


async def ledger(hass, *, baseline=(), **kwargs):
    remote = client()
    intake = DurableIntake(hass, "entry-a", "account-a", **kwargs)
    await intake.async_initialize(remote)
    initial = batch(*baseline)
    await intake.async_accept_batch(remote, initial, mode="bootstrap")
    remote.reset_mock()
    return intake, remote, initial


def stored(intake):
    return json.loads(Path(intake._store.path).read_text())["data"]


async def test_setup_boundary_retains_arrival_until_ha_started(hass):
    hass.set_state(CoreState.starting)
    intake, remote, initial = await ledger(
        hass, baseline=tuple(f"old-{i}" for i in range(20))
    )
    assert intake.initialized and intake.is_bootstrapped
    assert intake.pending_count == 0
    arriving = batch("arrived-during-startup", previous=initial.checkpoint)
    await intake.async_accept_batch(remote, arriving)
    delivered = AsyncMock()
    assert await intake.async_dispatch(delivered) == 0
    delivered.assert_not_called()
    hass.set_state(CoreState.running)
    assert await intake.async_dispatch(delivered) == 1
    assert delivered.call_args.args[0].item_id == "arrived-during-startup"
    assert intake.pending_count == 0


async def test_ack_observes_private_durable_queue_and_proposed_checkpoint(hass):
    intake, remote, initial = await ledger(hass)
    incoming = batch(
        "new-a",
        previous=initial.checkpoint,
        extra={
            "reportToken": "secret-report",
            "nested": {"accessToken": "secret-auth"},
            "medias": [
                {
                    "id": "m-a",
                    "contentUrl": "https://example.invalid/image?Signature=private",
                }
            ],
        },
    )

    def verify_before_ack(batch_id):
        saved = stored(intake)
        assert batch_id == incoming.batch_id
        assert saved["checkpoint"] == incoming.checkpoint.to_dict()
        assert len(saved["pending"]) == 1
        assert "secret-report" not in str(saved)
        assert "secret-auth" not in str(saved)
        assert "Signature=private" in str(saved)
        assert Path(intake._store.path).stat().st_mode & 0o077 == 0

    remote.ack_feed_batch.side_effect = verify_before_ack
    assert await intake.async_accept_batch(remote, incoming) == 1
    remote.ack_feed_batch.assert_called_once_with(incoming.batch_id)


async def test_restart_restores_checkpoint_before_any_retrieval_and_delivers_once(hass):
    intake, remote, initial = await ledger(hass)
    incoming = batch("new-a", previous=initial.checkpoint)
    await intake.async_accept_batch(remote, incoming)
    restarted = DurableIntake(hass, "entry-a", "account-a")
    replacement = client()
    await restarted.async_initialize(replacement)
    replacement.import_feed_checkpoint.assert_called_once_with(
        incoming.checkpoint.to_dict()
    )
    replacement.retrieve_feed_batch.assert_not_called()
    callback = AsyncMock()
    assert await restarted.async_dispatch(callback) == 1
    await restarted.async_accept_batch(replacement, incoming, mode="backfill")
    assert await restarted.async_dispatch(callback) == 0
    callback.assert_awaited_once()


async def test_swallowed_write_error_keeps_batch_unacknowledged(hass):
    intake, remote, initial = await ledger(hass)
    before = stored(intake)
    incoming = batch("new-a", previous=initial.checkpoint)
    with patch.object(
        intake._store,
        "_async_write_data",
        new=AsyncMock(side_effect=WriteError("synthetic disk failure")),
    ):
        with pytest.raises(IntakePersistenceError, match="not verified"):
            await intake.async_accept_batch(remote, incoming)
    remote.ack_feed_batch.assert_not_called()
    assert stored(intake) == before
    assert intake.pending_count == 0
    assert await intake.async_accept_batch(remote, incoming) == 1


async def test_shutdown_cannot_ack_a_deferred_store_write(hass):
    intake, remote, initial = await ledger(hass)
    hass.set_state(CoreState.stopping)
    try:
        with pytest.raises(IntakePersistenceError, match="stopping"):
            await intake.async_accept_batch(
                remote, batch("new-a", previous=initial.checkpoint)
            )
        remote.ack_feed_batch.assert_not_called()
    finally:
        hass.set_state(CoreState.running)


async def test_crash_after_persist_before_ack_recovers_new_work(hass):
    intake, remote, initial = await ledger(hass)
    incoming = batch("new-a", previous=initial.checkpoint)
    remote.ack_feed_batch.side_effect = RuntimeError("simulated crash")
    with pytest.raises(RuntimeError):
        await intake.async_accept_batch(remote, incoming)
    restarted = DurableIntake(hass, "entry-a", "account-a")
    restored = client()
    await restarted.async_initialize(restored)
    assert restarted.pending_count == 1
    restored.import_feed_checkpoint.assert_called_once_with(
        incoming.checkpoint.to_dict()
    )
    assert await restarted.async_dispatch(AsyncMock()) == 1


async def test_crash_after_external_delivery_replays_the_same_stable_event_id(hass):
    intake, remote, initial = await ledger(hass)
    await intake.async_accept_batch(remote, batch("new-a", previous=initial.checkpoint))
    callback = AsyncMock()
    with patch.object(intake._store, "async_save", new=AsyncMock()):
        with pytest.raises(IntakePersistenceError):
            await intake.async_dispatch(callback)
    first_id = callback.call_args.args[0].event_id
    restarted = DurableIntake(hass, "entry-a", "account-a")
    await restarted.async_initialize(client())
    assert await restarted.async_dispatch(callback) == 1
    assert callback.await_count == 2
    assert callback.call_args.args[0].event_id == first_id


async def test_failed_item_does_not_poison_later_items_or_persist_error_text(hass):
    intake, remote, initial = await ledger(hass)
    await intake.async_accept_batch(
        remote, batch("bad", "good", previous=initial.checkpoint)
    )

    async def callback(record):
        if record.item_id == "bad":
            raise IntakePermanentError("secret token in unsafe exception text")

    assert await intake.async_dispatch(callback) == 1
    assert intake.dead_letter_count == 1
    assert intake.pending_count == 0
    assert "secret token" not in str(stored(intake))
    assert (
        next(iter(stored(intake)["pending"].values()))["error_type"]
        == "IntakePermanentError"
    )
    assert await intake.async_retry_failed("bad") == 1
    assert await intake.async_dispatch(AsyncMock()) == 1


async def test_retries_back_off_and_stop_at_the_documented_limit(hass):
    now = [STAMP]
    intake, remote, initial = await ledger(hass, clock=lambda: now[0], max_attempts=2)
    await intake.async_accept_batch(remote, batch("bad", previous=initial.checkpoint))
    callback = AsyncMock(side_effect=RuntimeError("transient"))
    assert await intake.async_dispatch(callback) == 0
    assert await intake.async_dispatch(callback) == 0
    callback.assert_awaited_once()
    now[0] += timedelta(seconds=31)
    assert await intake.async_dispatch(callback) == 0
    assert callback.await_count == 2
    assert intake.dead_letter_count == 1
    now[0] += timedelta(days=31)
    assert await intake.async_dispatch(callback) == 0
    assert intake.dead_letter_count == 1


@pytest.mark.parametrize(
    "error", [AuthenticationFailedError(), ConfigEntryAuthFailed()]
)
async def test_authentication_failure_remains_repairable_and_pending(hass, error):
    intake, remote, initial = await ledger(hass)
    await intake.async_accept_batch(remote, batch("new-a", previous=initial.checkpoint))
    with pytest.raises(type(error)):
        await intake.async_dispatch(AsyncMock(side_effect=error))
    assert intake.pending_count == 1
    assert next(iter(stored(intake)["pending"].values()))["attempts"] == 0


@pytest.mark.parametrize(
    "limits,ids,extra",
    [
        ({"max_pending": 1}, ("a", "b"), {}),
        ({"max_bytes": 2000}, ("a",), {"content": "x" * 3000}),
    ],
)
async def test_capacity_failure_preserves_old_state_and_does_not_ack(
    hass, limits, ids, extra
):
    intake, remote, initial = await ledger(hass, **limits)
    before = stored(intake)
    with pytest.raises(IntakeCapacityError):
        await intake.async_accept_batch(
            remote, batch(*ids, previous=initial.checkpoint, extra=extra)
        )
    remote.ack_feed_batch.assert_not_called()
    assert stored(intake) == before
    assert intake.pending_count == 0


async def test_processed_capacity_is_checked_before_external_delivery(hass):
    intake, remote, initial = await ledger(hass, baseline=("history",), max_processed=1)
    await intake.async_accept_batch(remote, batch("new-a", previous=initial.checkpoint))
    callback = AsyncMock()
    with pytest.raises(IntakeCapacityError, match="before delivery"):
        await intake.async_dispatch(callback)
    callback.assert_not_called()
    assert intake.pending_count == 1


async def test_processed_retention_is_explicit_but_pending_work_is_not_evicted(hass):
    now = [STAMP]
    intake, remote, initial = await ledger(
        hass, baseline=("old",), clock=lambda: now[0]
    )
    await intake.async_accept_batch(
        remote, batch("pending", previous=initial.checkpoint)
    )
    now[0] += timedelta(days=31)
    await intake.async_accept_batch(
        remote, batch("old", previous=initial.checkpoint), mode="backfill"
    )
    assert intake.pending_count == 2
    assert stored(intake)["processed"] == {}


@pytest.mark.parametrize(
    "damage",
    [b"broken-json", b'{"version": 2,"key":"birdbuddy.entry-a.intake","data":{}}'],
)
async def test_corrupt_or_future_state_is_never_erased_or_silently_rebootstrapped(
    hass, damage
):
    intake = DurableIntake(hass, "entry-a", "account-a")
    path = Path(intake._store.path)
    path.parent.mkdir(parents=True, exist_ok=True)
    await hass.async_add_executor_job(path.write_bytes, damage)
    remote = client()
    with pytest.raises(IntakePersistenceError):
        await intake.async_initialize(remote)
    assert await hass.async_add_executor_job(path.read_bytes) == damage
    remote.import_feed_checkpoint.assert_not_called()
    assert not intake.initialized


async def test_account_identity_mismatch_cannot_reuse_a_durable_queue(hass):
    intake, _, _ = await ledger(hass)
    before = await hass.async_add_executor_job(Path(intake._store.path).read_bytes)
    wrong = DurableIntake(hass, "entry-a", "account-b")
    with pytest.raises(IntakePersistenceError, match="identity"):
        await wrong.async_initialize(client())
    assert (
        await hass.async_add_executor_job(Path(intake._store.path).read_bytes) == before
    )


async def test_backfill_over_100_records_is_bounded_and_only_emits_requested_events(
    hass,
):
    intake, remote, initial = await ledger(hass)
    historical = batch(*(f"old-{i}" for i in range(150)), previous=initial.checkpoint)
    assert await intake.async_accept_batch(remote, historical, mode="backfill") == 150
    delivered = AsyncMock()
    assert await intake.async_dispatch(delivered, max_items=100) == 100
    assert intake.pending_count == 50
    assert await intake.async_dispatch(delivered, max_items=100) == 50
    assert all(call.args[0].source == "backfill" for call in delivered.await_args_list)
    remote.ack_feed_batch.assert_called_once()
    remote.retrieve_feed_batch.assert_not_called()
    remote.identify_postcard.assert_not_called()
    remote.collect_postcard.assert_not_called()


async def test_cancelled_intake_does_not_ack_or_discard_the_replayable_batch(hass):
    intake, remote, initial = await ledger(hass)
    entered = asyncio.Event()
    release = asyncio.Event()
    real_save = intake._store.async_save

    async def blocked_save(state):
        entered.set()
        await release.wait()
        await real_save(state)

    incoming = batch("new-a", previous=initial.checkpoint)
    with patch.object(intake._store, "async_save", new=blocked_save):
        task = asyncio.create_task(intake.async_accept_batch(remote, incoming))
        await entered.wait()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()  # An outstanding write must drain before cancellation.
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    remote.ack_feed_batch.assert_not_called()
    assert intake.pending_count == 1
    assert await intake.async_accept_batch(remote, incoming) == 0


async def test_per_postcard_lock_serializes_same_card_without_blocking_another(hass):
    intake, _, _ = await ledger(hass)
    entered = asyncio.Event()
    release = asyncio.Event()
    order = []

    async def first():
        async with intake.postcard_lock("same"):
            order.append("first")
            entered.set()
            await release.wait()

    async def second():
        await entered.wait()
        async with intake.postcard_lock("same"):
            order.append("second")

    async def independent():
        await entered.wait()
        async with intake.postcard_lock("other"):
            order.append("other")
            release.set()

    await asyncio.gather(first(), second(), independent())
    assert order == ["first", "other", "second"]


async def test_interrupted_action_is_unknown_after_restart_and_requires_explicit_ack(
    hass,
):
    intake, _, _ = await ledger(hass)
    await intake.async_begin_action("card-a", {"operation": "collect", "share": False})
    assert stored(intake)["actions"]["card-a"]["status"] == "in_progress"
    restarted = DurableIntake(hass, "entry-a", "account-a")
    await restarted.async_initialize(client())
    assert restarted.unknown_action_count == 1
    with pytest.raises(ActionAlreadyRecordedError, match="unknown"):
        await restarted.async_begin_action("card-a", {"operation": "collect"})
    await restarted.async_acknowledge_action_retry("card-a")
    await restarted.async_begin_action("card-a", {"operation": "collect"})


async def test_known_success_is_not_automatically_repeated_but_identify_can_precede_collect(
    hass,
):
    intake, _, _ = await ledger(hass)
    await intake.async_begin_action("card-a", {"operation": "identify"})
    await intake.async_finish_action("card-a", "succeeded", {"postcard_id": "card-a"})
    await intake.async_begin_action(
        "card-a", {"operation": "collect", "identify": False}
    )
    await intake.async_finish_action(
        "card-a", "succeeded", {"collected_postcard_id": "collected-a"}
    )
    with pytest.raises(ActionAlreadyRecordedError, match="succeeded"):
        await intake.async_begin_action("card-a", {"operation": "collect"})


async def test_action_state_must_persist_before_the_caller_can_mutate(hass):
    intake, _, _ = await ledger(hass)
    with patch.object(intake._store, "async_save", new=AsyncMock()):
        with pytest.raises(IntakePersistenceError):
            await intake.async_begin_action("card-a", {"operation": "collect"})
    assert stored(intake)["actions"] == {}


async def test_unknown_actions_are_never_silently_evicted_by_retention(hass):
    now = [STAMP]
    intake, _, _ = await ledger(hass, max_actions=1, clock=lambda: now[0])
    await intake.async_begin_action("card-a", {"operation": "collect"})
    await intake.async_finish_action("card-a", "unknown")
    now[0] += timedelta(days=31)
    with pytest.raises(IntakeCapacityError):
        await intake.async_begin_action("card-b", {"operation": "collect"})
    assert intake.unknown_action_count == 1


async def test_interrupted_action_can_only_be_completed_by_same_id_read_reconciliation(
    hass,
):
    intake, _, _ = await ledger(hass)
    await intake.async_begin_action("card-a", {"operation": "collect"})
    await intake.async_finish_action("card-a", "unknown", error_type="TimeoutError")
    with pytest.raises(ValueError, match="No in-progress"):
        await intake.async_finish_action(
            "card-a",
            "succeeded",
            {
                "reconciled_by_read": True,
                "requested_postcard_id": "card-a",
                "collected_postcard_id": "different-card",
            },
        )
    assert intake.unknown_action_count == 1
    await intake.async_finish_action(
        "card-a",
        "succeeded",
        {
            "reconciled_by_read": True,
            "requested_postcard_id": "card-a",
            "collected_postcard_id": "card-a",
        },
    )
    assert intake.unknown_action_count == 0


@pytest.mark.parametrize(
    "result",
    [
        {"reportToken": "secret"},
        {"sharing_confirmed": "yes"},
        {"postcard_id": {"raw": "payload"}},
    ],
)
async def test_action_results_cannot_persist_raw_reports_or_untyped_flags(hass, result):
    intake, _, _ = await ledger(hass)
    await intake.async_begin_action("card-a", {"operation": "collect"})
    with pytest.raises(IntakePersistenceError):
        await intake.async_finish_action("card-a", "unknown", result)
    assert stored(intake)["actions"]["card-a"]["result"] == {}


async def test_opaque_item_ids_are_not_mistaken_for_credential_field_names(hass):
    intake, remote, initial = await ledger(hass)
    await intake.async_accept_batch(
        remote, batch("postcard-ending-in-token", previous=initial.checkpoint)
    )
    assert intake.pending_count == 1
    await intake.async_begin_action(
        "postcard-ending-in-token", {"operation": "collect"}
    )
    assert "postcard-ending-in-token" in stored(intake)["actions"]
