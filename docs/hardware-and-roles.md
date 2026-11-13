# Hardware and account-role evidence

This matrix records offline compatibility evidence as of **2026-11-13**.
No devices were authenticated, controlled, or updated during this review.
No model, firmware, subscription, or role combination is certified by a live
hardware test here. A supported GraphQL value establishes recognition, not
proof that every entity or command works on a physical product.

<!-- markdownlint-disable MD013 -->

## Hardware recognition

| API value | Evidence | Integration behavior |
| --- | --- | --- |
| `V1`, `V1_PRO`, `V2` | Existing client support; offline metadata tests | Hardware generation preserved independently from firmware |
| `V2_NOOK` | Live schema; synthetic fixture and offline tests | Recognized generation; Nook behavior remains unverified |
| `CLASSIC` | Live schema and offline tests | Model label: Birdbuddy Feeder |
| `HUMMINGBIRD` | Live schema and offline tests | Model label: Smart Hummingbird Feeder |
| `BIRD_BATH` | Live schema and offline tests | Model label: Smart Bird Bath |
| Missing or future generation/housing | Adversarial offline tests | Stable device identity; generic model and unknown metadata |

<!-- markdownlint-enable MD013 -->

Generation and housing are separate fields. Testing their combinations confirms
that metadata is kept independent; it does not assert that every combination is
sold or physically possible. The hardware label comes from `version`; installed
software comes from `firmwareVersion`.

<!-- markdownlint-disable MD013 -->

## Roles and account boundaries

| Account shape | Offline behavior |
| --- | --- |
| Owner | Read entities; audio, off-grid, selected power profile and firmware controls |
| Accepted member | Available read entities; no owner controls or invented owner metadata |
| Pending member | Identity retained; no operational entities until access is accepted |
| Empty account | Setup stays loaded and can discover feeders later |
| Shared feeder in two accounts | Independent account snapshots; explicit account routing when ambiguous |
| Membership removed or role downgraded | Old owner fields removed; entities become unavailable without deleting registry identity |
| Public feeder | Not treated as an account-owned feeder or granted owner controls |

<!-- markdownlint-enable MD013 -->

The same physical feeder can have different roles and payloads in different
accounts. Collection services validate every supplied account/device/feeder
constraint. A device or feeder ID alone cannot safely choose between multiple
eligible accounts. Use `config_entry_id` or `account_id` in automations.

## Capability and telemetry limits

- Audio is suppressed when `supportsAudio` is explicitly false. A missing value
  remains unknown; it is not inferred from the model, housing, or subscription.
- Owner role is required before device controls. Backend restrictions still
  apply; local visibility does not promise a successful device action.
- Power Profile represents the configured `powerProfile`. Smart Energy has a
  separate `runningPowerProfile` field on `FeederSmartEnergyEnabled`; this core
  implementation neither selects nor infers that running state.
- The schema includes Ultra Frenzy and Smart Energy structures. That does not
  establish plan eligibility, device support, or effective behavior. No paid
  subscription requirement is inferred solely from those names.
- Missing battery, signal, charging and temperature readings stay unknown.
  Missing food metrics stay unknown. Explicit zero readings remain zero.
  Food and Temperature remain experimental and disabled by default.
- Firmware installation requires a fresh owner/availability/precondition check.
  The Home Assistant action requests an immediate update and monitors it for a
  bounded period. An uncertain result is not an invitation to retry blindly.

## Evidence and future verification

`tests/test_hardware_matrix.py` exercises metadata, nullable capabilities,
account roles and independent shared-feeder snapshots. The lifecycle, routing,
control and firmware suites cover discovery, removal, ambiguity and failures.
Fixture origins are recorded in `tests/fixtures/manifest.json`; synthetic data
must never be relabeled as a real device capture. Public owner examples were
resanitized from an upstream test capture, not captured from the user's account.

The source contract is the schema captured without authentication at
`2026-11-13T19:09:10Z`, plus the pinned client query documents. The upstream
[hardware issue](https://github.com/jhansche/ha-birdbuddy/issues/120) and
[shared-account issue](https://github.com/jhansche/ha-birdbuddy/issues/32)
provide context, not certification for this fork.

Any later hardware verification needs a separately authorized, read-only
capture. Record date, firmware, reported generation/housing, account role and
known subscription context without names, account IDs, device identifiers,
locations, credentials or media URLs. Missing context must be recorded as
unknown. Nook, Smart Energy, subscription-dependent behavior and physical
firmware/control outcomes remain outside the verified hardware subset.
