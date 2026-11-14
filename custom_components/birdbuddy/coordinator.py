"""Account telemetry, durable postcard intake and explicit user actions."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import timedelta
from typing import Any, TypedDict

from birdbuddy.client import BirdBuddy
from birdbuddy.exceptions import AuthenticationFailedError
from birdbuddy.feed import FeedNode
from birdbuddy.feeder import Feeder
from birdbuddy.transport import HttpResponseError
from birdbuddy.visits import Visit, normalize_visit
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import (
    CALLBACK_TYPE,
    CoreState,
    Event,
    EventOrigin,
    HomeAssistant,
)
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CONF_ACCOUNT_ID,
    CONF_POLLING_INTERVAL,
    DEFAULT_POLLING_INTERVAL,
    DOMAIN,
    EVENT_NEW_FEED_ITEM,
    EVENT_NEW_POSTCARD,
    EVENT_POSTCARD_BACKFILL,
    LOGGER,
)
from .device import BirdBuddyDevice
from .events import build_legacy_feed_item_event, build_postcard_event, postcard_details
from .intakestore import DurableIntake, IntakeError, IntakeRecord, async_drain_task
from .postcard_policy import PostcardCollectionRequest
from .repairs import check_legacy_listeners
from .visitors import RecentVisitors, VisitorCallback

_VISITOR_KINDS = {
    "FeedItemNewPostcard",
    "FeedItemCollectedPostcard",
    "FeedItemSpeciesSighting",
    "FeedItemSpeciesUnlocked",
    "FeedItemMysteryVisitorNotRecognized",
    "FeedItemMysteryVisitorResolved",
}


class _EventContext(TypedDict):
    config_entry_id: str
    account_id: str
    event_id: str
    source: str


class BirdBuddyDataUpdateCoordinator(DataUpdateCoordinator[BirdBuddy]):
    """Keep reads independent of inference, collection and sharing."""

    config_entry: ConfigEntry

    def __init__(
        self, hass: HomeAssistant, client: BirdBuddy, entry: ConfigEntry
    ) -> None:
        self.client = client
        self.feeders: dict[str, BirdBuddyDevice] = {}
        self.visitors: dict[str, RecentVisitors] = {}
        self.account_id = ""
        self.intake: DurableIntake | None = None
        self.last_update_timestamp = None
        self.last_feed_success = None
        self.feed_error_type: str | None = None
        self._feed_work_lock = asyncio.Lock()
        self._pending_intake_mode: str | None = None
        self._startup_unsub: CALLBACK_TYPE | None = None
        self._closing = False
        self._active_work: dict[asyncio.Task[Any], int] = {}
        self._shutdown_task: asyncio.Task[None] | None = None
        if account_id := entry.data.get(CONF_ACCOUNT_ID):
            self.client.bind_account(account_id)
        minutes = entry.options.get(CONF_POLLING_INTERVAL, DEFAULT_POLLING_INTERVAL)
        super().__init__(
            hass,
            LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=timedelta(minutes=minutes),
        )
        if hass.state is not CoreState.running:
            self._startup_unsub = hass.bus.async_listen_once(
                EVENT_HOMEASSISTANT_STARTED, self._async_started
            )

    async def _async_started(self, event: Event) -> None:
        """Deliver queued startup arrivals once automations can receive them."""
        self._startup_unsub = None
        if self._closing:
            return
        async with self.async_work():
            check_legacy_listeners(self.hass)
            await self.async_request_refresh()

    @asynccontextmanager
    async def async_work(self) -> AsyncIterator[None]:
        """Own account work through unload, including nested calls in one task."""
        if self._closing:
            raise ServiceValidationError("This Bird Buddy account is unloading")
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("Account work requires an asyncio task")
        self._active_work[task] = self._active_work.get(task, 0) + 1
        try:
            yield
        finally:
            remaining = self._active_work[task] - 1
            if remaining:
                self._active_work[task] = remaining
            else:
                del self._active_work[task]

    async def async_shutdown(self) -> None:
        """Drain old account work before another owner can open the same Store."""
        self._closing = True
        if self._startup_unsub is not None:
            self._startup_unsub()
            self._startup_unsub = None
        await super().async_shutdown()
        if self._shutdown_task is None:
            current = asyncio.current_task()
            tasks = [task for task in self._active_work if task is not current]
            self._shutdown_task = asyncio.create_task(
                self._async_finish_shutdown(tasks)
            )
        await async_drain_task(self._shutdown_task)

    async def _async_finish_shutdown(self, tasks: list[asyncio.Task[Any]]) -> None:
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        # Action cancellation can still journal an unknown outcome above. Close
        # only once that cleanup has completed, then reject any late old writer.
        if self.intake is not None:
            await self.intake.async_close()

    def _verify_identity(self) -> None:
        """Verify account before restoring its cursor or emitting account data."""
        user = self.client.user
        account_id = user.get("id") if user is not None else None
        if not isinstance(account_id, str) or not account_id:
            raise UpdateFailed("Bird Buddy returned no account identity")
        stored = self.config_entry.data.get(CONF_ACCOUNT_ID)
        if stored and stored != account_id:
            raise ConfigEntryAuthFailed(
                "Credentials refer to a different Bird Buddy account"
            )
        if not stored:
            email = user.get(CONF_EMAIL) if user is not None else None
            if (
                not isinstance(email, str)
                or email.lower() != self.config_entry.data[CONF_EMAIL].lower()
            ):
                raise ConfigEntryAuthFailed(
                    "Unable to verify the existing Bird Buddy account"
                )
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                data=self.config_entry.data | {CONF_ACCOUNT_ID: account_id},
            )
        self.account_id = account_id
        self.client.bind_account(account_id)

    def verify_account_identity(self) -> None:
        """Refuse reads/writes when authentication no longer matches this entry."""
        try:
            self._verify_identity()
        except (ConfigEntryAuthFailed, UpdateFailed):
            raise AuthenticationFailedError(
                "Bird Buddy account identity could not be verified"
            ) from None

    def _reconcile_feeders(self) -> None:
        for feeder_id, feeder in self.client.feeders.items():
            if feeder_id in self.feeders:
                self.feeders[feeder_id].clear()
                self.feeders[feeder_id].update(feeder)
            else:
                self.feeders[feeder_id] = BirdBuddyDevice(feeder)
            if feeder_id not in self.visitors:
                self.visitors[feeder_id] = RecentVisitors(
                    self.feeders[feeder_id], self.client, self.hass
                )
            else:
                self.visitors[feeder_id].feeder = self.feeders[feeder_id]
        for feeder_id in set(self.feeders).difference(self.client.feeders):
            del self.feeders[feeder_id]
            # Retain the subscribed visitor object so reappearance uses the same listeners.
            self.visitors[feeder_id].apply_visits([], replace=True)

    def add_visitor_listener(
        self, feeder: Feeder, listener: VisitorCallback
    ) -> CALLBACK_TYPE:
        if feeder.id not in self.visitors:
            self.visitors[feeder.id] = RecentVisitors(feeder, self.client, self.hass)
        return self.visitors[feeder.id].register_callback(listener)

    def _apply_nodes(self, nodes: Sequence[FeedNode]) -> None:
        self._apply_visits(
            [
                normalize_visit(node.data, known_feeder_ids=tuple(self.feeders))
                for node in nodes
                if node.data.get("__typename") in _VISITOR_KINDS
            ]
        )

    def _apply_visits(self, visits: Sequence[Visit]) -> None:
        for feeder_id, visitors in self.visitors.items():
            if feeder_id in self.feeders:
                visitors.apply_visits(visits)

    def _require_intake(self) -> DurableIntake:
        if self.intake is None or not self.intake.initialized:
            raise ServiceValidationError(
                "Postcard storage is not ready; check the Bird Buddy repair"
            )
        return self.intake

    def _record_feed_error(self, err: Exception) -> None:
        error_type = type(err).__name__
        if self.feed_error_type != error_type:
            LOGGER.warning("Bird Buddy postcard processing paused (%s)", error_type)
        self.feed_error_type = error_type
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            f"postcard_intake_{self.config_entry.entry_id}",
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="postcard_intake",
            translation_placeholders={"error_type": self.feed_error_type},
        )

    async def _accept_pending(
        self, *, backfill: bool = False, max_pages: int = 100
    ) -> int:
        self.verify_account_identity()
        intake = self._require_intake()
        mode = self._pending_intake_mode or (
            "bootstrap"
            if not intake.is_bootstrapped
            else "backfill"
            if backfill
            else "live"
        )
        # Empty bootstrap has no timestamp boundary. Traverse all subsequent pages
        # until one exists, keeping those arrivals live instead of suppressing them.
        scan_all = backfill or (
            intake.is_bootstrapped
            and self.client.export_feed_checkpoint()["watermark"] is None
        )
        batch = await self.client.retrieve_feed_batch(
            backfill=scan_all, max_pages=max_pages
        )
        self.verify_account_identity()
        self._pending_intake_mode = mode
        added = await intake.async_accept_batch(self.client, batch, mode=mode)
        self._pending_intake_mode = None
        self._apply_nodes(batch.nodes)
        return added

    async def _publish_intake_record(self, record: IntakeRecord) -> None:
        self.verify_account_identity()
        payload = record.payload
        if payload.get("__typename") not in _VISITOR_KINDS:
            return
        visit = normalize_visit(payload, known_feeder_ids=tuple(self.feeders))
        context: _EventContext = {
            "config_entry_id": self.config_entry.entry_id,
            "account_id": self.account_id,
            "event_id": record.event_id,
            "source": record.source,
        }
        if record.source == "backfill":
            self.hass.bus.async_fire(
                EVENT_POSTCARD_BACKFILL,
                build_postcard_event(visit, **context),
                EventOrigin.remote,
            )
            return
        # Construct both payloads before emitting either, avoiding partial events
        # when an identity cannot fit within recorder's byte limit.
        notification = build_legacy_feed_item_event(visit, **context)
        postcard = (
            build_postcard_event(visit, **context)
            if visit.kind == "FeedItemNewPostcard"
            else None
        )
        if postcard is not None:
            self.hass.bus.async_fire(EVENT_NEW_POSTCARD, postcard, EventOrigin.remote)
        self.hass.bus.async_fire(EVENT_NEW_FEED_ITEM, notification, EventOrigin.remote)

    async def _async_update_data(self) -> BirdBuddy:
        async with self.async_work():
            return await self._async_update_account()

    async def _async_update_account(self) -> BirdBuddy:
        try:
            await self.client.refresh()
            self._verify_identity()
            self._reconcile_feeders()
        except AuthenticationFailedError as err:
            raise ConfigEntryAuthFailed("Bird Buddy authentication expired") from err
        except (ConfigEntryAuthFailed, UpdateFailed):
            raise
        except Exception as err:
            raise UpdateFailed(
                f"Bird Buddy telemetry failed ({type(err).__name__})"
            ) from err
        self.last_update_timestamp = dt_util.utcnow()
        async with self._feed_work_lock:
            errors: list[Exception] = []
            try:
                if self.intake is None:
                    self.intake = DurableIntake(
                        self.hass, self.config_entry.entry_id, self.account_id
                    )
                if not self.intake.initialized:
                    await self.intake.async_initialize(self.client)
                await self._accept_pending()
                await self.intake.async_dispatch(self._publish_intake_record)
                if self.intake.dead_letter_count:
                    errors.append(IntakeError("Postcard events need explicit retry"))
            except AuthenticationFailedError as err:
                raise ConfigEntryAuthFailed(
                    "Bird Buddy authentication expired"
                ) from err
            except Exception as err:
                errors.append(err)
            # Refresh same-ID species/media/URL changes once per account, even if
            # intake has already accepted that item. Apply after pending snapshots
            # so an older persisted payload cannot replace fresher state.
            try:
                feed = await self.client.feed(first=100)
                self.verify_account_identity()
                self._apply_nodes(feed.validated_nodes())
            except AuthenticationFailedError as err:
                raise ConfigEntryAuthFailed(
                    "Bird Buddy authentication expired"
                ) from err
            except Exception as err:
                errors.append(err)
            if errors:
                self._record_feed_error(errors[0])
            else:
                self.feed_error_type = None
                self.last_feed_success = dt_util.utcnow()
                ir.async_delete_issue(
                    self.hass, DOMAIN, f"postcard_intake_{self.config_entry.entry_id}"
                )
        if self.hass.state is CoreState.running:
            check_legacy_listeners(self.hass)
        return self.client

    def validate_visit_target(
        self, actual_feeder_id: str | None, data: Mapping[str, Any]
    ) -> None:
        from .hass_util import (
            _feeder_id_for_device,
        )  # account resolver imports coordinator

        expected = data.get("feeder_id")
        if device_id := data.get("device_id"):
            device_feeder = _feeder_id_for_device(self.hass, device_id)
            if expected is not None and expected != device_feeder:
                raise ServiceValidationError("Selected device and feeder conflict")
            expected = device_feeder
        if expected is not None and expected != actual_feeder_id:
            raise ServiceValidationError(
                "Postcard does not identify the selected feeder"
            )

    async def _finish_failed_action(
        self, item_id: str, err: BaseException, *, compound: bool = False
    ) -> None:
        # Resolver errors may follow a committed side effect. A compound action
        # may have already identified before collection fails, so preserve doubt.
        # Authentication may fail during the SDK's read reconciliation after a
        # lost mutation response. Once intent is durable, auth failure alone
        # cannot prove that no write happened. Preflight auth failures occur
        # before async_begin_action and never enter this outcome handler.
        rejected = isinstance(err, HttpResponseError) and err.status in {
            400,
            401,
            403,
            404,
            405,
            413,
            415,
            422,
            429,
        }
        status = "failed" if rejected and not compound else "unknown"
        try:
            await self._require_intake().async_finish_action(
                item_id, status, error_type=type(err).__name__
            )
        except Exception as storage_error:
            self._record_feed_error(storage_error)
            # The durable in_progress entry becomes unknown on restart.

    async def handle_collect_postcard(
        self, request: PostcardCollectionRequest
    ) -> dict[str, Any]:
        async with self.async_work():
            return await self._async_collect_postcard(request)

    async def _async_collect_postcard(
        self, request: PostcardCollectionRequest
    ) -> dict[str, Any]:
        intake = self._require_intake()
        try:
            self.verify_account_identity()
            async with intake.postcard_lock(request.postcard_id):
                visit = await self.client.get_feed_item(request.postcard_id)
                self.verify_account_identity()
                self.validate_visit_target(visit.feeder_id, asdict(request))
                if visit.kind != "FeedItemNewPostcard":
                    raise ServiceValidationError("Only a new postcard can be collected")
                await intake.async_begin_action(
                    request.postcard_id,
                    {
                        "operation": "collect",
                        "share": request.share,
                        "identify": request.identify,
                    },
                )
                try:
                    result = await self.client.collect_postcard(
                        request.postcard_id,
                        share=request.share,
                        identify=request.identify,
                    )
                    self.verify_account_identity()
                    if result.receipt is None:
                        raise ValueError("Collection has no confirmed receipt")
                    receipt = asdict(result.receipt) | {
                        "collected_postcard_id": result.id
                    }
                    await intake.async_finish_action(
                        request.postcard_id, "succeeded", receipt
                    )
                except BaseException as err:
                    await self._finish_failed_action(
                        request.postcard_id, err, compound=request.identify
                    )
                    raise
                self._apply_visits(
                    [normalize_visit(result.data, known_feeder_ids=tuple(self.feeders))]
                )
            try:
                await self.client.refresh_collections(force=True)
            except AuthenticationFailedError:
                self.config_entry.async_start_reauth(self.hass)
            except Exception as err:
                LOGGER.debug(
                    "Collection saved; browser refresh deferred (%s)",
                    type(err).__name__,
                )
            return receipt
        except AuthenticationFailedError as err:
            self.config_entry.async_start_reauth(self.hass)
            raise HomeAssistantError("Bird Buddy authentication expired") from err
        except IntakeError as err:
            raise ServiceValidationError(str(err)) from err
        except ServiceValidationError:
            raise
        except Exception as err:
            raise HomeAssistantError(
                f"Postcard action failed or needs review ({type(err).__name__})"
            ) from err

    async def handle_identify_postcard(self, data: Mapping[str, Any]) -> dict[str, Any]:
        async with self.async_work():
            return await self._async_identify_postcard(data)

    async def _async_identify_postcard(self, data: Mapping[str, Any]) -> dict[str, Any]:
        intake = self._require_intake()
        item_id = data["postcard_id"]
        try:
            self.verify_account_identity()
            async with intake.postcard_lock(item_id):
                visit = await self.client.get_feed_item(item_id)
                self.verify_account_identity()
                self.validate_visit_target(visit.feeder_id, data)
                if visit.kind != "FeedItemNewPostcard":
                    raise ServiceValidationError(
                        "Only a new postcard can be identified"
                    )
                await intake.async_begin_action(item_id, {"operation": "identify"})
                try:
                    result = await self.client.identify_postcard(item_id)
                    self.verify_account_identity()
                    await intake.async_finish_action(
                        item_id, "succeeded", {"postcard_id": item_id}
                    )
                except BaseException as err:
                    await self._finish_failed_action(item_id, err)
                    raise
                identified = normalize_visit(
                    result.data, known_feeder_ids=tuple(self.feeders)
                )
                self._apply_visits([identified])
                return postcard_details(
                    identified,
                    config_entry_id=self.config_entry.entry_id,
                    account_id=self.account_id,
                )
        except AuthenticationFailedError as err:
            self.config_entry.async_start_reauth(self.hass)
            raise HomeAssistantError("Bird Buddy authentication expired") from err
        except IntakeError as err:
            raise ServiceValidationError(str(err)) from err
        except ServiceValidationError:
            raise
        except Exception as err:
            raise HomeAssistantError(
                f"Postcard identification needs review ({type(err).__name__})"
            ) from err

    async def async_backfill(self, *, max_pages: int = 10) -> dict[str, Any]:
        async with self.async_work():
            return await self._async_backfill(max_pages=max_pages)

    async def _async_backfill(self, *, max_pages: int) -> dict[str, Any]:
        async with self._feed_work_lock:
            intake = self._require_intake()
            # Persist any previous live/backfill batch with its original source.
            if self._pending_intake_mode or not intake.is_bootstrapped:
                await self._accept_pending()
            queued = await self._accept_pending(backfill=True, max_pages=max_pages)
            delivered = await intake.async_dispatch(self._publish_intake_record)
            return {
                "queued": queued,
                "delivered": delivered,
                "pending": intake.pending_count,
            }

    async def async_retry_feed_items(self, item_id: str | None) -> int:
        """Own recovery writes through unload; never repeat cloud actions."""
        async with self.async_work():
            self.verify_account_identity()
            retried = await self._require_intake().async_retry_failed(item_id)
            await self.async_request_refresh()
            return retried

    async def async_acknowledge_postcard_retry(self, item_id: str) -> None:
        """Record the explicit retry decision within the current owner lifetime."""
        async with self.async_work():
            self.verify_account_identity()
            intake = self._require_intake()
            async with intake.postcard_lock(item_id):
                await intake.async_acknowledge_action_retry(item_id)
