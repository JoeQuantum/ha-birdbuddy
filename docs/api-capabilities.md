# API capability and deferred-work register

Reviewed 2026-11-13 against the production GraphQL schema and both repository
histories. The schema has **51 queries, 115 mutations, one subscription and 568
types**. A published field confirms a schema contract; it does not prove account
permission, device support, transport behavior, retention or mutation idempotence.
The integration remains cloud polling. This register describes the development
candidate, not a claim that every vendor API operation is implemented.

The companion [operation disposition inventory](api-operation-dispositions.json)
contains all 167 roots, their exact signatures, domain, task references, deprecation
status and original review disposition. Its `client_status` records the API-catalog
baseline; the implementation status below is the current development decision.

| API domain | Roots | Current disposition |
| --- | ---: | --- |
| Account | 15 | Email authentication, refresh and verified reauth in core. Social exchange/account lifecycle remain deferred; account deletion excluded from ordinary telemetry. |
| Alexa | 6 | Account/device administration intentionally excluded. |
| Collections | 14 | Account-scoped collection/media browsing and refreshed URLs in core. Other curation/community operations remain explicit future work. |
| Commercial | 7 | Billing, purchase, cancellation and referral administration intentionally excluded. |
| Community | 6 | Schema-only groundwork; remote account metadata evaluated separately. No sharing, rotation or public-community fetch during ordinary polling. |
| Feeder controls | 17 | Audio, off-grid and selected power profile use bounded core controls. Optional hardware insights report Smart Energy status and camera quality/FOV metadata. Smart Energy writes and camera controls require hardware evidence. |
| Inference conversations | 5 | Conversational AI/image inference outside the ordinary telemetry scope; no automatic calls. |
| Legacy sightings | 12 | Retained only as client compatibility surface where still present. Integration uses modern explicit identify/collect; unsupported old strategy/report-token actions receive migration guidance. |
| Livestream | 10 | Runtime workflow unresolved; no camera entity/session mutation. See [research decision](push-and-camera-research.md). |
| Locations | 4 | Location administration/discovery not needed for current HA features. Precise location is excluded from diagnostics/events. |
| Media | 12 | Complete selected postcard media and account collection resolution supported. Local archive is an explicit recipe; uploads/voting/curation remain excluded from automatic polling. |
| Membership | 10 | Read/reconcile owner/member/pending role and availability in core. Invitation, access-grant and member-removal administration deferred. |
| Named birds | 6 | Opt-in read-only identity/recognition/statistics implemented in account insights. No rename or recognition-toggle mutation without separate acceptance evidence. |
| Postcards | 8 | Read-only detail, modern explicit identification/collection, bounded events and durable intake in core. Discard, feedback and assignment policies require separate validated semantics. |
| Profile/runtime | 9 | Verified account identity used in core; feature locks/preferences available through optional reads. Heartbeat is unresolved, notification registration/beta toggles excluded from automatic refresh. |
| Provisioning/firmware | 12 | Bounded firmware workflow in core. Pairing, factory reset and other provisioning administration excluded. |
| Remote feeders | 6 | Optional account-only connection metadata implemented. Never treated as owned hardware; connect/switch/disconnect remain explicit future work. |
| Reports | 3 | Optional cached read-only vendor analytics implemented; unavailable results remain normal data. |
| Species | 5 | Stable IDs and recognized/unknown species in core. Rich taxonomy/preferences reads implemented in account insights; no global ignore mutation from a sensor/notification filter. |

## Explicit exclusions and gates

- **Reboot/local API:** unsupported until a real operation/protocol is demonstrated.
  Factory reset and off-grid toggling are not substitutes for reboot.
- **Push:** BB-33 concluded transport and personal account scope unresolved. BB-34
  is deferred, and durable polling remains authoritative.
- **Camera:** BB-35 needs an authorized selected feeder/account, protocol evidence
  and reliable cleanup. BB-36 is deferred. Schema enums do not become selectable
  camera-quality/FOV options without hardware evidence (BB-27).
- **Smart Energy:** read status can be modeled independently. Its configured
  profile and actual running profile must stay separate; writes remain gated.
- **Archive:** the [tested local recipe](local-archive.md) fills the current gap.
  A first-class archive subsystem is deferred until a concrete unmet need exists.
- **Optional reads:** no additional background requests or unbounded entity
  creation. The [account insights service](account-insights.md) implements eight
  selected resources with bounded caches. Repository tests validate schema
  contracts and synthetic results; account access, entitlement and availability
  require separately scoped evidence and are not universal capability guarantees.
- **Account canary:** no live authentication, cloud mutation or physical hardware
  behavior is inferred from fixture tests. Release records must retain this limit.

## Review coverage and future upkeep

The workspace review catalog covers all **56 upstream issues and 63 upstream PRs**
that existed at the review snapshot, including closed/merged items, comments and
relevant diffs. Every one has a task mapping or a documented reason no work is
needed in `development-plan-2026-11-13/issue-coverage.json` and `pr-coverage.json`.
Upstream version numbers are independent of this fork; changes were selected for
behavior, not copied as a blind merge. The companion client review covers the
modern API migration, pagination, account-role and stream discussions as well.

During a future release review, compare upstream heads and refresh the rich schema
introspection deliberately, without login. Compare exact roots, argument/default
changes, nullability, union members and enum values against stored provenance.
Keep normal unit tests offline; an optional manual schema refresh must not become
a network dependency of CI or the integration poll. Reopen a deferred item when
new evidence or user priorities resolve its explicit gate, then add targeted
fixtures and record the new immutable client revision.
