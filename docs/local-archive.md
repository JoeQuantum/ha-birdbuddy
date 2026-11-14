# Save postcard photos and videos locally

Cloud collection and local file storage are separate actions. The integration's
`birdbuddy.get_postcard_details` response supplies every selected image/video URL,
including assets omitted from a recorder-sized event. This recipe only reads that
response and downloads files. It never identifies, collects or shares a postcard.

1. In Home Assistant Developer Tools, call `birdbuddy.get_postcard_details` with
   the event's `postcard_id` and `config_entry_id`. Request a response and save its
   JSON locally as `postcard-details.json`. Keep this file private: URLs can be signed.
2. Choose an existing destination directory. Copy the included
   `scripts/archive_postcard.py` to a computer with Python 3.12 or later.
3. Preview the stable filenames, then explicitly enable writes:

```sh
python3 scripts/archive_postcard.py postcard-details.json /path/to/bird-archive
python3 scripts/archive_postcard.py postcard-details.json /path/to/bird-archive --write
```

The script accepts either the detail response itself or HA's `service_response`
envelope. It rejects a compacted event, missing full-media URL, unsupported media
kind or inconsistent media count. Each filename hashes account ID plus media ID;
changing postcard ID, species name, language or signed URL does not duplicate an
asset. Photos use `.image` and videos `.video`, retaining the exact downloaded bytes
without assuming JPEG versus PNG or MP4 versus another video format. Open them with
an image/video application, or copy and rename after inspecting their actual format.

Writes are sequential and limited to 100 assets, 100 MB per asset and 500 MB per
invocation. A request/read has a 30-second socket timeout. The script also checks
60-second per-file and 10-minute invocation deadlines between chunks; an in-flight
read can take up to its socket timeout before the deadline is checked again. Files first download into private temporary files in the
selected directory. An atomic no-overwrite operation publishes each completed
file. Cancellation/error removes the partial file; successes remain available.
Empty responses, wrong content types and truncated declared lengths fail visibly.
Symlinks and empty existing destinations are not counted as successful archives.

The JSON result lists each asset as `saved`, `already_present`, `planned` or
`failed`; any failure gives the command a nonzero exit status. It contains no signed
URLs. A 401/403/404/410 response says `refresh_details`: request the postcard's full
details again, replace the input JSON and rerun. Existing successful assets are
skipped. If the vendor has already removed the media, the script cannot recover it;
a missing or replaced postcard ID is not proof of collection success.

This is an explicit local recipe, not a background Home Assistant downloader.
Ordinary polling creates no archive directory and writes no media. It requires no
HA long-lived access token in the script and no Bird Buddy credentials. The
synthetic test suite covers multi-image/video, unknown species, video-only reruns,
expired URLs, partial success, truncated content, byte limits and no-overwrite
behavior. No actual user media was downloaded during development.

## BB-31 decision

The recipe covers the requested file workflow using the complete detail action,
stable filenames and explicit reruns with fresh URLs. No concrete gap currently
justifies another storage subsystem inside the integration. A first-class archive
action is deferred; revisit it if unattended destination management or another
specific workflow cannot reasonably use this recipe. That future action must keep
cloud collection/sharing independent and preserve the same bounded download,
cancellation, idempotence and partial-failure guarantees.
