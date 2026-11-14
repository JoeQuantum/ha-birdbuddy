# Optional account insights

`birdbuddy.get_account_insights` reads optional vendor data when explicitly called.
It requires a response variable in a script or automation; Developer Tools can
show the returned response directly. It creates no entities, background polling,
remote connections, or mutations. Ordinary feeder polling never requests these
insights.

```yaml
- action: birdbuddy.get_account_insights
  data:
    kind: weekly_recap
    config_entry_id: YOUR_BIRD_BUDDY_CONFIG_ENTRY_ID
  response_variable: birdbuddy_recap
```

Select one configured account with `config_entry_id` or its vendor `account_id`.
Every supplied selector must agree. Multiple accounts require unambiguous routing,
including shared feeders. The service checks the account before and after the
read and refuses a response if the account was unloaded, reloaded, or changed.
Authentication failures start the selected account’s reauthentication flow.

| `kind` | Selected data | Additional parameters | Cache |
| --- | --- | --- | --- |
| `hardware` | Owner/member/pending roles, hardware/capabilities, explicit locks, premium role/type/active flag, configured profile, Smart Energy running state/profile, owner video quality/camera field of view | Optional `limit` | 5 minutes |
| `preferences` | Ignored species IDs and mystery-visitor preference | Optional `limit` | 5 minutes |
| `species` | Stable species ID, localized/scientific name, taxonomy, icon and selected exact-species name flags | Required `species_id`; omit `limit` | 24 hours |
| `named_birds` | Individual IDs/nicknames, recognition state, relationship/personality, species, vendor visit statistics and sample thumbnails | Optional `limit` | 15 minutes |
| `weekly_recap` | Vendor-selected ISO week/year, visit/observation counts, daily/feeder/species summaries, mystery visits and streaks | Optional `limit` | 1 hour |
| `yearly_review` | Vendor-selected year, counts/percentiles, monthly/species and individual-bird summaries | Optional `limit` | 1 hour |
| `feeder_report` | Account or selected feeder report, authoritative period and visit metrics | Optional `feeder_id`, paired `start`/`end`, `limit` | 1 hour |
| `remote_connections` | Expiring remote connection metadata, tier/type and connected/unlocked flags | Optional `limit` | 5 minutes |

`limit` is an integer from 1 to 100, defaulting to 50 for list resources. It bounds
selected nested lists locally; these API fields offer no page cursor. Explicit
`limit` is rejected for a single-species lookup. Unused parameters are rejected:
`species_id` is only for `species`; `feeder_id`, `start`, and `end` are only for
`feeder_report`. There is no device selector or arbitrary user-ID input.

A feeder report’s date range must supply both timezone-aware ISO datetimes, with
end later than start and at most 366 days apart. That local limit does not promise
historical retention. The weekly/yearly API does not accept a week/year argument.
Always use the period returned by the vendor.

Omitting the range asks the vendor to select a period; it does not request a
guaranteed rolling or lifetime report. A server error for one feeder or period
does not establish zero visits, an offline feeder, or a retention cutoff. The
service raises a safe error instead of returning another period or fabricated
counts. Failed reads are not cached, and successful reports for other periods
remain usable. Only authentication failures start reauthentication.

The response includes `schema_version: 1`, `config_entry_id`, `account_id`,
`resource`, `status`, `data`, `complete`, `limit`, `pagination`, explicit paths for
truncated/missing/unknown fields, `fetched_at`, and `cache_ttl_seconds`. Known missing
or insufficient recaps return an unavailable status, not invented zero counts.
`complete` describes the selected projection; callers must still handle nullable
fields. Named-bird `STOPPED` state and missing statistics stay explicit. Individual
counts are distinct from species totals. Stable species IDs can remain useful
across language changes without implying recognition accuracy or exact taxonomy.

Smart Energy’s running profile is separate from the configured power profile.
`NOT_SUPPORTED` is distinct from disabled. Feature locks do not supply their cause;
subscription metadata does not prove an undocumented entitlement rule. Reported
video quality and camera field of view do not enable controls. Remote connections
are account metadata, never owned physical devices. Their expiry is recomputed
when cached data is returned, and unknown expiry remains unknown. Account features
can be requested with zero physical feeders if the vendor permits them.

`supportsLivestreamSnapshot` is an enum: `SUPPORTED`, `UNAVAILABLE`, or
`REQUIRES_FIRMWARE_UPDATE`. It is not a Boolean; testing whether its string is
nonempty would incorrectly treat every value as support. Unknown future values
are preserved without claiming support. These metadata reads do not start a
snapshot or camera session.

Cloud polling success does not prove fresh physical telemetry. Last Sync records
the successful account poll; a feeder's `presenceUpdatedAt` is a separate vendor
timestamp and is not guaranteed to track every metric or state change. The
incubating temperature and food sensors remain disabled by default; metadata
availability alone does not establish measurement accuracy.

One SDK adapter is created lazily per loaded account. Its bounded cache and
coalesced reads avoid duplicate requests. No force-refresh input bypasses the
TTLs. Unload cancels and awaits shared requests and clears cached account data.
If the client language changes during a read, `InsightLanguageError` prevents
mismatched data from being cached; retry after choosing a stable language. Public
service errors contain a safe error type rather than raw vendor response text.

The SDK bounds selected results, list lengths, cache bytes and concurrent reads;
exceeding a resource limit returns an error instead of silently cutting IDs or
URLs. Sample thumbnail addresses are temporary references, not archive URLs.
Billing details, account location/email, feeder coordinates/serials, invitation
or authentication tokens, and report-token material are not selected.

## Filter notifications by stable species ID

Use a species ID actually returned in your postcard details; do not infer an ID
from a translated name. For example, if Bird Buddy identifies a squirrel or another
visitor you want to exclude from notifications, copy its returned `species.id`.
Insert this action immediately after the `get_postcard_details` action in the
[notification example](../examples/notify_postcard.yaml), before its variables
and notification actions:

```yaml
- condition: template
  value_template: >-
    {% set ignored_ids = ['REPLACE_WITH_RETURNED_SPECIES_ID'] %}
    {{ postcard_details.species
       | selectattr('id', 'in', ignored_ids) | list | count == 0 }}
```

This skips a notification when any recognized species matches an ignored ID.
Unidentified visits still pass; add `postcard_details.species | count > 0` if you
also want to suppress them. Fetch complete details before filtering, because a
large event can omit its species list. Language changes alter display names, not
this ID comparison. The filter only controls this automation's notification; it
does not discard a postcard, change global ignored-species preferences or promise
that the vendor recognizes every squirrel or other visitor correctly.

The queries validate against the schema captured on 2026-11-13. Repository tests
use synthetic fixtures and mocked requests, including report failures and
capability enums. These offline checks do not establish account entitlement,
physical hardware behavior, report retention, or current availability. Standalone
API checks and live HA installation/lifecycle checks are separate validation
steps; account-specific evidence belongs in private release records.
