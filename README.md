# Bird Buddy — Home Assistant Integration

A community fork of [jhansche/ha-birdbuddy](https://github.com/jhansche/ha-birdbuddy),
maintained by [@JoeQuantum](https://github.com/JoeQuantum). Original integration
credit belongs to [Joe Hansche](https://github.com/jhansche).

This integration uses the [forked pybirdbuddy client](https://github.com/JoeQuantum/pybirdbuddy).
The runtime dependency is pinned to an immutable commit in
[manifest.json](custom_components/birdbuddy/manifest.json); development tests
exercise the same client contract.

## Compatibility and upgrading

Requires Home Assistant **2025.1.4 or newer**. See the tested minimum, modern and
current versions in [DEVELOPMENT.md](DEVELOPMENT.md). Tests use recorded and
synthetic responses; physical hardware support has separate
[evidence and limits](docs/hardware-and-roles.md).

This fork uses the same `birdbuddy` domain as upstream. Keep only one copy of
the integration installed. Back up your configuration before replacing an
existing installation; preserve its config entry and registry data where
possible to retain device and entity identities.

**Review collection automations before upgrading.** The old
`birdbuddy_new_postcard_sighting` report-token workflow is no longer emitted.
Current collection requires an explicit `selection_policy: server`; it has no
verified equivalence to recognized-only, best-guess, mystery or numeric
confidence rules. Disable old collection automations and follow the
[automation migration guide](docs/automation-migration.md). Home Assistant
Repairs explains deprecated inputs. Your automations are not rewritten.

## Installation and setup

You need a Bird Buddy account with email/password authentication. Google,
Facebook and other social sign-in methods are not implemented. An invited
email/password member account may expose fewer fields and no owner controls;
do not reset or re-pair hardware merely to install this integration.

### Marketplace (Home Assistant 2026.11 and newer)

HACS is built into these versions as **Settings → Marketplace**. A separate HACS
installation is no longer used. See the
[Home Assistant Marketplace guide](https://www.home-assistant.io/integrations/marketplace/).

1. Open **Settings → Marketplace → Add from link** and enter
   `https://github.com/JoeQuantum/ha-birdbuddy`.
2. Complete the GitHub connection when prompted; custom repositories require it.
3. Install Bird Buddy and restart Home Assistant when prompted.
4. Open **Settings → Devices & Services → Add Integration → Bird Buddy** and
   enter your Bird Buddy email and password.

Existing HACS installations migrate to Marketplace when Home Assistant upgrades.
Back up your configuration before upgrading either Home Assistant or Bird Buddy.

### HACS (Home Assistant 2025.1.4–2026.10)

1. Add `https://github.com/JoeQuantum/ha-birdbuddy` as a HACS custom repository,
   category **Integration**.
2. Install Bird Buddy and restart Home Assistant when prompted.
3. Open **Settings → Devices & Services → Add Integration → Bird Buddy**.
4. Enter your Bird Buddy email and password.

[![Add this HACS repository](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?category=Integration&repository=ha-birdbuddy&owner=JoeQuantum)

### Manual

Copy `custom_components/birdbuddy` into your Home Assistant configuration's
`custom_components` directory, restart Home Assistant, then add Bird Buddy
through **Settings → Devices & Services**.

[![Set up Bird Buddy](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=birdbuddy)

Expired authentication opens a reauthentication flow for the existing account.
Use that same Bird Buddy account: account UUID checks prevent silently moving
an existing entry to another account. An account with no feeders can remain
configured and discover accepted feeders later.

## Polling and devices

Under **Settings → Devices & Services → Bird Buddy → Configure**, set the polling
interval from **1–20 minutes**, default **10**. Changes apply without restarting.
This is a cloud polling integration, not a real-time push feed.

Each accepted owner/member feeder gets a device. Newly accepted feeders and
role changes are discovered on successful account refreshes. Removed feeders
become unavailable; registry identities and user-disabled settings are retained.
Pending invitations do not create operational entities. Shared feeders need
explicit account selection for ambiguous actions.

**Last Sync** is the last successful account/feeder telemetry refresh in UTC.
The same account timestamp is shown on each of its feeders. It is not a promise
that every postcard, image or collection request succeeded: postcard processing
has independent status and Repairs. See
[diagnostics and recovery](docs/diagnostics-and-restore.md).

## Entities

<!-- markdownlint-disable MD013 -->

| Entity | Behavior |
| --- | --- |
| Battery / Charging | Reported percentage and charging state; missing values stay unknown |
| Feeder State | Reported device state; future values fall back to unknown |
| Signal Strength | Reported Wi-Fi RSSI; disabled by default |
| Audio | Owner control; omitted/unavailable when audio is explicitly unsupported |
| Off-Grid | Owner control with bounded completion checks |
| Power Profile | Owner's configured selection; disabled by default; does not infer Smart Energy's running profile |
| Recent Visitor | Latest recognized species name, or unknown for unidentified visits; selected preview image and last-five `visitors` attribute |
| Recent Visitor Image | Most recent preview image; enabled by default |
| Recent Visitor Image 2–5 | Additional recent visits; disabled by default |
| Last Sync | Account telemetry timestamp attached to each feeder |
| Update | Owner firmware availability and immediate installation with fresh preconditions and bounded monitoring |
| Temperature / Food Level | Experimental, disabled by default; missing readings stay unknown |

<!-- markdownlint-enable MD013 -->

Enable optional entities on the feeder's entity page. Hardware generations,
housings, account roles and subscription limits are documented in the
[hardware matrix](docs/hardware-and-roles.md). Schema recognition of Nook,
Ultra Frenzy or Smart Energy does not establish verified physical behavior or
subscription eligibility.

## Recent images and media browser

The recent visitor sensor exposes up to five entries with `species`, `media_url`
and `created_at`; unidentified species are null. Preview images may appear
before collection. Video-only visits remain visits; a thumbnail can be shown
when available, but a video content URL is never treated as an image.

Image entities refresh expiring media through read-only detail lookups and
bounded caching. URLs are temporary and should not be used as stable IDs or
permanent archive links. A failed refresh remains a visible failure.

Collected media is browsable under **Media → Bird Buddy**, grouped by account
and species collection. Cloud collection through the app or explicit service
is separate from downloading files. See the [local archive recipe](docs/local-archive.md)
to save every selected image and video to an explicit local destination.

### Dashboard example

Replace these example entity IDs with your feeder's IDs:

```yaml
type: vertical-stack
cards:
  - type: picture-entity
    entity: image.backyard_recent_visitor_image
    name: Latest visitor
  - type: entities
    entities:
      - sensor.backyard_recent_visitor
      - sensor.backyard_battery
      - sensor.backyard_feeder_state
      - switch.backyard_audio
```

For a carousel, enable image positions 2–5 and add those image entities to your
chosen dashboard card. The integration supplies the images; third-party card
installation and compatibility are independent.

## Events and delivery

`birdbuddy_new_postcard` is the collection-safe event for newly observed
`FeedItemNewPostcard` items. Its version 1 payload includes a stable `event_id`,
`source`, `config_entry_id`, `account_id`, `feeder_id`, `postcard_id`, recognized
`species`, typed `medias` and `preview_url`. Unknown attribution remains null.

Large events explicitly mark omitted detail. Call `birdbuddy.get_postcard_details`
to retrieve all selected media/species and refresh URLs without inference,
collection or sharing. See the [complete event contract](docs/postcard-events.md).

Initial setup seeds the latest baseline without firing collection events.
Subsequent pending work and checkpoints are stored per account. Delivery is
at least once: after a crash, the same event may repeat with its stable ID.
External effects need their own deduplication. The
[durable intake guide](docs/durable-intake.md) explains capacity, retention,
retry, unknown outcomes and recovery.

`birdbuddy_postcard_backfill` is separate historical delivery, never the default
collection trigger. The temporary `birdbuddy_new_feed_item` notification
adapter retains `item_id`, `type`, `created_at`, `feeder_id`, `media_url` and
`thumbnail_url`, plus the new contract. The image URL may be null, particularly
for video-only visits. The adapter and equivalent `postcard.id` / `share_media`
action aliases remain through releases **0.2 and 0.3**, with removal planned
for **0.4.0**. Legacy strategy/sighting data has no compatibility mapping.

The tested [notification example](examples/notify_postcard.yaml) handles
recognized, unidentified and video-only postcards using fresh details and
stable notification tags. Read the [notification guide](docs/notifications.md)
for queue, image and duplicate-delivery limits.

## Actions

All supplied `config_entry_id`, `account_id`, `device_id` and `feeder_id` targets
must agree. Use an explicit account when a feeder is shared. Never choose an
account based on list order or infer feeder ownership from a media URL.

<!-- markdownlint-disable MD013 -->

| Action | Purpose |
| --- | --- |
| `birdbuddy.get_postcard_details` | Read full selected details for `postcard_id`; returns a response |
| `birdbuddy.get_account_insights` | Read selected account insights on demand; returns a response |
| `birdbuddy.identify_postcard` | Explicitly run inference; does not collect/share |
| `birdbuddy.collect_postcard` | Collect using explicit `selection_policy: server`; booleans `identify` and `share` default false |
| `birdbuddy.backfill` | Read bounded history and queue separate backfill events; no collection |
| `birdbuddy.retry_feed_items` | Retry failed event delivery; subscribed automations may act again |
| `birdbuddy.acknowledge_postcard_retry` | Explicitly permit a later retry after checking an uncertain action; performs no cloud mutation itself |

<!-- markdownlint-enable MD013 -->

The [service definitions](custom_components/birdbuddy/services.yaml) list all
fields and limits. Unknown action outcomes are retained and are not blindly
retried. Read the postcard's current state and check the Bird Buddy app before
acknowledging a retry. Read-only polling and historical replay never identify,
collect or share automatically.

### Optional account insights

`birdbuddy.get_account_insights` exposes hardware and feature metadata, Smart Energy
running state, species/preferences, named birds, vendor analytics and remote
connection metadata. Choose a resource and account explicitly. It uses bounded
caches and makes no additional background polls or changes to your account.
Read the [resource and response contract](docs/account-insights.md) for examples,
limits and unavailable results. These queries are schema- and fixture-tested;
live account availability and device support remain unverified.

### Collect a chosen postcard

This action deliberately accepts server selection, without inference or sharing:

```yaml
service: birdbuddy.collect_postcard
data:
  config_entry_id: YOUR_BIRD_BUDDY_CONFIG_ENTRY
  postcard_id: YOUR_POSTCARD_ID
  selection_policy: server
  identify: false
  share: false
```

Read the [selection-policy evidence](docs/postcard-selection-policy.md) before
migrating a custom recognized-only, mystery or confidence-based workflow.

### Collection blueprint

The [collection blueprint](custom_components/birdbuddy/blueprints/collect_postcard.yaml)
has explicit account/device selectors and a finite queue of 20 by default.
Its initial policy is disabled pending review. Choose **Use Bird Buddy's server
selection** deliberately, then decide whether to identify or share. It accepts
only version 1 live new postcards for the selected feeder and account.

[![Import the collection blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FJoeQuantum%2Fha-birdbuddy%2Fblob%2Fmain%2Fcustom_components%2Fbirdbuddy%2Fblueprints%2Fcollect_postcard.yaml)

Queue overflow is logged as a warning; failures appear in traces. Automation
queues do not survive reloads/restarts. Installed blueprint copies are not
updated by integration updates: reimport and migrate deliberately using the
[upgrade guide](docs/automation-migration.md).

## Development and support

Report fork issues at [JoeQuantum/ha-birdbuddy](https://github.com/JoeQuantum/ha-birdbuddy/issues).
Include integration/HA versions and sanitized diagnostics, not passwords,
report tokens, private locations or signed media URLs. See
[development checks](DEVELOPMENT.md), [fixture provenance](docs/fixtures.md) and
[diagnostics and restore](docs/diagnostics-and-restore.md).
