"""Durable, bounded feed intake and explicit mutation outcome records."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from birdbuddy.exceptions import AuthenticationFailedError
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.json import json_bytes
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

if TYPE_CHECKING:
    from birdbuddy.client import BirdBuddy
    from birdbuddy.feed import FeedBatch


class IntakeError(ValueError):
    """Durable intake could not safely advance."""


class IntakePersistenceError(IntakeError):
    """The proposed intake state was not verified on disk."""


class IntakeCapacityError(IntakeError):
    """A documented ledger limit was reached without dropping pending work."""


class IntakePermanentError(IntakeError):
    """One item requires manual repair rather than automatic retries."""


class ActionAlreadyRecordedError(IntakeError):
    """A mutation cannot be retried without a separate explicit decision."""

    def __init__(self, status: str) -> None:
        self.status = status
        super().__init__(f"Postcard action is {status}; reconcile before retrying")


@dataclass(frozen=True)
class IntakeRecord:
    """One delivery with a stable identity and a defensive payload copy."""

    event_id: str
    item_id: str
    source: str
    attempts: int
    _payload: str = field(repr=False)

    @property
    def payload(self) -> dict[str, Any]:
        return json.loads(self._payload)


_ACTION_ID_FIELDS = {"postcard_id", "requested_postcard_id", "collected_postcard_id"}
_ACTION_FLAG_FIELDS = {
    "is_first_collected_postcard",
    "reconciled_by_read",
    "share_requested",
    "sharing_confirmed",
}


def _validate_action_result(result: Any) -> None:
    if not isinstance(result, dict) or set(result).difference(
        _ACTION_ID_FIELDS | _ACTION_FLAG_FIELDS
    ):
        raise IntakePersistenceError(
            "Action results must contain only safe IDs and receipt flags"
        )
    for key, value in result.items():
        if value is None:
            continue
        if key in _ACTION_ID_FIELDS and (
            not isinstance(value, str) or not value or len(value) > 512
        ):
            raise IntakePersistenceError("Invalid postcard receipt ID")
        if key in _ACTION_FLAG_FIELDS and type(value) is not bool:
            raise IntakePersistenceError("Invalid postcard receipt flag")


def _read_envelope(path: str, key: str, max_bytes: int) -> dict[str, Any] | None:
    """Read the actual file, avoiding Store's pending/cache views and resets."""
    try:
        with Path(path).open("rb") as handle:
            raw = handle.read(max_bytes + 1)
    except FileNotFoundError:
        return None
    if len(raw) > max_bytes:
        raise IntakeCapacityError("Stored intake exceeds the byte limit")
    try:
        envelope = json.loads(raw)
    except (ValueError, UnicodeError) as err:
        raise IntakePersistenceError(
            "Intake storage is corrupt; restore or repair it"
        ) from err
    if (
        not isinstance(envelope, dict)
        or envelope.get("version") != 1
        or envelope.get("key") != key
        or not isinstance(envelope.get("data"), dict)
        or not isinstance(envelope["data"].get("revision"), str)
        or not envelope["data"]["revision"]
    ):
        raise IntakePersistenceError("Unsupported intake storage envelope")
    return envelope["data"]


def _safe_payload(value: Any) -> Any:
    """Keep selected read data, stripping authentication and report-token fields."""
    if isinstance(value, Mapping):
        return {
            key: _safe_payload(child)
            for key, child in value.items()
            if isinstance(key, str)
            and not key.lower().endswith(("token", "tokens"))
            and key.lower() not in {"password", "authorization", "credentials"}
        }
    if isinstance(value, (list, tuple)):
        return [_safe_payload(child) for child in value]
    return value


def _timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise IntakePersistenceError("Invalid intake timestamp")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as err:
        raise IntakePersistenceError("Invalid intake timestamp") from err
    if result.tzinfo is None:
        raise IntakePersistenceError("Intake timestamps require a timezone")
    return result.astimezone(timezone.utc)


async def async_drain_task(task: asyncio.Task[Any]) -> Any:
    """Propagate cancellation only after protected work has really finished.

    In particular, cancelling an executor-backed Store write cannot stop its
    thread. Letting the caller exit early could let a reloaded owner overwrite
    that file before the old thread completes its stale write.
    """
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    if cancelled:
        # Retrieve any exception before propagating caller cancellation.
        if not task.cancelled():
            task.exception()
        raise asyncio.CancelledError
    return task.result()


