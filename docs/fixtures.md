# Reusable Bird Buddy fixtures

The corpus in `tests/fixtures/` supplies realistic boundaries for the account, visitor, media, event and retry work. It deliberately does not assert the baseline's broken runtime behavior. In particular, it does not require zero-feeder setup to fail, strip media arrays, lose startup events or select the first configured account.

## Loading and evidence

```python
from tests.fixtures import load_fixture

analysis = load_fixture("public_postcard_analysis.json")
```

The loader accepts only filenames listed in `manifest.json` and parses a fresh independent object on every call. Tests may mutate their copy. No fixture loader downloads data, obtains credentials or calls the API. No existing `conftest.py` fixture is replaced.

Each manifest entry records its purpose, root GraphQL type, origin and intentional schema exceptions. Public excerpts include an exact source URL/commit and source-file SHA-256. The source is the MIT-0-licensed [`jhansche/pybirdbuddy`](https://github.com/jhansche/pybirdbuddy/tree/207ed47578016e7223708e94d36a6b5be8c3c1e4/tests/fixtures) fixture corpus. Those upstream files describe historical sanitized API captures; their structure is useful evidence, not a newly verified account response.

| Origin | Meaning |
| --- | --- |
| `public_capture_resanitized` | Minimal selected public payload, sanitized again in memory. Field structure, enum values and media cardinality are preserved; identity/contact/location/URLs are placeholders. |
| `synthetic_from_live_schema` | Constructed selected response following the public schema retrieved on 2026-11-13. Never describe it as an observed device/account response. |
| `synthetic_adversarial` | Deliberate robustness case. `schema_valid: false` identifies known schema violations; a large but valid payload remains schema-valid. |

`schema_shapes.json` contains a projection of the live introspection fields/types needed for these responses, with the source hash and timestamp. `validate_fixture_shape()` checks field names, union/interface membership, selected-field nullability, enum values and basic scalar/list shapes. It permits absent unselected fields, as GraphQL requires. It is not a full response-to-query validator and does not establish permissions, required selections, actual sensor behavior or working backend operations.

## Fixture map

| Fixture | Boundary it preserves |
| --- | --- |
| `public_owner_account.json` | Historical owner `Me` selection, including telemetry and account settings. |
| `public_postcard_analysis.json` | Modern recognized preview with **five images plus one video**, explicit feeder and inference fields. |
| `public_collected_partial.json` | Public collected result with **eleven ID/type-only assets**. No media URL/timestamp is invented to make the parser's job easier. |
| `public_collection.json` | Account collection/cover metadata using the older selected `feederName` field. |
| `account_owner_hardware.json` | Owner hardware/capability metadata and null food/temperature. `V2_NOOK` is schema-recognized; this fixture is not hardware validation. |
| `account_member_pending.json` | Member lacks owner settings and can have null metrics; pending feeder has only ID/name/type. |
| `account_empty.json` | A valid account can have no feeder objects. |
| `accounts_shared_feeder.json` | Two distinct accounts share a physical feeder; one also owns another. Do not account-prefix the existing device identity implicitly. |
| `postcard_complete.json` | Full selected-shape new card with two images, video and explicit feeder. |
| `postcard_unrecognized.json` | Empty recognized-species preview even with `HIGH_CONFIDENCE`. |
| `postcard_video_only.json` | No image, but a valid sole video asset. |
| `postcard_collected_complete.json` | Multiple species and mixed media, explicitly synthetic and distinct from the sparse public collected result. |
| `feed_variants.json` | `collection.species` nesting, singular media, nullable mystery media and plural postcard media; same species across different feeders. |
| `feed_postcard_minimal.json` | Routine FEED's ID/time/type-only NewPostcard selection. Missing media in this response is not proof the API has none. |
| `feed_empty.json` | Empty feed and terminal paging metadata. |
| `feed_incremental_pages.json` | Two small pages with overlap and equal timestamps. Use for retry/deduplication tests, then parameterize a larger batch when testing default page size. |
| `feed_malformed_timestamp.json` | Intentionally null non-null `createdAt`, for backend/schema-drift tolerance. |
| `feed_unknown_type.json` | Invented future union member, intentionally outside the current schema. |
| `media_expired.json` | Harmless placeholder URL with `Expires=1`, for image/cache invalidation. |
| `postcard_long_urls.json` | Twenty-four images plus a video; UTF-8 JSON exceeds 32KiB using synthetic URL padding. Use to design bounded complete events/retrieval, not to justify dropping assets. |

## Safe additions

1. Start with an already public sanitized excerpt or an explicitly synthetic payload. Never save a raw account response, token, signed media URL or downloaded asset in the repository.
2. Use one `FixtureSanitizer` instance for linked excerpts so matching IDs remain linked. It replaces known IDs, personal names, email/location fields, credentials and URLs while preserving selected shape. All fixture asset URLs use `https://example.invalid/...`.
3. Review every retained field, including free text and new schema fields. The sanitizer and `privacy_errors()` are mechanical guards, not a comprehensive personal-data detector. Remove identifying context that they do not understand.
4. Record provenance and intended selections in `manifest.json`; do not label synthetic additions to a capture as observed. Keep public partial responses partial and add a separate synthetic complete case when needed.
5. Update the schema projection from a verified introspection snapshot when new types/fields are needed. Do not make it permissive merely to silence a contract failure. Mark intentionally invalid robustness cases explicitly.
6. Run the fixture checks and relevant consumer tests. Fixture checks prove corpus safety/shape, not that the integration correctly handles it; later runtime tests must consume the same data through actual public boundaries.

The corpus has no account passwords, access/refresh/report tokens, actual signed URLs, live coordinates or downloaded media. Placeholder expiry parameters and padding are intentional test data. The generated synthetic dates are fixed; expiry tests use explicitly expired/far-future placeholders rather than accidental dependence on the current clock.
