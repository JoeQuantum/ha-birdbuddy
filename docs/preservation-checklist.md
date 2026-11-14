# Fork preservation checklist

BB-01 implementation baseline: fork `fbb00c22b6971c430c6d0b8d76f13380974331a3` (`0.1.11`), verified 2026-11-13. Its runtime is identical to reviewed `07c64a12ed331e3aa20f5e6553e6077dbb86e330`; the newer commit only enables manual runs of three workflows. This records compatibility contracts and intended behavior to preserve while completing the development backlog. Known defects below are correction targets, not invariants.

## Baseline evidence

- Published upgrade baseline: [v0.1.11](https://github.com/JoeQuantum/ha-birdbuddy/releases/tag/v0.1.11), published 2026-11-13 at 18:26:53 UTC. The release has no separate binary assets; retain its immutable source tag for package and rollback comparisons.
- Integration client pin: `JoeQuantum/pybirdbuddy@v0.0.25`, commit `3e92c3faf0ea73c525fe9acde9052b57b05b9854`. Both implementation checkouts start clean on `codex/birdbuddy-modernize`.
- Manual `workflow_dispatch` already exists for HACS, hassfest and Python tests. The Python PR trigger and the modern test/tooling matrix remain BB-02 work.

- The existing suite passed **84 tests**, with **77% statement coverage**, using Python **3.12.13**, Home Assistant **2025.1.4**, `pytest-homeassistant-custom-component` **0.13.205**, pytest **8.3.4**, and the JoeQuantum `pybirdbuddy` **0.0.25** dependency. This is previously recorded review evidence, not a fresh run for this document.
- Coverage was **0% for `media_source.py`** and **47% for `update.py`**. Passing counts do not establish media-browser or firmware-monitor reliability.
- API calls were mocked. The results establish neither live authentication/device behavior nor compatibility with the declared HACS minimum, HA `2024.4.0`.
- The baseline tests are predominantly synthetic dictionaries assembled in test modules. `tests/` has no captured-payload fixture directory. The current API research includes separate sanitized public payloads and schema validation; these are evidence to integrate deliberately, not a substitute for boundary tests.
- No applicable on-disk `AGENTS.md` was found in the inspected baseline checkout or checked ancestors. Standing user instructions still apply. This document does not authorize changes to credentials, accounts or devices.

## Identifiers that must survive upgrades

Let `F` be the vendor feeder UUID and `E` the existing Home Assistant config-entry ID. Never derive stable IDs from display names, current array position, email changes or signed media URLs.

| Surface | Baseline contract | Migration guard |
| --- | --- | --- |
| Integration | Domain/platform `birdbuddy` | Keep the domain; do not create a second integration namespace during upstream reconciliation. |
| Config entry | `ConfigFlow.VERSION = 1`; unique ID is `email.lower()`; title is the account display name | Preserve existing entry IDs, stored unique IDs and titles. A new account-ID strategy requires an explicit duplicate-safe migration. |
| Credentials/options | `entry.data`: `email`, `password`; `entry.options.polling_interval`: integer minutes | Preserve stored data/options during schema changes and reauthentication. Never expose credentials in migration output. |
| Device registry | `identifiers={("birdbuddy", F)}`; manufacturer `Bird Buddy, Inc.` | Reuse the existing device; do not prepend account IDs to legacy physical-device identifiers without a registry migration. |
| Runtime account lookup | `hass.data["birdbuddy"][E]` | Internal representation may change; services/media identifiers must still resolve the same account. |
| Media-source root/account | `""` / `E` | Preserve account scope and existing identifiers or provide deliberate translation. |
| Media-source collection/item | `E#collection_id` / `E#collection_id#media_id` | Keep saved media references resolvable; fail cleanly when their account/media is unavailable. |

Entity unique IDs are registered under platform `birdbuddy` with these exact formulas:

| Entity domain | Unique ID | Baseline default |
| --- | --- | --- |
| `sensor` | `F-battery` | Enabled |
| `sensor` | `F-signal` | Disabled |
| `sensor` | `F-state` | Enabled |
| `sensor` | `F-last-sync` | Enabled |
| `sensor` | `F-recent-visitor` | Enabled |
| `sensor` | `F-temperature` | Disabled; incubating |
| `sensor` | `F-food-state` | Disabled; incubating |
| `binary_sensor` | `F-charging` | Enabled |
| `image` | `F-recent-image` | Enabled; carousel position 1 |
| `image` | `F-recent-image-2` through `F-recent-image-5` | Disabled |
| `switch` | `F-offgrid`, `F-audio` | Enabled; availability restricted to owners |
| `select` | `F-power-profile` | Disabled; availability restricted to owners |
| `update` | `F-updater` | Enabled for owners; owner-only availability |

All entity defaults are overridden to disabled for pending invitations. Existing registry settings take precedence over new defaults. Actual HA `entity_id` strings are generated/persisted by HA and can be renamed by the user; README examples such as `image.backyard_recent_visitor_image` are not identifier formulas.

- [ ] Preserve device/entity registry associations, user names, areas, icons, enabled/disabled choices and history across reload/migration.
- [ ] Keep the existing promotion migration narrowly scoped: only `*-recent-visitor` rows with `disabled_by=INTEGRATION` are re-enabled. Never clear `disabled_by=USER`; do not enable signal/carousel/incubating entities incidentally.
- [ ] Add account-level devices/entities without moving or recreating legacy per-feeder entities unnecessarily. `F-last-sync` remains a per-feeder entity whose value is account-wide.
- [ ] Test an existing customized registry before changing account structure or dependency/model classes. Same physical feeder across multiple accounts needs an explicit ownership/routing policy, not a silent unique-ID rewrite.

Sources: `config_flow.py`, `device.py`, `__init__.py`, all entity platform modules and `media_source.py` under `custom_components/birdbuddy/`.

## Behavior and user surfaces to retain

| Contract | Preservation requirement |
| --- | --- |
| Configurable polling | Keep `polling_interval`, range **1–20 minutes**, default **10**. Existing entries without the option retain ten minutes. Options apply via entry reload without restarting HA. |
| Recent visitor display | Retain native `image` entities plus the recent-visitor sensor. A new unidentified visit shows its image with unknown/null species, never the previous visitor's species. |
| Carousel | Retain five newest visits, newest first; positions 2–5 are opt-in. Consecutive visits of the same species remain separate by default (`dedupe_by_species=False`); do not collapse them accidentally. |
| Sensor attributes | Retain `visitors` as an ordered list containing `species`, `media_url`, `created_at`; species can be null and timestamp is serializable. Existing dashboard templates must keep working or receive a documented migration. |
| Raw postcard fallback | Preserve images for unidentified/uncollected postcards without depending on successful legacy sighting conversion or auto-ID. Single-feeder attribution fallback must never become cross-feeder leakage. |
| Image delivery | Prefer usable full-size content URL, otherwise thumbnail. Preserve handling of CloudFront image bytes returned as `text/plain`; invalidate cached bytes when changing or clearing the image. |
| Refresh behavior | Rebuild recent media from fresh API data, seed image state when entities are added, and retain listener cleanup. Signed URLs are temporary locators, not stable identities or archives. |
| Last Sync | Retain timestamp of the last successful account poll. A failed poll must not advance it. Do not describe it as the last feeder visit or device contact. |
| Hardware metadata | Preserve housing-based model/model ID, firmware software version and device hardware version. Current fork imports `FeederHousingType` and `FeederDeviceVersion`; a dependency-only switch to upstream `0.1.0` breaks those imports. Unknown future hardware values must degrade safely. |
| Controls | Keep battery/charging/state/signal and owner restrictions for off-grid/audio/profile/firmware. Profile options follow the supported enum without `UNKNOWN`; unrecognized state must not become a selectable valid option. |
| Diagnostic honesty | Food and temperature remain disabled/incubating until meaningful payloads establish support. Null/absent telemetry must not become fabricated measured zeros. |
| Error isolation | Failure to process one postcard or refresh visitor media must not prevent processing unrelated cards or all feeder telemetry. Make failure visible without silently advancing past retryable work. |
| Localization | Continue forwarding HA language to the client; preserve `en`, `de`, `nl`, `sv` translation keys and lowercase enum states. Stable IDs, rather than translated bird names, identify species. |
| Account media | Preserve account → collection → media browsing, image/video distinction, refresh and URL renewal. Cloud collection/browsing is not a local backup. |
| No startup flood | Preserve the intent to avoid replaying old visitor notifications, while correcting the current blind spot for visitors arriving between setup and the first scheduled poll. Backfill is a separate explicit operation. |

## Event, trigger and service migration contracts

These are observable baseline APIs. They need compatibility handling or a documented migration; their currently broken implementation must not be frozen in place.

| Surface | Current shape |
| --- | --- |
| `birdbuddy_new_feed_item` | `item_id`, `type`, `created_at`, `feeder_id`, `media_url`, `thumbnail_url`. Intended for newly seen visitor items, including unidentified postcards, independently of collection. |
| `birdbuddy_new_postcard_sighting` | `postcard` and `sighting`; nested `sighting.feeder.id` drives current device-trigger filtering. |
| Device trigger | `platform: device`, `domain: birdbuddy`, `type: new_postcard`, HA `device_id`, optional vendor `feeder_id`. Missing feeder ID is derived from the device registry. |
| `birdbuddy.collect_postcard` | Required `postcard.id`, `sighting.sightingReport`, `sighting.feeder.id/name`; optional `device_id`, `strategy`, `best_guess_confidence`, `share_media`. Defaults to recognized strategy and no community sharing. |
| Blueprint | `custom_components/birdbuddy/blueprints/collect_postcard.yaml`, distributed from the fork. Imported copies do not update automatically. |

- [ ] Keep collection, identification, sharing, notification and local download as distinct actions. Do not turn a read-only visitor refresh into an undisclosed collection/share/discard operation.
- [ ] Preserve the new-feed-item notification use case and unknown-visitor images while migrating the legacy collection event/service to the server-side API.
- [ ] Preserve all needed media IDs, types and URLs in a bounded event/retrieval contract. Test the emitted payload through actual internal listeners, sharing and automation consumers; size alone is insufficient.
- [ ] Coordinate service, event, device-trigger, blueprint and README changes. New names or removed fields require explicit transition behavior; do not silently claim the old pass-through contract still works.
- [ ] Preserve user intent for sharing (`false` by default). Ambiguous multi-account requests must not select the first account arbitrarily.
- [ ] Maintain burst-safe postcard handling and retry identity. Do not preserve the old blueprint's `mode: single` drops or in-memory deduplication limitations as desired behavior.

## Existing tests and the meaningful gaps

The checks below refer to the baseline suite. A test's existence is not proof of a broader contract it mocks away.

| Area | Existing useful evidence | Required gap/guard |
| --- | --- | --- |
| Config/setup | `test_config_flow.py`: form, invalid auth, connection error. `test_init.py`: setup/unload smoke and default-promotion registry behavior. | Existing customized registry/IDs, duplicate lowercased email, data migration, reauth, multiple accounts, actual dependency imports and dynamic lifecycle. Setup visitor refresh is stubbed. |
| Options | `test_options_flow.py`: persistence/prefill/range rejection. `test_coordinator.py`: configured/default interval. | Exercise options-update reload and retain user choices/registry state across it. |
| Visitor normalization | `test_visitors.py`: ordering, repeated species, optional dedupe, empty/fallback, mystery, raw postcard, same-node precedence and nonfatal query failure. `test_util.py`: singular/plural media and feeder URL filtering. | Real `collection.species` nesting, explicit feeder IDs, member/pending/public shapes, null telemetry, multi-species/video assets, and ambiguous two-feeder attribution. |
| Sensor/carousel | `test_sensor.py`: callback state/picture coherence and serialized visitor attributes. `test_image_indexed.py`: slot selection, clearing and index unique ID/name. | End-to-end normalized payload → callback → HA state; all legacy unique IDs, restore/reload, clearing actual cached bytes, renewed signed URLs, valid image MIME handling. |
| Last Sync | `test_last_sync.py`: successful timestamp, no advance on failure, sensor passthrough. | Timestamp semantics under partial visitor failure, availability/recovery and real unload scheduling. |
| Notification events | `test_feed_item_event.py`: repeat suppression, slim fields, unidentified image, bounded seen IDs, seed-only behavior. | Arrival between setup and first poll; >20 new items; page-2 failure/retry; repeated timestamps; restart dedupe; multiple accounts and selected feeder attribution. |
| Postcard isolation | `test_coordinator.py`: failed postcard does not kill following cards; no listeners skips legacy conversion; visitor failure is nonfatal. | Actual new API responses/domain errors, retries and progress commit, media completeness, shared account routing. |
| Payload/service/trigger | `test_util.py`: oversized input/slim size; `test_services.py`: schema rejection and mocked `finish_postcard` arguments; `test_device_trigger.py`: matching/nonmatching feeder and config validation. | Emitted compact payload through `PostcardSighting`/new model, internal listener and sharing; image/video URLs; effective oversized strategy; real blueprint burst behavior. `finish_postcard` is mocked, so sharing completeness is unproven. |
| Device metadata/controls | Setup imports and constructs entities; some code executes through setup coverage. | Dedicated current hardware/member/pending/unknown enum and capability fixtures; owner-only action paths and live dependency compatibility. |
| Firmware | Baseline `update.py` coverage 47%; setup covers construction. | Success/failure/timeout/cancellation cleanup, retry after failure, sleeping/low battery/member rejection and unload. |
| Media browser | No dedicated baseline tests; `media_source.py` coverage 0%. | Account/collection/media browse and resolution, video, refresh/expiry, unloaded/missing accounts and stale IDs. |

Tests that intentionally encode baseline behavior must change with the relevant bug fix:

- `test_setup_entry_no_feeders` currently expects setup failure. The planned account model instead accepts a valid account with zero feeders.
- `test_slim_payload_drops_known_bloat` currently asserts that `sighting.medias` is removed. That conflicts with real internal listener/sharing consumers; replace it with bounded **complete** media-contract assertions.
- `test_setup_poll_does_not_fetch_feed_or_fire` plus delayed seed-only behavior do not cover the first-poll notification gap. Preserve no historical flood, not loss of new arrivals.
- Synthetic callback coherence tests do not catch the wrong feed species path; add boundary fixtures instead of copying those simplified shapes.

## Completion gates for changes touching these contracts

- [ ] Record the baseline commit/environment and new test results separately; never relabel the original 84-pass result as validation of later changes.
- [ ] Load an existing config/device/entity registry snapshot with custom names and USER-disabled entries; verify stable identifiers and preserved options.
- [ ] Verify latest image, five-slot carousel, unidentified visit, repeated species and account-wide Last Sync through the integrated refresh path.
- [ ] Verify media/event/service/blueprint behavior using shared realistic sanitized fixtures, including multiple assets, member/owner/pending roles and two accounts/feeders.
- [ ] Exercise failure/retry/unload paths where behavior changed; forbid real network access in ordinary unit tests.
- [ ] Keep schema-valid, fixture-observed, mocked-success and live-account/hardware-verified claims distinct. Live schema recognizes `V2_NOOK`; the baseline tests do not confirm a Nook device works.
- [ ] Update this checklist when an intentional public contract changes, alongside its migration and user-facing documentation.
