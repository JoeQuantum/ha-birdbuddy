# 0.2.0 core migration candidate

This development candidate moves the fork to Bird Buddy's current postcard API and
keeps its five-image carousel, visitor history, unidentified images, polling option,
Last Sync sensor and existing config-entry/device/entity identifiers.

## Automation migration

New postcard delivery uses `birdbuddy_new_postcard` schema version 1. Events include
account/config-entry/postcard/feeder identity, species, selected photo/video media,
a representative preview, a stable event ID and source (`live` or historical).
Large media lists are explicitly compacted to stay within recorder limits;
`birdbuddy.get_postcard_details` returns the complete selected data.

`birdbuddy.collect_postcard` now requires an explicit `selection_policy: server`.
Identification and sharing are separate boolean choices, both false by default.
Legacy strategy/confidence/report-token inputs are rejected with a Repair because
their semantics have not been established for the new API. Equivalent `postcard.id`
and `share_media` aliases remain temporarily available, with removal planned for
0.4.0. The old `birdbuddy_new_postcard_sighting` event is not fabricated. Replace old
blueprint instances using [the migration guide](automation-migration.md).

The notification event `birdbuddy_new_feed_item` retains its compatibility fields
and gains the account-scoped versioned data. Device-trigger type `new_postcard`
remains stable and filters by account, feeder and live source. The replacement
blueprint uses a finite queue and requires the user to choose the server policy.

## Reliability and account handling

- Feed pagination is bounded and validates cursors/timestamps. Checkpoints advance
  only after queue state is durably stored and read back from disk.
- Initial history is suppressed once. Startup arrivals are retained until Home
  Assistant is running; restarts restore accepted state. Backfill has a separate
  event and cannot activate the replacement collection blueprint.
- Per-postcard action records precede cloud mutations. Uncertain outcomes block
  automatic replay, including a lost response followed by failed authentication.
- Unload drains owned account work and pending storage writes before a replacement
  coordinator can open the same store. Later writes through a closed owner fail.
- Reauthentication verifies the vendor account UUID while preserving the existing
  email-based entry unique ID. Ambiguous shared-feeder targeting is rejected.
- Empty accounts remain valid. Feeder additions, removals and role changes update
  runtime availability and add eligible entities without deleting registry history.
- Visitor species, media and timestamps share one canonical visit. Independent
  image caches refresh signed URLs without invoking inference or collection.
- Collection browsing is account scoped and bounded by a shared cache. Unsupported
  media remains unsupported rather than being misrepresented as an image.
- Controls use bounded server-result checks, truthful unknown/completed states and
  owner/capability checks. Firmware monitoring is cancellable and clears local busy
  state after failure while retaining the last server progress.
- Missing telemetry remains unknown; zero battery/temperature and false charging
  are preserved when actually returned.

## Diagnostics and developer support

The candidate includes bounded allowlist diagnostics, installation/restore guidance,
private durable storage, package integrity checks and a tested local archive recipe.
The core client is pinned to immutable revision
`593bf8df89ef04291a79004df9bfd89c30d1889d`. Home Assistant compatibility lanes are
2025.1.4, 2026.7.1 and 2026.11.1, with exactly matched test harnesses. See
[release validation](release-validation.md) for final results and artifact provenance.

## Limits of this candidate

This is a local/offline-validated development candidate. No authenticated account
canary, live cloud collection/sharing, firmware installation or physical hardware
compatibility is claimed. A release decision must retain those limits. Heartbeat
push, camera sessions, hardware-specific camera modes and Smart Energy writes stay
behind their documented evidence gates. See [API capabilities](api-capabilities.md).

Back up configuration and `.storage` before installing. Preserve the new intake
store across a code rollback; reverting code does not undo cloud actions or repair
obsolete vendor APIs. See [restore guidance](diagnostics-and-restore.md).
