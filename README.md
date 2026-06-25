# Bird Buddy Plus — Home Assistant Integration

A community fork of [`jhansche/ha-birdbuddy`](https://github.com/jhansche/ha-birdbuddy) by [@JoeQuantum](https://github.com/JoeQuantum), targeting more entities, better reliability, and a faster path for fixes and modern Home Assistant patterns.

This component uses the [`pybirdbuddy`](https://github.com/JoeQuantum/pybirdbuddy) library (also a fork; pinned to the `schema-refresh-2026-05` branch).

## Relation to upstream

This is a domain-compatible community fork of [`jhansche/ha-birdbuddy`](https://github.com/jhansche/ha-birdbuddy). It uses the same integration domain (`birdbuddy`), so it **replaces** the upstream integration — the two cannot be installed alongside each other on the same Home Assistant instance.

All credit for the original design and code belongs to [Joe Hansche](https://github.com/jhansche). Improvements in this fork aim to be contributed back upstream where appropriate.

## Migrating from upstream `jhansche/ha-birdbuddy`

This fork is a drop-in replacement: same domain, same entity IDs, same event/service names. **You must uninstall the upstream integration first** — both register the `birdbuddy` domain and HA will not load two integrations with the same domain. Existing automations, blueprints, and dashboard cards that reference `birdbuddy_*` entities, `birdbuddy_new_postcard_sighting` events, or the `birdbuddy.collect_postcard` service will keep working after the swap.

> **Upgrading from an early `birdbuddy_plus` build of this fork:** if you ever ran a version of this fork that used the `birdbuddy_plus` domain, manually delete the `custom_components/birdbuddy_plus/` directory in your HA config after updating. HACS does not remove the old folder, and Home Assistant will keep loading the stale copy — shadowing the current `birdbuddy` integration with outdated code.

## Prior to installation

You will need your Bird Buddy `email` and `password`.

> **Note**
>
> If your BirdBuddy account was created using SSO (Google, Facebook, etc), those methods will
> not work currently. To work around that, you can sign up a new account using email and password,
> and then invite that new account as a member of your main/owner account. Be aware that certain
> information or functionality may not be available to member accounts (for example, "off-grid"
> settings and firmware version).
>
> Alternatively, you may reset the Bird Buddy unit and re-pair it with a new account that was created
> with a password. See [Bird Buddy support](https://support.mybirdbuddy.com/hc/en-us/articles/9764938883089-Connecting-Bird-Buddy-to-a-different-Wi-Fi-network)
> for more information.

## Installation

### With HACS

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

1. Open HACS Settings and add this repository (`https://github.com/JoeQuantum/ha-birdbuddy/`)
   as a Custom Repository (use **Integration** as the category).
2. The `Bird Buddy` page should automatically load (or find it in the HACS Store).
3. Click `Install`.
4. Continue to [Setup](#setup).

Alternatively, click the button below to add the repository:

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?category=Integration&repository=ha-birdbuddy&owner=JoeQuantum)

### Manual

Copy the `birdbuddy` directory from `custom_components` in this repository, and place it inside your Home Assistant Core installation's `custom_components` directory.

## Setup

1. Install this integration.
2. Navigate to the Home Assistant Integrations page (**Settings → Devices & Services**).
3. Click the **+ Add Integration** button in the bottom-right.
4. Search for `Bird Buddy`.

Alternatively, click the button below to add the integration:

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=birdbuddy)

# Devices

A device is created for each Bird Buddy feeder associated with the account. See below for the entities available.

# Entities

| Entity           | Entity Type     | Notes                                                                                                                                           |
|------------------|-----------------|-------------------------------------------------------------------------------------------------------------------------------------------------|
| `Audio`          | `switch`        | Whether recorded visitor videos will include audio.                                                                                             |
| `Battery`        | `sensor`        | Current Bird Buddy battery percentage                                                                                                           |
| `Charging`       | `binary_sensor` | Whether the Bird Buddy is currently charging                                                                                                    |
| `Off-Grid`       | `switch`        | Present and toggle Off-Grid status (owners only)                                                                                                |
| `Power Profile`  | `select`        | Choose between Power Profile settings. NOTE: `FRENZY_MODE` appears to be a paid feature requiring an active payment subscription.               |
| `Recent Visitor` | `sensor`        | State represents the most recent visitor's bird species name (blank for an unidentified visitor), and the `entity_picture` points to that visit's image — fetched directly from the postcard, so it appears even before the bird is identified in the app. The `visitors` attribute exposes the last 5 visits as a list (`species`, `media_url`, `created_at`) for templating; `species` is `null` for unidentified visits. |
| `Recent Visitor Image`     | `image`         | The most recent visitor's image. Enabled by default.                                                                                  |
| `Recent Visitor Image 2-5` | `image`         | Carousel positions 2..5 of the recent-visitor feed. Disabled by default — enable in **Settings → Devices → Bird Buddy** for the carousel dashboard cards (see below). |
| `State`          | `sensor`        | Current state (ready, offline, etc)                                                                                                             |
| `Signal`         | `sensor`        | Current wifi signal (RSSI)                                                                                                                      |
| `Update`         | `update`        | Show and install Firmware updates (owners only)                                                                                                 |

Some entities are disabled or hidden by default if they represent an advanced use case (for example, the "Signal" entity), or because the support is not yet enabled by the Bird Buddy API (for example, the Temperature and Food Level sensors).

More entities will be added as this fork matures.

# Media

Bird species and sightings that have _already been collected_ from postcards can be viewed in the Home Assistant Media Browser. To collect a postcard you will need to use the mobile app to open the postcards as they arrive. Only opened postcards can be viewed in the Media Browser (same as the Collections tab in the Bird Buddy app).

# Dashboards

> This native carousel replaces the older download-and-rotate-files automation pattern that some forum posts recommend. The integration now retains the last 5 visits with fresh signed URLs (rebuilt from the feed each poll), so a third-party carousel card can drive a slideshow directly off the entities.

## Recent visitor carousel

The integration exposes the most recent visitor at `image.<feeder>_recent_visitor_image` (enabled by default) and positions 2..5 at `image.<feeder>_recent_visitor_image_2` … `_5` (disabled by default — enable them under **Settings → Devices & Services → Bird Buddy → Entities**).

Pair them with [Simple Swipe Card](https://github.com/nutteloost/simple-swipe-card) (actively maintained, has a built-in carousel mode and auto-swipe). The example below uses `picture-entity` cards so each slide shows the bird image and a tap action that opens the entity:

```yaml
type: custom:simple-swipe-card
card_min_height: 240
loop_mode: infinite
auto_swipe: true
swipe_interval: 5000
cards:
  - type: picture-entity
    entity: image.backyard_recent_visitor_image
    show_state: false
    show_name: false
  - type: picture-entity
    entity: image.backyard_recent_visitor_image_2
    show_state: false
    show_name: false
  - type: picture-entity
    entity: image.backyard_recent_visitor_image_3
    show_state: false
    show_name: false
  - type: picture-entity
    entity: image.backyard_recent_visitor_image_4
    show_state: false
    show_name: false
  - type: picture-entity
    entity: image.backyard_recent_visitor_image_5
    show_state: false
    show_name: false
```

[`swipe-card-lite`](https://github.com/nutteloost/swipe-card-lite) (same author, no auto-swipe) is also a good fit if you want a manual-swipe gallery.

> **Avoid `Image` cards inside the older unmaintained `custom:swipe-card`.** That combination throws `Unknown type encountered: Image` and the card silently fails to render. If you must use `swipe-card`, switch the inner cards to `picture-entity` or `picture-glance` as shown above.

You can also drive a custom carousel from the `Recent Visitor` sensor's `visitors` attribute (a list of `{species, media_url, created_at}`), which is useful for templating or for cards that take a list of URLs directly.

## Example dashboards

A simple "what's at the feeder" panel — entities for state-at-a-glance, plus the latest visit picture for the photo:

```yaml
type: vertical-stack
cards:
  - type: picture-entity
    entity: image.backyard_recent_visitor_image
    name: Latest visitor
  - type: entities
    title: Backyard feeder
    entities:
      - entity: sensor.backyard_recent_visitor
        name: Most recent visitor
      - entity: sensor.backyard_battery
      - entity: sensor.backyard_state
      - entity: switch.backyard_audio
```

> **Some entities are disabled by default** — `Signal Strength`, `Temperature`, `Food Level`, and the carousel positions `Recent Visitor Image 2` through `_5`. To enable any of them: **Settings → Devices & Services → Bird Buddy → (your feeder) → +N entities not shown → Enable**. (This addresses upstream docs request [#64](https://github.com/jhansche/ha-birdbuddy/issues/64).)

# Events

### `birdbuddy_new_postcard_sighting`

This event is fired when a new postcard is detected in the feed *and Bird Buddy is able to convert it to a sighting*. See "Postcard auto-collection" below for how the integration handles cases where Bird Buddy refuses the conversion.

> **Note (fix for upstream [#78](https://github.com/jhansche/ha-birdbuddy/issues/78))**
>
> Home Assistant's recorder caps `event_data` at 32768 bytes. The raw GraphQL response for a postcard sighting routinely exceeds that (~150 KB in pathological cases) due to signed media URL lists, deeply-nested feeder context, locale-translated species text, and the full suggestions tree. Bird Buddy slims the payload before firing — the resulting event is typically <10 KB.

## Postcard auto-collection (handling `INTERNAL_SERVER_ERROR`)

The Bird Buddy API intermittently returns `INTERNAL_SERVER_ERROR` from the `sightingCreateFromPostcard` mutation — see upstream issue [#98](https://github.com/jhansche/ha-birdbuddy/issues/98). This affects only **auto-collection** — turning a postcard into a saved sighting for the [`birdbuddy.collect_postcard`](#birdbuddycollect_postcard) service and the [Media Browser](#media). **It does not affect the `Recent Visitor` image or `visitors` attribute**, which (as of v0.1.8) are fetched directly from the postcard feed node and appear whether or not the bird has been identified in the app. So a feeder without auto-ID still surfaces its visitors' images (with `species: null`); only saving them to collections requires the mutation to succeed.

What the integration does when the error occurs:

- The failing `sightingCreateFromPostcard` call is caught.
- A warning is logged naming the postcard ID.
- That postcard is skipped (no `birdbuddy_new_postcard_sighting` event fires for it, so no `collect_postcard` automation runs against it).
- The remaining postcards in the same refresh cycle are still processed.
- The coordinator stays healthy — feeder state, battery, signal, and other entities keep updating.

**Whether a skipped postcard is recoverable on a subsequent refresh has not been confirmed.** The feed cursor in the underlying library advances unconditionally during refresh, so a postcard skipped in one cycle may not reappear in later cycles even if a later attempt would succeed. Investigation is ongoing.

| Field      | Description                                                                                              |
| ---------- | -------------------------------------------------------------------------------------------------------- |
| `postcard` | `{id, __typename, createdAt}` — just enough to reference the postcard.                                   |
| `sighting` | Slim view of `PostcardSighting`; see fields below.                                                        |

`sighting` contains:

- `sighting.feeder` — `{id, name}` only. Use `id` to filter automations to a specific feeder (the Device Trigger does this automatically).
- `sighting.coverMedia` — `{id, __typename, thumbnailUrl, contentUrl}` for the first (representative) image. Time-sensitive signed URLs. Use the Recent Visitor entity's `entity_picture` for a persistent reference instead.
- `sighting.videoMedia` — `{id, __typename}` when a video is available (id is sufficient for `collect_postcard`).
- `sighting.sightingReport`:
  - `.reportToken` — opaque signed token; required by `collect_postcard`.
  - `.sightings[]` — list of sightings grouped together in the postcard. Each entry has:
    - `id`, `__typename` (e.g. `SightingRecognizedBird`, `SightingCantDecideWhichBird`)
    - `matchTokens[]`
    - `species` — `{id, __typename, name, iconUrl}` for the recognized species
    - `suggestions[]` — alternative species the AI considered. Each `{id, __typename, species: {id, __typename, name}}`. `iconUrl` is intentionally dropped from suggestions to stay under the recorder cap (each signed URL is ~600-900 bytes and there can be 5-10 suggestions per sighting).

The slim payload preserves everything `birdbuddy.collect_postcard` needs, so pass-through blueprints and automations continue to work.

This event data can also be passed through as-is to the [`birdbuddy.collect_postcard`](#birdbuddycollect_postcard) service.

This event can also be wired up via the "A new postcard is ready" Device Trigger:

```yaml
trigger:
  - platform: device
    domain: birdbuddy
    type: new_postcard
    device_id: <ha device id>
    feeder_id: <bird buddy feeder id>
```

# Services

### `birdbuddy.collect_postcard`

"Finishes" a postcard sighting by adding the media to the associated species collections, making them available in the [Media Browser](#media). This is the same effect as opening and saving the postcard in the Bird Buddy app.

> **Note**
>
> This service _is not_ intended to be invoked manually — use it in conjunction with the
> [`birdbuddy_new_postcard_sighting`](#birdbuddy_new_postcard_sighting) event, device trigger, or [Blueprint](#blueprint).
>
> Attempting to call the service manually will likely fail, because the service requires the `postcard` and `sighting` data that would be included in the event.

| Service attribute data  | Optional | Description                                                                                |
| ----------------------- | -------- | ------------------------------------------------------------------------------------------ |
| `postcard`              | No       | Postcard data from `birdbuddy_new_postcard_sighting` event                            |
| `sighting`              | No       | Sighting data from `birdbuddy_new_postcard_sighting` event                            |
| `strategy`              | Yes      | Strategy for resolving the sighting (see strategies below, default: `recognized`)          |
| `best_guess_confidence` | Yes      | Minimum confidence to support `"best_guess"` strategy (default: 10%)                       |
| `share_media`           | Yes      | Whether the saved media will also be shared with the community (default: false)            |

Postcard sighting strategies:

- `recognized` (default): collect the postcard only if Bird Buddy's AI identified a bird species. Note: the identified species may be incorrect. Sightings not recognized by the Bird Buddy API will be *discarded*.
- `best_guess`: In the "can't decide which bird" sightings, a list of possible species is usually included. This strategy behaves like `recognized`, but if the species is not recognized it will select the highest-confidence species automatically (assuming that confidence is at least `best_guess_confidence`, default 10%). If none of the suggestions meet the threshold, the sighting is *discarded*.
- `mystery`: Same behavior as `best_guess`, but if no species meets the confidence threshold, collect the sighting as a "Mystery Visitor".

#### Automation example

```yaml
trigger:
  - platform: event
    event_type: birdbuddy_new_postcard_sighting
  # OR a device trigger:
  - platform: device
    domain: birdbuddy
    type: new_postcard
    # $ids...
action:
  - service: birdbuddy.collect_postcard
    data:
      strategy: best_guess
      # pass-through these 2 event fields as they are
      postcard: "{{ trigger.event.data.postcard }}"
      sighting: "{{ trigger.event.data.sighting }}"
```

#### Blueprint

To simplify the combination of the trigger and the action of collecting the postcard, you can import a predefined [Blueprint](https://www.home-assistant.io/docs/automation/using_blueprints/).

To add the Blueprint, use the button below:

[![Open your Home Assistant instance and show the blueprint import dialog with a specific blueprint pre-filled.](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FJoeQuantum%2Fha-birdbuddy%2Fblob%2Fmain%2Fcustom_components%2Fbirdbuddy%2Fblueprints%2Fcollect_postcard.yaml)

Or go to **Settings → Automations & Scenes → Blueprints**, click the **Import Blueprint** button, and enter this URL:

```
https://github.com/JoeQuantum/ha-birdbuddy/blob/main/custom_components/birdbuddy/blueprints/collect_postcard.yaml
```

After import, [create an automation from the Blueprint](https://www.home-assistant.io/docs/automation/using_blueprints/#blueprint-automations). If we update the Blueprint upstream, your imported Blueprint will not automatically receive the update — you may need to re-import.
