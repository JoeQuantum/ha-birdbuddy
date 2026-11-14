"""Explicit local archive recipe for a complete birdbuddy.get_postcard_details response.

Run with --write to download; without it the command only validates and plans.
Use a fresh detail response and rerun after an expired URL failure. No Bird Buddy
credentials, collection calls or Home Assistant installation are required here.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
from http.client import IncompleteRead
import json
import os
from pathlib import Path
import tempfile
from time import monotonic
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, build_opener

MAX_FILES = 100
MAX_FILE_BYTES = 100_000_000
MAX_TOTAL_BYTES = 500_000_000
MAX_FILE_SECONDS = 60
MAX_TOTAL_SECONDS = 600


class _HttpsRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlsplit(newurl)
        if parsed.scheme != "https" or parsed.username or parsed.password:
            raise ValueError("Media redirect must remain HTTPS without credentials")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def plan(detail: dict[str, Any]) -> list[dict[str, str]]:
    """Require complete data and use opaque stable identities for filenames."""
    account_id = detail.get("account_id")
    if not isinstance(account_id, str) or not account_id:
        raise ValueError(
            "A complete account-scoped postcard detail response is required"
        )
    medias = detail.get("medias")
    if detail.get("medias_complete") is not True or not isinstance(medias, list):
        raise ValueError("Use get_postcard_details, not an event with omitted media")
    if detail.get("media_count") != len(medias) or len(medias) > MAX_FILES:
        raise ValueError("Incomplete media list or archive file budget exceeded")
    result = {}
    for media in medias:
        if not isinstance(media, dict):
            raise ValueError("Malformed media item")
        media_id, kind = media.get("id"), media.get("kind")
        if not isinstance(media_id, str) or not media_id:
            raise ValueError("Media requires a stable ID")
        suffix = (
            {"MediaImage": "image", "MediaVideo": "video"}.get(kind)
            if isinstance(kind, str)
            else None
        )
        if suffix is None:
            raise ValueError("Unsupported media kind; inspect the detail response")
        url = media.get("content_url")
        if not isinstance(url, str) or not url:
            raise ValueError("Full media URL missing; request fresh postcard details")
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            raise ValueError("Full media requires an HTTPS URL without credentials")
        # No species, timestamp, signed URL, user filename, or path component is
        # interpreted as a local path. Same media in another postcard deduplicates.
        digest = sha256(
            json.dumps([account_id, media_id], separators=(",", ":")).encode()
        ).hexdigest()
        record = {
            "media_id": media_id,
            "kind": kind,
            "filename": f"{digest}.{suffix}",
            "url": url,
        }
        if media_id in result and result[media_id]["kind"] != kind:
            raise ValueError("Conflicting kinds for the same media ID")
        result.setdefault(media_id, record)
    return list(result.values())


def archive(
    detail: dict, destination: Path, *, write: bool = False, opener=None
) -> list[dict]:
    """Download sequentially with no overwrites; retain successes on partial failure."""
    records = plan(detail)
    if not write:
        return [
            {
                "media_id": row["media_id"],
                "filename": row["filename"],
                "status": "planned",
            }
            for row in records
        ]
    destination = destination.expanduser().resolve(strict=True)
    if not destination.is_dir():
        raise ValueError("Select an existing archive directory")
    open_url = opener or build_opener(_HttpsRedirect()).open
    outcomes = []
    total = 0
    deadline = monotonic() + MAX_TOTAL_SECONDS
    for row in records:
        target = destination / row["filename"]
        outcome = {"media_id": row["media_id"], "filename": target.name}
        # A symlink or empty/corrupt target must not silently count as archived.
        if target.is_symlink():
            outcomes.append(
                outcome | {"status": "failed", "reason": "unsafe_existing_path"}
            )
            continue
        if target.exists():
            outcomes.append(
                outcome
                | {
                    "status": "already_present"
                    if target.is_file() and target.stat().st_size > 0
                    else "failed",
                    "reason": "existing_destination",
                }
            )
            continue
        temporary = None
        try:
            if total >= MAX_TOTAL_BYTES:
                raise ValueError("Archive byte budget exhausted")
            if monotonic() >= deadline:
                raise TimeoutError("Archive time budget exhausted")
            file_deadline = min(deadline, monotonic() + MAX_FILE_SECONDS)
            with open_url(row["url"], timeout=30) as response:
                mime = (
                    response.headers.get("Content-Type", "")
                    .split(";", 1)[0]
                    .strip()
                    .lower()
                )
                family = "image/" if row["kind"] == "MediaImage" else "video/"
                if not mime.startswith(family) and mime != "application/octet-stream":
                    raise ValueError("Unexpected media response type")
                size = 0
                with tempfile.NamedTemporaryFile(
                    dir=destination, prefix=".birdbuddy-", delete=False
                ) as output:
                    temporary = Path(output.name)
                    while chunk := response.read(64 * 1024):
                        if monotonic() >= file_deadline:
                            raise TimeoutError("Media time budget exhausted")
                        size += len(chunk)
                        total += len(chunk)
                        if size > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
                            raise ValueError("Archive byte budget exceeded")
                        output.write(chunk)
                    if monotonic() >= file_deadline:
                        raise TimeoutError("Media time budget exhausted")
                    if size == 0:
                        raise ValueError("Empty media response")
                    declared = response.headers.get("Content-Length")
                    if declared is not None and int(declared) != size:
                        raise ValueError("Truncated media response")
                    output.flush()
                    os.fsync(output.fileno())
                # Hard-link creation is atomic and never replaces a concurrent
                # writer's destination. The temporary file stays on this filesystem.
                os.link(temporary, target)
                outcomes.append(outcome | {"status": "saved", "bytes": size})
        except FileExistsError:
            outcomes.append(
                outcome | {"status": "already_present", "reason": "concurrent_writer"}
            )
        except HTTPError as err:
            outcomes.append(
                outcome
                | {
                    "status": "failed",
                    "reason": "refresh_details"
                    if err.code in {401, 403, 404, 410}
                    else "http_error",
                    "http_status": err.code,
                }
            )
        except (OSError, ValueError, IncompleteRead) as err:
            # Never serialize exception text; urllib errors can contain signed URLs.
            outcomes.append(
                outcome | {"status": "failed", "reason": type(err).__name__}
            )
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return outcomes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("details", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    detail = json.loads(args.details.read_text())
    # The input can be the service response alone or HA's response envelope.
    detail = detail.get("service_response", detail)
    results = archive(detail, args.destination, write=args.write)
    print(json.dumps(results, indent=2))
    if any(row["status"] == "failed" for row in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
