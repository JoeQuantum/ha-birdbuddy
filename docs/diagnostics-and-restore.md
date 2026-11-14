# Diagnostics, installation and restore

Download diagnostics from the Bird Buddy integration entry in Home Assistant.
The export contains installed versions, telemetry/feed success times, feeder role
counts, checkpoint size, queued/dead-letter event counts and unknown action counts.
It deliberately omits account/feeder/postcard IDs, names, credentials, report tokens,
media URLs, location and raw API responses. Unknown exception names become `other`.
Diagnostics work for an unloaded entry, with version information only.

## Choose the right recovery

| Symptom | Meaning and next step |
| --- | --- |
| Invalid credentials or expired account authentication | Use Home Assistant's reauthentication flow. It verifies the existing account UUID and preserves entity/device IDs. Social-only sign-in is not supported by this client. |
| HTTP timeout, rate limit or transient cloud failure | Allow the bounded read retry and next poll. A rate limit may include a retry delay. Changing your password does not repair a cloud outage. |
| Postcard intake repair, with working battery/state entities | Check the safe error category and queue counts. Intake storage or pagination failed; the accepted checkpoint is not advanced past an unpersisted batch. Resolve storage capacity/permissions or cloud problems first. |
| Dead-letter event count | Investigate the failing event data, then explicitly call `birdbuddy.retry_feed_items`. Re-emitted events can trigger your automations again. |
| Unknown postcard action count | Check the Bird Buddy app before retrying. A cloud action may have completed. `birdbuddy.acknowledge_postcard_retry` with `confirm: true` only permits a later separate request; it does not itself collect or identify. |
| ImportError, missing class/module, empty Python file after restore | Check the integration and dependency installation. Restore/reinstall the complete matching package and immutable client dependency, then restart. Do not erase your integration entry or change credentials to fix damaged files. |
| Stale cloud battery/state | Last Sync is the last successful cloud telemetry poll. Polling cannot force camera uploads, change solar charging behavior, or establish the device's physical state between uploads. |

## Verify a candidate artifact

From a checkout, build and check a manual-install archive:

```sh
python3 scripts/package_release.py /tmp/birdbuddy-candidate.zip --build-from .
python3 scripts/package_release.py /tmp/birdbuddy-candidate.zip
```

The archive contains `custom_components/birdbuddy/`, documentation and blueprints.
The checker requires an exact 40-character client commit, validates Python/JSON,
and checks required files, nonempty integration files and SHA256 hashes. Hashes
check file integrity; they do not authenticate a publisher. The build is local and
does not publish, install or change Home Assistant. HACS continues to use the
repository layout and minimum Home Assistant version declared in `hacs.json`.

For a real installation, additionally import the extracted component against its
exact supported Home Assistant runtime and installed client requirement. The
`ha_runtime_smoke.py` script performs this import check in a prepared environment;
it does not sign in or communicate with Bird Buddy. The release gate records its
matrix and archive hash separately from hardware/account canary evidence.

## Preserve state during upgrades and rollback

Back up Home Assistant configuration and `.storage` before installing a candidate.
Keep the entry ID, its email-based unique ID, and device/entity registry intact.
The verified vendor account UUID is an additional config-entry field; entry version
remains 1. The new private `birdbuddy.<entry_id>.intake` store is separate from the
legacy integration's storage and contains bounded queues, checkpoints and action
receipts. Do not edit the checkpoint independently of its queue.

An older integration ignores this new store. Preserve it if temporarily reverting
code: deleting it loses deduplication and unknown-outcome protections when you
upgrade again. Rollback does not undo cloud collections, sharing, inference, or
firmware installation. Old report-token collection automations remain incompatible
with the changed cloud API even after a local rollback. Restore the old automation
configuration only when its external API behavior has been independently verified.