class DurableIntake:
    """Persist queue and proposed checkpoint before acknowledging any batch.

    Completion after bus delivery is deliberately at-least-once: a crash in
    between can replay the stable event_id. No method invokes a cloud mutation.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        account_id: str,
        *,
        max_pending: int = 1000,
        max_processed: int = 10000,
        max_actions: int = 1000,
        max_bytes: int = 10_000_000,
        retention_days: int = 30,
        max_attempts: int = 5,
        clock: Callable[[], datetime] = dt_util.utcnow,
    ) -> None:
        if not entry_id or any(c in entry_id for c in "/\\") or not account_id:
            raise IntakeError("Valid entry and account IDs are required")
        limits = (
            max_pending,
            max_processed,
            max_actions,
            max_bytes,
            retention_days,
            max_attempts,
        )
        if any(type(limit) is not int or limit < 1 for limit in limits):
            raise IntakeError("Intake limits must be positive integers")
        self.hass = hass
        self.entry_id = entry_id
        self.account_id = account_id
        self._max_pending = max_pending
        self._max_processed = max_processed
        self._max_actions = max_actions
        self._max_bytes = max_bytes
        self._retention = timedelta(days=retention_days)
        self._max_attempts = max_attempts
        self._clock = clock
        self._store: Store[dict[str, Any]] = Store(
            hass, 1, f"birdbuddy.{entry_id}.intake", private=True, atomic_writes=True
        )
        self._state: dict[str, Any] | None = None
        self._closed = False
        self._lock = asyncio.Lock()
        self._dispatch_lock = asyncio.Lock()
        self._item_locks: dict[str, tuple[asyncio.Lock, int]] = {}

    @property
    def initialized(self) -> bool:
        return self._state is not None

    @property
    def is_bootstrapped(self) -> bool:
        return bool(self._state and self._state["bootstrapped"])

    @property
    def pending_count(self) -> int:
        return sum(
            record["status"] == "pending"
            for record in (self._state or {}).get("pending", {}).values()
        )

    @property
    def dead_letter_count(self) -> int:
        return sum(
            record["status"] == "dead_letter"
            for record in (self._state or {}).get("pending", {}).values()
        )

    @property
    def unknown_action_count(self) -> int:
        return sum(
            record["status"] == "unknown"
            for record in (self._state or {}).get("actions", {}).values()
        )

    def _current(self) -> dict[str, Any]:
        if self._state is None:
            raise IntakeError("Initialize intake before polling or dispatching")
        return deepcopy(self._state)

    def _event_id(self, item_id: str) -> str:
        encoded = json_bytes([self.account_id, item_id])
        return "birdbuddy:" + sha256(encoded).hexdigest()

    def _validate(self, state: dict[str, Any]) -> None:
        if (
            state.get("version") != 1
            or state.get("account_id") != self.account_id
            or state.get("entry_id") != self.entry_id
            or type(state.get("bootstrapped")) is not bool
            or not isinstance(state.get("checkpoint"), dict)
            or set(state).difference(
                {
                    "version",
                    "entry_id",
                    "account_id",
                    "bootstrapped",
                    "checkpoint",
                    "pending",
                    "processed",
                    "actions",
                    "revision",
                }
            )
        ):
            raise IntakePersistenceError(
                "Intake version or account identity does not match"
            )
        for key, limit in (
            ("pending", self._max_pending),
            ("processed", self._max_processed),
            ("actions", self._max_actions),
        ):
            records = state.get(key)
            if not isinstance(records, dict):
                raise IntakePersistenceError("Invalid intake record collection")
            if len(records) > limit:
                raise IntakeCapacityError(
                    f"Intake {key} limit exceeded; pending work retained"
                )
        envelope = {
            "version": 1,
            "minor_version": 1,
            "key": self._store.key,
            "data": state,
        }
        # HA Store pretty-prints its envelope. Bound that representation before
        # writing, not only the smaller compact encoding used for event data.
        if (
            len(json.dumps(envelope, indent=2, ensure_ascii=False).encode())
            > self._max_bytes
        ):
            raise IntakeCapacityError(
                "Intake byte limit exceeded; pending work retained"
            )
        for event_id, record in state["pending"].items():
            if (
                not isinstance(record, dict)
                or not isinstance(record.get("item_id"), str)
                or not record["item_id"]
                or event_id != self._event_id(record["item_id"])
                or not isinstance(record.get("payload"), dict)
                or record["payload"].get("id") != record["item_id"]
                or record.get("status") not in {"pending", "dead_letter"}
                or record.get("source") not in {"live", "backfill"}
                or type(record.get("attempts")) is not int
                or record["attempts"] < 0
                or set(record).difference(
                    {
                        "item_id",
                        "payload",
                        "source",
                        "status",
                        "attempts",
                        "first_seen",
                        "next_attempt",
                        "error_type",
                    }
                )
            ):
                raise IntakePersistenceError("Invalid pending intake record")
            if _safe_payload(record["payload"]) != record["payload"]:
                raise IntakePersistenceError(
                    "Pending payload contains prohibited token fields"
                )
            _timestamp(record.get("first_seen"))
            _timestamp(record.get("next_attempt"))
        for event_id, record in state["processed"].items():
            if not isinstance(record, dict) or record.get("status") not in {
                "delivered",
                "bootstrap",
            }:
                raise IntakePersistenceError("Invalid processed intake record")
            if set(record) != {"item_id", "status", "processed_at"}:
                raise IntakePersistenceError("Unexpected processed intake fields")
            if (
                not isinstance(record.get("item_id"), str)
                or not record["item_id"]
                or event_id != self._event_id(record["item_id"])
            ):
                raise IntakePersistenceError("Processed intake identity does not match")
            _timestamp(record.get("processed_at"))
        for item_id, record in state["actions"].items():
            if (
                not isinstance(item_id, str)
                or not isinstance(record, dict)
                or record.get("status")
                not in {
                    "in_progress",
                    "succeeded",
                    "failed",
                    "unknown",
                    "retry_allowed",
                }
            ):
                raise IntakePersistenceError("Invalid postcard action record")
            if set(record) != {
                "action_id",
                "intent",
                "status",
                "updated_at",
                "result",
                "error_type",
            }:
                raise IntakePersistenceError("Unexpected postcard action fields")
            intent = record.get("intent")
            if (
                not isinstance(intent, dict)
                or intent.get("operation") not in {"identify", "collect"}
                or set(intent).difference({"operation", "identify", "share"})
                or any(
                    type(intent[key]) is not bool
                    for key in ("identify", "share")
                    if key in intent
                )
            ):
                raise IntakePersistenceError("Invalid persisted postcard action intent")
            _validate_action_result(record.get("result"))
            _timestamp(record.get("updated_at"))
        for record in (*state["pending"].values(), *state["actions"].values()):
            error_type = record.get("error_type")
            if error_type is not None and (
                not isinstance(error_type, str)
                or not error_type.isidentifier()
                or len(error_type) > 100
            ):
                raise IntakePersistenceError("Only an exception type may be stored")

    def _prune(self, state: dict[str, Any]) -> None:
        cutoff = self._clock() - self._retention
        state["processed"] = {
            key: value
            for key, value in state["processed"].items()
            if _timestamp(value["processed_at"]) >= cutoff
        }
        state["actions"] = {
            key: value
            for key, value in state["actions"].items()
            if value["status"] not in {"succeeded", "failed"}
            or _timestamp(value["updated_at"]) >= cutoff
        }

    async def _save(self, state: dict[str, Any]) -> None:
        if self._closed:
            raise IntakePersistenceError("Postcard storage owner is closed")
        self._prune(state)
        state["revision"] = uuid4().hex
        self._validate(state)
        # Keep the caller's lock until the real write and readback finish, even
        # if unload cancels the caller. Never abandon an executor write.
        await async_drain_task(asyncio.create_task(self._persist(state)))

    async def _persist(self, state: dict[str, Any]) -> None:
        """Complete one persistence transaction before releasing its owner."""
        if self.hass.state in {
            CoreState.stopping,
            CoreState.final_write,
            CoreState.stopped,
        }:
            raise IntakePersistenceError(
                "Home Assistant is stopping; intake was not accepted"
            )
        await self._store.async_save(state)
        # Store swallows WriteError and may expose an in-memory pending value
        # through async_load. Only the actual persisted revision proves intake.
        actual = await self.hass.async_add_executor_job(
            _read_envelope, self._store.path, self._store.key, self._max_bytes
        )
        if actual != state:
            raise IntakePersistenceError(
                "Intake write was not verified; batch remains unacknowledged"
            )
        self._state = deepcopy(state)

    async def async_close(self) -> None:
        """Reject late writes and wait until the last transaction has finished."""
        self._closed = True
        async with self._lock:
            pass

    async def async_initialize(self, client: BirdBuddy) -> None:
        """Validate durable state and restore its checkpoint before first retrieval."""
        async with self._lock:
            if self._closed:
                raise IntakePersistenceError("Postcard storage owner is closed")
            if self.initialized:
                return
            state = await self.hass.async_add_executor_job(
                _read_envelope, self._store.path, self._store.key, self._max_bytes
            )
            if state is None:
                state = {
                    "version": 1,
                    "entry_id": self.entry_id,
                    "account_id": self.account_id,
                    "bootstrapped": False,
                    "checkpoint": client.export_feed_checkpoint(),
                    "pending": {},
                    "processed": {},
                    "actions": {},
                }
            self._validate(state)
            client.import_feed_checkpoint(state["checkpoint"])
            interrupted = False
            for record in state["actions"].values():
                if record["status"] == "in_progress":
                    record["status"] = "unknown"
                    record["error_type"] = "InterruptedAction"
                    record["updated_at"] = self._clock().isoformat()
                    interrupted = True
            if interrupted:
                await self._save(state)
            self._state = deepcopy(state)

    async def async_accept_batch(
        self, client: BirdBuddy, batch: FeedBatch, *, mode: str = "live"
    ) -> int:
        """Save all queued work and proposed cursor together, then acknowledge."""
        if mode not in {"bootstrap", "live", "backfill"}:
            raise IntakeError("Unknown intake mode")
        async with self._lock:
            state = self._current()
            if mode == "bootstrap" and state["bootstrapped"]:
                raise IntakeError(
                    "An established intake cannot suppress a second bootstrap"
                )
            if mode != "bootstrap" and not state["bootstrapped"]:
                raise IntakeError("Establish the initial history boundary first")
            now = self._clock().isoformat()
            self._prune(state)
            added = 0
            for node in batch.nodes:
                item_id = node.node_id
                if not isinstance(item_id, str) or not item_id:
                    raise IntakeError("Feed item has no stable identity")
                event_id = self._event_id(item_id)
                if event_id in state["pending"] or event_id in state["processed"]:
                    continue
                if mode == "bootstrap":
                    state["processed"][event_id] = {
                        "item_id": item_id,
                        "status": "bootstrap",
                        "processed_at": now,
                    }
                else:
                    state["pending"][event_id] = {
                        "item_id": item_id,
                        "payload": _safe_payload(node.data),
                        "source": mode,
                        "status": "pending",
                        "attempts": 0,
                        "first_seen": now,
                        "next_attempt": now,
                        "error_type": None,
                    }
                    added += 1
            state["checkpoint"] = batch.checkpoint.to_dict()
            state["bootstrapped"] = True
            await self._save(state)
            client.ack_feed_batch(batch.batch_id)
            return added

    @asynccontextmanager
    async def postcard_lock(self, item_id: str) -> AsyncIterator[None]:
        """Serialize event work and explicit actions per postcard, including waiters."""
        if not isinstance(item_id, str) or not item_id:
            raise IntakeError("Postcard ID is required")
        lock, users = self._item_locks.get(item_id, (asyncio.Lock(), 0))
        self._item_locks[item_id] = (lock, users + 1)
        try:
            async with lock:
                yield
        finally:
            _, users = self._item_locks[item_id]
            if users == 1:
                self._item_locks.pop(item_id)
            else:
                self._item_locks[item_id] = (lock, users - 1)

    async def async_dispatch(
        self,
        callback: Callable[[IntakeRecord], Awaitable[None]],
        *,
        max_items: int = 100,
    ) -> int:
        """Deliver due work after HA startup, retaining failures independently."""
        if type(max_items) is not int or max_items < 1:
            raise IntakeError("Dispatch limit must be positive")
        # HA's is_running includes CoreState.starting, when automations may
        # still be attaching. The delivery boundary must be fully running.
        if self.hass.state is not CoreState.running:
            return 0
        delivered = 0
        async with self._dispatch_lock:
            state = self._current()
            due = [
                event_id
                for event_id, record in state["pending"].items()
                if record["status"] == "pending"
                and _timestamp(record["next_attempt"]) <= self._clock()
            ][:max_items]
            for event_id in due:
                if self.hass.state is not CoreState.running:
                    break
                record = self._current()["pending"].get(event_id)
                if record is None:
                    continue
                async with self.postcard_lock(record["item_id"]):
                    # Reserve completion capacity before publishing externally;
                    # otherwise a full processed ledger could cause repeats on
                    # every poll without any possibility of recording success.
                    preview = self._current()
                    self._prune(preview)
                    if len(preview["processed"]) >= self._max_processed:
                        raise IntakeCapacityError(
                            "Processed intake limit reached before delivery"
                        )
                    work = IntakeRecord(
                        event_id,
                        record["item_id"],
                        record["source"],
                        record["attempts"],
                        json.dumps(record["payload"]),
                    )
                    try:
                        await callback(work)
                    except (AuthenticationFailedError, ConfigEntryAuthFailed):
                        raise
                    except Exception as err:
                        async with self._lock:
                            updated = self._current()
                            failed = updated["pending"][event_id]
                            failed["attempts"] += 1
                            failed["error_type"] = type(err).__name__
                            if (
                                isinstance(err, IntakePermanentError)
                                or failed["attempts"] >= self._max_attempts
                            ):
                                failed["status"] = "dead_letter"
                            failed["next_attempt"] = (
                                self._clock()
                                + timedelta(
                                    seconds=min(
                                        3600, 30 * 2 ** (failed["attempts"] - 1)
                                    )
                                )
                            ).isoformat()
                            await self._save(updated)
                        continue
                    async with self._lock:
                        updated = self._current()
                        updated["pending"].pop(event_id)
                        updated["processed"][event_id] = {
                            "item_id": record["item_id"],
                            "status": "delivered",
                            "processed_at": self._clock().isoformat(),
                        }
                        await self._save(updated)
                        delivered += 1
        return delivered

    async def async_retry_failed(self, item_id: str | None = None) -> int:
        """Explicitly retry event processing; this never retries cloud mutations."""
        async with self._lock:
            state = self._current()
            retried = 0
            for record in state["pending"].values():
                if record["attempts"] and (
                    item_id is None or record["item_id"] == item_id
                ):
                    record.update(
                        status="pending",
                        attempts=0,
                        error_type=None,
                        next_attempt=self._clock().isoformat(),
                    )
                    retried += 1
            if retried:
                await self._save(state)
            return retried

    async def async_begin_action(
        self, item_id: str, intent: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Persist before inference/collection; caller must hold postcard_lock."""
        if (
            not isinstance(item_id, str)
            or not item_id
            or intent.get("operation") not in {"identify", "collect"}
            or set(intent).difference({"operation", "identify", "share"})
        ):
            raise IntakeError(
                "Action intent must identify the operation and boolean options"
            )
        if any(
            type(intent[key]) is not bool
            for key in ("identify", "share")
            if key in intent
        ):
            raise IntakeError("Action options must be boolean")
        async with self._lock:
            state = self._current()
            self._prune(state)
            previous = state["actions"].get(item_id)
            identified_then_collect = (
                previous
                and previous["status"] == "succeeded"
                and previous["intent"].get("operation") == "identify"
                and intent["operation"] == "collect"
            )
            if (
                previous
                and previous["status"] not in {"failed", "retry_allowed"}
                and not identified_then_collect
            ):
                raise ActionAlreadyRecordedError(previous["status"])
            record = {
                "action_id": uuid4().hex,
                "intent": dict(intent),
                "status": "in_progress",
                "updated_at": self._clock().isoformat(),
                "result": {},
                "error_type": None,
            }
            state["actions"][item_id] = record
            await self._save(state)
            return deepcopy(record)

    async def async_finish_action(
        self,
        item_id: str,
        status: str,
        result: Mapping[str, Any] | None = None,
        *,
        error_type: str | None = None,
    ) -> None:
        """Record safe result identifiers; uncertain outcomes block automatic retry."""
        if status not in {"succeeded", "failed", "unknown"}:
            raise IntakeError("Unknown action outcome")
        safe = dict(result or {})
        _validate_action_result(safe)
        async with self._lock:
            state = self._current()
            record = state["actions"].get(item_id)
            reconciled_unknown = (
                record
                and record["status"] == "unknown"
                and status == "succeeded"
                and safe.get("reconciled_by_read") is True
                and safe.get("requested_postcard_id") == item_id
                and safe.get("collected_postcard_id") == item_id
            )
            if not record or (
                record["status"] != "in_progress" and not reconciled_unknown
            ):
                raise IntakeError("No in-progress postcard action matches the result")
            record.update(
                status=status,
                result=safe,
                error_type=error_type,
                updated_at=self._clock().isoformat(),
            )
            await self._save(state)

    async def async_acknowledge_action_retry(self, item_id: str) -> None:
        """Separate explicit user decision permitting retry of an unknown outcome."""
        async with self._lock:
            state = self._current()
            record = state["actions"].get(item_id)
            if not record or record["status"] != "unknown":
                raise IntakeError(
                    "Only an unknown outcome can be acknowledged for retry"
                )
            record.update(status="retry_allowed", updated_at=self._clock().isoformat())
            await self._save(state)
