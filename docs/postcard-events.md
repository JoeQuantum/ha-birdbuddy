# Postcard event and detail contract

`birdbuddy_new_postcard` uses `schema_version: 1`. The payload contains only
selected public-contract fields from the canonical `Visit` model. It contains
no report token, match token, fabricated sighting report, or raw API response.
Event creation does not identify, collect, or share a postcard.

<!-- markdownlint-disable MD013 -->

| Field | Meaning |
| --- | --- |
| `postcard_id` | The vendor feed-item/postcard ID. |
| `event_id`, `source` | Stable durable-delivery ID and `live`/`backfill` source; included before measuring the byte budget. |
| `config_entry_id`, `account_id` | The HA entry and separate authenticated vendor account ID. |
| `feeder_id`, `attribution` | Known feeder and attribution evidence, or null when unknown. |
| `kind`, `created_at` | API item kind and normalized UTC timestamp; malformed/missing dates remain null. |
| `species` | Recognized species objects containing `id`, `name`, and `icon_url`. Suggestions are not promoted to recognized species. |
| `medias` | Every selected image/video/unknown asset, ordinarily complete. |
| `media` | Representative typed asset, preferably an image; may be a video for a video-only card. |
| `preview_url` | An image URL or a video's thumbnail, never its video content URL. |
| `media_count`, `species_count` | Counts before event compaction. |
| `medias_complete`, `media_complete`, `species_complete` | Whether the corresponding selected details are intact. |
| `details_required`, `omitted_fields` | Explicit indication of event compaction. |
| `details` | Discoverable read-only detail action and account-scoped request data. |

<!-- markdownlint-enable MD013 -->

Each media object contains `id`, `kind`, `created_at`, `content_url`, and
`thumbnail_url`. `kind` retains the API type, such as `MediaImage` or
`MediaVideo`. Sparse historical data remains nullable. A video-only postcard
still has its video in `medias`; it does not become an empty image result.
All timestamps that are available are ISO 8601 UTC strings.

## Size and complete details

Home Assistant 2025.1.4, 2026.7.1, and 2026.11.1 recorder each limit encoded
event data to 32,768 bytes. The integration budgets 30,000 bytes using HA's
`json_bytes` encoder, so Unicode and long signed URLs are counted as bytes.
Tests also pass the result through recorder's real event-data serializer.

Ordinary events include all selected media and species. Oversized events first
omit the media list and mark it incomplete while retaining its original count.
If necessary, they omit representative URLs, species icons, and finally
species/representative detail, recording the omitted field names. The canonical
model is unchanged. Opaque identity and account-routing fields are never
truncated; an envelope that cannot fit fails explicitly instead of emitting
an unusable lookup reference.

Every event advertises the same detail request:

```yaml
action: birdbuddy.get_postcard_details
data:
  postcard_id: "the-event-postcard-id"
  config_entry_id: "the-event-account-entry-id"
response_variable: postcard_details
```

The response uses the same versioned fields with complete selected media and
species. It makes one `get_feed_item` read on the resolved account and performs
no inference, collection, or sharing. It refreshes the selected API detail;
it is not a local archival snapshot and availability still depends on the
account and upstream item. It does not apply the recorder event-size budget
to the response or silently drop assets.
[Local archiving](local-archive.md) is a separate workflow and is not required
for this lookup.

## Compatibility and migration

The existing device-trigger type `new_postcard`, device identifiers, and entity
unique IDs remain stable. Its event subscription moves to the canonical event;
account/device routing must be validated before attaching the trigger.

`birdbuddy_new_feed_item` is a temporary notification adapter. It retains its
six existing fields (`item_id`, `type`, `created_at`, `feeder_id`, `media_url`,
`thumbnail_url`) and adds the canonical contract. `media_url` remains an image
content URL; video-only notifications can use `thumbnail_url`. This adapter is
subject to the same measured byte budget and explicit omissions.

`birdbuddy_new_postcard_sighting` is not emitted with invented report data.
Legacy listeners/actions require a Home Assistant Repair directing users to
the canonical event and explicit modern selection policy. Unsupported strategy,
confidence, and sighting fields fail immediately, even during compatibility.
No automation is rewritten and no collection is automatically performed.

The notification adapter and equivalent ID/sharing aliases remain available
through releases **0.2 and 0.3**, with removal planned for **0.4.0**. Repairs
expose this migration window. No old strategy or sighting contract is restored.
Use the [migration guide](automation-migration.md) to replace an installed
blueprint copy and the [notification example](notifications.md) for safe image,
video and unidentified-species handling.
