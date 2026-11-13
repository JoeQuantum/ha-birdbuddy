"""Exercise the opt-in recipe without network or cloud collection."""

from email.message import Message
from io import BytesIO
from unittest.mock import Mock
from urllib.error import HTTPError

import pytest

from scripts.archive_postcard import archive, plan


def details(*kinds):
    return {
        "account_id": "account-a",
        "postcard_id": "postcard-a",
        "species": [],
        "medias_complete": True,
        "media_count": len(kinds),
        "medias": [
            {
                "id": f"../asset-{i}",
                "kind": kind,
                "content_url": f"https://example.invalid/{i}?secret=private",
            }
            for i, kind in enumerate(kinds)
        ],
    }


class Response(BytesIO):
    def __init__(self, data, mime="application/octet-stream"):
        super().__init__(data)
        self.headers = {"Content-Type": mime}


def test_preview_never_writes_or_fetches(tmp_path):
    opener = Mock()
    result = archive(
        details("MediaImage", "MediaVideo"), tmp_path / "not-created", opener=opener
    )
    assert [row["status"] for row in result] == ["planned", "planned"]
    assert all("/" not in row["filename"] for row in result)
    assert not list(tmp_path.iterdir())
    opener.assert_not_called()


def test_mixed_and_video_only_unknown_species_deduplicate(tmp_path):
    opener = Mock(
        side_effect=[Response(b"image", "image/jpeg"), Response(b"video", "video/mp4")]
    )
    detail = details("MediaImage", "MediaVideo")
    result = archive(detail, tmp_path, write=True, opener=opener)
    assert [row["status"] for row in result] == ["saved", "saved"]
    detail["postcard_id"] = "another-postcard"
    detail["medias"] = [detail["medias"][1]]
    detail["media_count"] = 1
    assert (
        archive(detail, tmp_path, write=True, opener=opener)[0]["status"]
        == "already_present"
    )
    assert opener.call_count == 2
    assert sorted(p.read_bytes() for p in tmp_path.iterdir()) == [b"image", b"video"]


def test_expired_url_partial_failure_and_fresh_detail_rerun(tmp_path):
    opener = Mock(
        side_effect=[
            HTTPError("private-url", 403, "expired", Message(), None),
            Response(b"video"),
        ]
    )
    detail = details("MediaImage", "MediaVideo")
    result = archive(detail, tmp_path, write=True, opener=opener)
    assert [row["status"] for row in result] == ["failed", "saved"]
    assert result[0]["reason"] == "refresh_details"
    detail["medias"][0]["content_url"] = "https://example.invalid/fresh"
    result = archive(
        detail, tmp_path, write=True, opener=Mock(return_value=Response(b"new-image"))
    )
    assert [row["status"] for row in result] == ["saved", "already_present"]
    assert len(list(tmp_path.iterdir())) == 2
    assert "private-url" not in str(result)


@pytest.mark.parametrize("mime,data", [("text/html", b"login"), ("image/jpeg", b"")])
def test_bad_response_leaves_no_partial_file(tmp_path, mime, data):
    result = archive(
        details("MediaImage"),
        tmp_path,
        write=True,
        opener=Mock(return_value=Response(data, mime)),
    )
    assert result[0]["status"] == "failed"
    assert not list(tmp_path.iterdir())


def test_byte_limit_cleans_partial_file(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.archive_postcard.MAX_FILE_BYTES", 2)
    result = archive(
        details("MediaVideo"),
        tmp_path,
        write=True,
        opener=Mock(return_value=Response(b"123")),
    )
    assert result[0]["status"] == "failed"
    assert not list(tmp_path.iterdir())


def test_incomplete_event_and_unknown_kind_are_explicit_failures():
    detail = details("MediaImage")
    detail["medias_complete"] = False
    with pytest.raises(ValueError, match="get_postcard_details"):
        plan(detail)
    with pytest.raises(ValueError, match="Unsupported media kind"):
        plan(details("UnknownMedia"))


def test_existing_symlink_and_empty_target_are_not_silent_success(tmp_path):
    detail = details("MediaImage", "MediaVideo")
    rows = plan(detail)
    (tmp_path / rows[0]["filename"]).symlink_to(tmp_path / "outside")
    (tmp_path / rows[1]["filename"]).touch()
    opener = Mock()
    assert all(
        row["status"] == "failed"
        for row in archive(detail, tmp_path, write=True, opener=opener)
    )
    opener.assert_not_called()


def test_truncated_download_is_not_committed(tmp_path):
    response = Response(b"short")
    response.headers["Content-Length"] = "10"
    result = archive(
        details("MediaImage"), tmp_path, write=True, opener=Mock(return_value=response)
    )
    assert result[0]["status"] == "failed"
    assert not list(tmp_path.iterdir())


def test_slow_stream_time_budget_leaves_no_partial_file(tmp_path, monkeypatch):
    values = iter([0, 0, 0, 61])
    monkeypatch.setattr("scripts.archive_postcard.monotonic", lambda: next(values))
    result = archive(
        details("MediaImage"),
        tmp_path,
        write=True,
        opener=Mock(return_value=Response(b"small-but-too-late")),
    )
    assert result[0]["status"] == "failed"
    assert result[0]["reason"] == "TimeoutError"
    assert not list(tmp_path.iterdir())
