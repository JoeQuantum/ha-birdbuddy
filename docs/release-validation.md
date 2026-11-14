# Core 0.2.0 release validation

This record separates offline candidate validation from the subsequent temporary
HA read-only canary. Neither is evidence of a published release or production
deployment.
The candidate pairs integration **0.2.0** with immutable client commit
`593bf8df89ef04291a79004df9bfd89c30d1889d`. The offline checks below did not
authenticate or perform collection, inference, sharing, device control or
firmware installation. The later live read-only canary has its own scope below.

## Upgrade and storage checks

`tests/test_upgrade_restore.py` starts a real Home Assistant integration entry
with mocked network boundaries and a premodern version-1/email-based identity.
It uses real Home Assistant device/entity registries and temporary private
`Store` files. Setup, unload, setup again and `async_reload` preserve:

- Config-entry ID, email unique ID, user title, options and existing data.
- Device ID, user device name, existing entity IDs and user-disabled settings.
- The added authenticated account UUID without increasing entry version.
- Historical processed identities, a pending event and the accepted checkpoint.
- A recorded uncertain mutation outcome, which continues to block repetition.
- An action interrupted by real entry unload: its caller is cancelled, the
  outcome stays unknown on disk, and setup/reload does not replay it.

The focused upgrade test and five package integrity tests pass on the exact
minimum, modern and current HA/client environments. Runtime checks use the
installed immutable client, without a source-path dependency override.
A separate shutdown regression checks that cancelled work from an unloaded
coordinator cannot write over a newly loaded coordinator's store.

The upgrade test exercises a premodern-shaped entry, not a real historical
account session. Its rollback interval leaves the integration unloaded and
preserves the new store before loading current code again. A separate
filesystem smoke restores the actual premodern integration source and then the
candidate while leaving `.storage` untouched. Historical cloud API operations
are not executed or claimed to work after rollback.

## Artifact checks

The local build includes the component, translations, Repairs, collection
blueprint, migration and support documentation, notification example and offline
archive/verification scripts. `scripts/package_release.py` validates required
nonempty files, Python syntax, JSON, immutable dependency pin and every file's
SHA256. Its deterministic ZIP can be rebuilt for a reproducibility comparison.

Extracted-artifact validation imports every integration module from the
extraction directory under the exact current HA runtime. It checks each module's
resolved path and the installed client's direct-URL commit metadata; importing
the source checkout by accident does not count. The result, archive path and
SHA256 are recorded alongside the candidate artifact, outside this document
so embedding a checksum does not change the package it describes.

The final archive must be rebuilt and rechecked whenever a packaged file changes.
Run the complete compatibility matrix against the same final client pin before
publication. The [development matrix](../DEVELOPMENT.md) identifies exact HA,
Python and harness versions; full-suite results belong in the final release
record in addition to this focused smoke evidence.

## Rollback procedure and limits

1. Stop collection automations and back up the complete HA configuration and
   `.storage` before installing the candidate.
2. For a code rollback, stop Home Assistant, restore the complete chosen
   integration directory and its matching client dependency, then restart.
   Do not merge individual old/new Python files or remove the config entry.
3. Keep the new private `birdbuddy.<entry_id>.intake` file. Older code does not
   use it. Removing it loses durable deduplication and uncertain-action evidence
   when upgrading again. Keep its checkpoint and queue together.
4. Restore registry/config files only from a coordinated backup. An older
   backup cannot undo effects that already happened in Bird Buddy or another
   notification/archive system.
5. On re-upgrade, verify the original entry/account identities and inspect
   pending/dead-letter/unknown-action diagnostics before resuming automations.

Code rollback cannot reverse cloud collection, sharing, inference or firmware.
It cannot restore compatibility with an API that changed independently. The
old report-token collection automation must remain disabled unless its external
behavior has separately been verified. Reverting code is not an instruction to
replay unknown cloud actions.

## Temporary HA and Marketplace canary

On 2026-11-13, a separate localhost-only HA 2026.11.1 development instance
installed the core candidate through the built-in Marketplace at integration
revision `034ce58238b6a0021366a06cb7a9683a7ef1cd6a`, with the exact core client
pin above. Marketplace then upgraded it to optional integration revision
`e69816f886b21229a2cd311858e1953a5a9519e0`, with client
`80574d823c5c7e32f711c2ff9dfb77737c386110`. Installed component files were compared
byte-for-byte with both Git revisions before use.

A real email/password account completed setup, telemetry and postcard intake.
Both candidates passed reload/unload/re-enable, full process restart, diagnostics,
postcard details, collection browsing, media resolution, image retrieval and
recorder-history checks. The core-to-optional upgrade preserved the entry,
device/entity registry identities, disabled controls and durable intake. Real
token renewal succeeded through each HA coordinator without another password
sign-in. The lab cleared only the in-memory access token to trigger that check.

All eight optional insight resources were exercised through HA's response-only
service, including typed unavailable recap handling, cache reuse and rejection
of invalid requests before cloud access. A historical short report still returned
a vendor error; it remained separate from a successful cached full-day report and
did not invalidate account telemetry or trigger reauthentication.

The lab blocked all Bird Buddy mutations except authentication exchanges, disabled
control entities and configured no collection automations. No hardware or
postcard action was tested. Private account data, credentials and raw responses
are excluded from public evidence. The temporary runtime is a macOS development
environment and is not a production installation.

HA 2026.11 replaces HACS with the built-in
[Marketplace](https://www.home-assistant.io/integrations/marketplace/). This run
validates that current install path. It does not validate a legacy HACS-to-
Marketplace migration with existing user data.

## Final offline review

The follow-up PR review fixed three shared failure paths: account shutdown now
cancels and drains Media Browser reads; core service errors omit raw backend
payloads and exception chains; firmware authentication failures start
reauthentication immediately instead of consuming the progress retry budget.
Firmware monitoring still preserves the last reported server progress and does
not imply that the physical update stopped.

Fifteen regression cases reproduce these failures using mocked network responses,
including collection and identification failures before any mutation. These
changes follow the exact live-tested revisions above and are validated offline;
no additional Bird Buddy requests or physical actions were performed for this
review.

## Remaining release gates

The existing home installation still needs backup, actual 0.1.x upgrade and
household-specific lifecycle/automation validation. Physical device actions,
collection/sharing, firmware success, supported Smart Energy/camera behavior and
vendor report availability limits remain unverified. Read-only cloud and lab
evidence does not satisfy those gates. See the
[hardware evidence matrix](hardware-and-roles.md).

This workflow prepares a reviewable candidate only. Publishing a release,
pushing a tag, merging changes or installing it on the user's HA instance is
outside these smoke checks.
