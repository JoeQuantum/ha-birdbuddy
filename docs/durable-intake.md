# Durable postcard intake

The per-account intake file records pending event work, completed identities,
the accepted client checkpoint, and explicit mutation outcomes. It uses HA
`Store` with private permissions and atomic writes at
`.storage/birdbuddy.<config_entry_id>.intake`.

An awaited `Store.async_save` alone is insufficient evidence of persistence:
supported HA versions log and swallow some write errors, and may defer writes
during shutdown. Intake therefore assigns a new revision, saves the complete
queue/checkpoint transaction, and reads the actual file back before advancing
the client's watermark. It does not verify through HA's pending-data/cache
view. A missing or different revision leaves the batch unacknowledged and
recoverable. Corrupt, oversized, future-version, or different-account files
raise a visible error; they are not erased or silently replaced by a new
bootstrap. These guarantees cover the tested file-write boundaries, not
storage hardware that falsely reports a successful write.

## Startup and polling

1. Verify the authenticated account UUID, construct `DurableIntake`, and call
   `async_initialize(client)` before retrieving a feed batch. This restores
   the persisted checkpoint and converts interrupted actions to `unknown`.
2. When `is_bootstrapped` is false, fetch the initial latest-20 baseline and
   pass it to `async_accept_batch(..., mode="bootstrap")`. Those IDs are
   recorded as historical, without events or collection.
3. Later batches use `mode="live"`. The queue accepts arrivals during startup,
   including the gap between setup and the first scheduled poll. Dispatch
   requires `hass.state is CoreState.running`; HA's `is_running` also includes
   the earlier `starting` state, when automation listeners may be absent.
4. If the initial feed is empty, the accepted watermark remains null. Once
   bootstrapped, retrieve with `backfill=True` and a finite page limit until a
   real dated boundary exists. Otherwise the client's first-page limit could
   hide a burst of more than 20 arrivals. Enqueue these as live observations,
   not a second suppressed bootstrap.

One accepted `FeedBatch` supplies the queue's selected read data and the
proposed checkpoint together. Acceptance never fetches another feed. Display
refreshes can separately reread an existing item whose metadata changed;
those reads do not advance delivery identity or invoke inference.

## Delivery, retries and limits

The callback receives an immutable `IntakeRecord` with `event_id`, `item_id`,
`source`, `attempts`, and a defensive-copy `payload`. `event_id` is a SHA-256
identity derived from the account UUID and feed-item ID. Pass it and `source`
to the canonical and compatibility event builders before size compaction.
Do not use an expiring media URL as a delivery identity.

Delivery is **at least once**. A crash after the event bus accepts an event
but before completion is persisted can deliver it again after restart, with
the same `event_id`. External automation effects are not transactionally
coupled to the HA event bus. Consumers requiring deduplication must retain
those IDs themselves; the integration does not claim exactly-once delivery.

Per-item failures are persisted independently and do not poison subsequent
items. Retry delays start at 30 seconds and double, capped at one hour. After
five failed attempts, or an explicit `IntakePermanentError`, a record becomes
a dead letter. Authentication errors propagate to HA recovery and remain
pending. Cancellation leaves unfinished work pending. `async_retry_failed`
explicitly resets event-processing failures; it does not retry cloud mutations.

Default limits are 1,000 pending/dead-letter records, 10,000 processed records,
1,000 action records, and 10,000,000 bytes for the pretty-printed storage
envelope. Capacity failures occur before acknowledging intake; processed
capacity is also checked before publishing an event. Pending/dead-letter
records and unknown outcomes are never silently evicted. Processed records
and completed/definitively failed action records have a documented 30-day
retention. Once that retention expires, a deliberately requested historical
backfill can surface the item again. Shortening a limit below stored contents
requires explicit repair, not automatic deletion.

`pending_count`, `dead_letter_count`, and `unknown_action_count` expose status
without including payloads. Stored errors contain the exception class only,
never exception messages. Authentication/report-token keys are stripped from
queued read data. Selected signed media URLs remain in the private bounded
queue so pending events can be rebuilt; details can refresh those URLs.

## Backfill and explicit actions

Backfill is an explicitly requested, bounded client retrieval, then
`async_accept_batch(..., mode="backfill")`. Cancellation before acceptance
does not acknowledge the client's batch. After acceptance, queued work is
durable and can be delivered after restart. A backfill source is passed into
the event contract and should use the separate historical replay event path;
default collection automations must not treat it as a live arrival. Intake
and replay never identify, collect, or share a postcard.

For an explicit cloud action, hold `postcard_lock(item_id)` and persist
`async_begin_action(item_id, intent)` before calling the client. Intent contains
`operation: identify|collect` and optional boolean `identify`/`share` values.
Record `succeeded`, `failed` only for a definitive no-effect failure, or
`unknown` with `async_finish_action`. Results accept only receipt IDs and
boolean flags; `error_type` may contain an exception class name.

A recorded successful collection is not automatically repeated during its
retention, and an in-progress/unknown action blocks another mutation. A
successful identification can be followed by collection. Restart converts an
interrupted in-progress action to unknown before any operation can resume.
Read-only reconciliation may mark an unknown action successful only with an
explicit collected result for the same original ID. A changed ID or absence
cannot establish success. The separate `async_acknowledge_action_retry` method
records an explicit user decision permitting another attempt; ordinary
collection does not call it.

The tests use real temporary files rather than the HA harness's in-memory
storage mock. They exercise write errors swallowed by HA, shutdown deferral,
crashes on both sides of acknowledgement/delivery, startup, restart, bounded
backfill, cancellation, retry isolation, retention, corrupted storage,
account mismatch, per-postcard concurrency, and interrupted action outcomes.
