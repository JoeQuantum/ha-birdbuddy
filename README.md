# Bird Buddy Plus — Home Assistant Integration

A community fork of [`jhansche/ha-birdbuddy`](https://github.com/jhansche/ha-birdbuddy) by [@JoeQuantum](https://github.com/JoeQuantum), targeting more entities, better reliability, and a faster path for fixes and modern Home Assistant patterns.

This component uses the [`pybirdbuddy`](https://github.com/JoeQuantum/pybirdbuddy) library (also a fork; pinned to the `schema-refresh-2026-05` branch).

## Relation to upstream

This is a **hard fork** with a distinct integration domain (`birdbuddy_plus`), so it can be installed alongside the original `jhansche/ha-birdbuddy` integration — both can run on the same Home Assistant instance against the same Bird Buddy account, which makes it easy to A/B compare.

All credit for the original design and code belongs to [Joe Hansche](https://github.com/jhansche). Improvements in this fork aim to be contributed back upstream where appropriate.

## Migrating from `jhansche/ha-birdbuddy`

Because the domain changed, **none of your existing entities, automations, blueprints, or scripts will auto-migrate.** Things to update if you're switching:

| Old | New |
| --- | --- |
| Domain | `birdbuddy` → `birdbuddy_plus` |
| Event | `birdbuddy_new_postcard_sighting` → `birdbuddy_plus_new_postcard_sighting` |
| Service | `birdbuddy.collect_postcard` → `birdbuddy_plus.collect_postcard` |
| Device trigger `domain` | `birdbuddy` → `birdbuddy_plus` |
| Entity IDs | `<type>.<feeder>_<thing>` → `<type>.<feeder>_<thing>_2` (if upstream is also installed; HA suffixes duplicates) |

You can keep upstream installed during the transition — they don't conflict.

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
2. The `Bird Buddy Plus` page should automatically load (or find it in the HACS Store).
3. Click `Install`.
4. Continue to [Setup](#setup).

Alternatively, click the button below to add the repository:

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?category=Integration&repository=ha-birdbuddy&owner=JoeQuantum)

### Manual

Copy the `birdbuddy_plus` directory from `custom_components` in this repository, and place it inside your Home Assistant Core installation's `custom_components` directory.

## Setup

1. Install this integration.
2. Navigate to the Home Assistant Integrations page (**Settings → Devices & Services**).
3. Click the **+ Add Integration** button in the bottom-right.
4. Search for `Bird Buddy Plus`.

Alternatively, click the button below to add the integration:

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=birdbuddy_plus)

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
| `Recent Visitor` | `sensor`        | State represents the most recent visitor's bird species name, and the `entity_picture` points to the cover media of that recent postcard visit. |
| `State`          | `sensor`        | Current state (ready, offline, etc)                                                                                                             |
| `Signal`         | `sensor`        | Current wifi signal (RSSI)                                                                                                                      |
| `Update`         | `update`        | Show and install Firmware updates (owners only)                                                                                                 |

Some entities are disabled or hidden by default if they represent an advanced use case (for example, the "Signal" and "Recent Visitor" entities). There are also entities disabled by default because the support is not yet enabled by the Bird Buddy API (for example, the Temperature and Food Level sensors).

More entities will be added as this fork matures.

# Media

Bird species and sightings that have _already been collected_ from postcards can be viewed in the Home Assistant Media Browser. To collect a postcard you will need to use the mobile app to open the postcards as they arrive. Only opened postcards can be viewed in the Media Browser (same as the Collections tab in the Bird Buddy app).

# Events

### `birdbuddy_plus_new_postcard_sighting`

This event is fired when a new postcard is detected in the feed.

> **Note (fix for upstream [#78](https://github.com/jhansche/ha-birdbuddy/issues/78))**
>
> Home Assistant's recorder caps `event_data` at 32768 bytes. The raw GraphQL response for a postcard sighting routinely exceeds that (~150 KB in pathological cases) due to signed media URL lists, deeply-nested feeder context, locale-translated species text, and the full suggestions tree. Bird Buddy Plus slims the payload before firing — the resulting event is typically <10 KB.

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

The slim payload preserves everything `birdbuddy_plus.collect_postcard` needs, so pass-through blueprints and automations continue to work.

This event data can also be passed through as-is to the [`birdbuddy_plus.collect_postcard`](#birdbuddy_pluscollect_postcard) service.

This event can also be wired up via the "A new postcard is ready" Device Trigger:

```yaml
trigger:
  - platform: device
    domain: birdbuddy_plus
    type: new_postcard
    device_id: <ha device id>
    feeder_id: <bird buddy feeder id>
```

# Services

### `birdbuddy_plus.collect_postcard`

"Finishes" a postcard sighting by adding the media to the associated species collections, making them available in the [Media Browser](#media). This is the same effect as opening and saving the postcard in the Bird Buddy app.

> **Note**
>
> This service _is not_ intended to be invoked manually — use it in conjunction with the
> [`birdbuddy_plus_new_postcard_sighting`](#birdbuddy_plus_new_postcard_sighting) event, device trigger, or [Blueprint](#blueprint).
>
> Attempting to call the service manually will likely fail, because the service requires the `postcard` and `sighting` data that would be included in the event.

| Service attribute data  | Optional | Description                                                                                |
| ----------------------- | -------- | ------------------------------------------------------------------------------------------ |
| `postcard`              | No       | Postcard data from `birdbuddy_plus_new_postcard_sighting` event                            |
| `sighting`              | No       | Sighting data from `birdbuddy_plus_new_postcard_sighting` event                            |
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
    event_type: birdbuddy_plus_new_postcard_sighting
  # OR a device trigger:
  - platform: device
    domain: birdbuddy_plus
    type: new_postcard
    # $ids...
action:
  - service: birdbuddy_plus.collect_postcard
    data:
      strategy: best_guess
      # pass-through these 2 event fields as they are
      postcard: "{{ trigger.event.data.postcard }}"
      sighting: "{{ trigger.event.data.sighting }}"
```

#### Blueprint

To simplify the combination of the trigger and the action of collecting the postcard, you can import a predefined [Blueprint](https://www.home-assistant.io/docs/automation/using_blueprints/).

To add the Blueprint, use the button below:

[![Open your Home Assistant instance and show the blueprint import dialog with a specific blueprint pre-filled.](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FJoeQuantum%2Fha-birdbuddy%2Fblob%2Fmain%2Fcustom_components%2Fbirdbuddy_plus%2Fblueprints%2Fcollect_postcard.yaml)

Or go to **Settings → Automations & Scenes → Blueprints**, click the **Import Blueprint** button, and enter this URL:

```
https://github.com/JoeQuantum/ha-birdbuddy/blob/main/custom_components/birdbuddy_plus/blueprints/collect_postcard.yaml
```

After import, [create an automation from the Blueprint](https://www.home-assistant.io/docs/automation/using_blueprints/#blueprint-automations). If we update the Blueprint upstream, your imported Blueprint will not automatically receive the update — you may need to re-import.
