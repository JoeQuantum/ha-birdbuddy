"""Guard the reusable data corpus, without asserting broken runtime behavior."""

import json

import pytest

from . import load_fixture, load_manifest, validate_fixture_shape
from ._support import privacy_errors
from .sanitize import FixtureSanitizer

_MANIFEST = load_manifest()


@pytest.mark.parametrize("name", sorted(_MANIFEST["fixtures"]))
def test_selected_payload_shape_matches_declared_provenance(name):
    """Valid selected fields match the schema; adversarial exceptions are explicit."""
    metadata = _MANIFEST["fixtures"][name]
    if metadata.get("schema_valid", True):
        validate_fixture_shape(name)
    else:
        with pytest.raises(ValueError, match=metadata["expected_schema_error"]):
            validate_fixture_shape(name)


@pytest.mark.parametrize("name", sorted(_MANIFEST["fixtures"]))
def test_fixture_has_no_credential_values_or_external_asset_urls(name):
    assert not privacy_errors(load_fixture(name))


def test_public_analysis_preserves_mixed_media_and_partial_collection():
    """Real selected shapes include both rich analysis and sparse collect results."""
    analysis = load_fixture("public_postcard_analysis.json")
    assert [asset["__typename"] for asset in analysis["medias"]].count(
        "MediaImage"
    ) == 5
    assert [asset["__typename"] for asset in analysis["medias"]].count(
        "MediaVideo"
    ) == 1
    assert analysis["sightingReportPreview"]["sightings"][0]["species"]["name"]
    partial = load_fixture("public_collected_partial.json")
    assert len(partial["medias"]) == 11
    assert all(set(asset) == {"id", "__typename"} for asset in partial["medias"])


def test_roles_and_shared_feeder_identity_remain_distinct():
    """A member does not acquire owner fields just because another account owns it."""
    accounts = load_fixture("accounts_shared_feeder.json")
    owner = accounts[0]["feeders"][0]
    member = accounts[1]["feeders"][0]
    assert accounts[0]["user"]["id"] != accounts[1]["user"]["id"]
    assert owner["id"] == member["id"]
    assert owner["__typename"] == "FeederForOwner"
    assert member["__typename"] == "FeederForMember"
    assert "powerProfile" not in member
    assert member["battery"] is None
    pending = load_fixture("account_member_pending.json")["feeders"][1]
    assert set(pending) == {"__typename", "id", "name"}
    assert load_fixture("account_empty.json")["feeders"] == []


def test_feed_boundary_cases_are_not_flattened_into_one_media_shape():
    nodes = [edge["node"] for edge in load_fixture("feed_variants.json")["edges"]]
    first = nodes[0]
    assert first["collection"]["species"]["name"]
    assert "species" not in first
    assert "media" in first and "medias" not in first
    assert nodes[3]["media"] is None
    assert len(nodes[4]["species"]) == 2
    assert {asset["__typename"] for asset in nodes[4]["medias"]} == {
        "MediaImage",
        "MediaVideo",
    }


def test_unknown_species_and_video_only_are_independent_from_confidence():
    unknown = load_fixture("postcard_unrecognized.json")
    assert unknown["inferenceConfidenceLevel"] == "HIGH_CONFIDENCE"
    assert unknown["sightingReportPreview"]["sightings"] == []
    video = load_fixture("postcard_video_only.json")
    assert video["mediaImageCount"] == 0
    assert [asset["__typename"] for asset in video["medias"]] == ["MediaVideo"]


def test_paging_fixture_requires_identity_deduplication_not_timestamp_only():
    first, second = load_fixture("feed_incremental_pages.json")
    assert first["pageInfo"]["hasNextPage"]
    assert not second["pageInfo"]["hasNextPage"]
    assert first["edges"][-1]["node"]["id"] == second["edges"][0]["node"]["id"]
    timestamps = {
        edge["node"]["createdAt"] for page in (first, second) for edge in page["edges"]
    }
    assert len(timestamps) == 1


def test_long_url_fixture_really_exceeds_event_budget_without_losing_video():
    postcard = load_fixture("postcard_long_urls.json")
    assert len(json.dumps(postcard).encode()) > 32768
    assert len(postcard["medias"]) == 25
    assert postcard["medias"][-1]["__typename"] == "MediaVideo"


def test_fixture_loads_are_isolated_and_allowlisted():
    first = load_fixture("account_empty.json")
    first["feeders"].append({"id": "changed-by-test"})
    assert load_fixture("account_empty.json")["feeders"] == []
    with pytest.raises(ValueError, match="Unknown fixture"):
        load_fixture("../manifest.json")


def test_sanitizer_preserves_links_but_removes_identifiers_and_url_tokens():
    sanitizer = FixtureSanitizer()
    original = {
        "__typename": "FeederForOwner",
        "id": "original-feeder-id",
        "name": "Original feeder name",
        "email": "not-a-real-person@example.com",
        "token": "dummy-test-token",
        "thumbnailUrl": "https://cdn.example.com/private/path?Signature=dummy",
        "member": {"id": "original-feeder-id"},
    }
    sanitized = sanitizer.sanitize(original)
    assert sanitized["id"] == sanitized["member"]["id"]
    assert sanitized["id"] != original["id"]
    assert sanitized["name"] != original["name"]
    assert not privacy_errors(sanitized)
    assert "Signature" not in sanitized["thumbnailUrl"]
    assert original["token"] == "dummy-test-token"  # Input is unchanged.


def test_privacy_guard_detects_unreviewed_credentials_and_external_urls():
    errors = privacy_errors(
        {"accessToken": "dummy", "contentUrl": "https://cdn.example.com/a"}
    )
    assert len(errors) == 2
