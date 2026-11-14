"""Modern collection must never silently reinterpret legacy collection rules."""

import pytest

from custom_components.birdbuddy.postcard_policy import (
    UnsupportedPostcardPolicy,
    validate_collection_request,
)

BASE = {"postcard_id": "card-a", "selection_policy": "server"}


@pytest.mark.parametrize(
    "strategy", ["recognized", "best_guess", "mystery", "typo", None]
)
@pytest.mark.parametrize(
    "confidence", [{}, {"best_guess_confidence": 10}, {"best_guess_confidence": None}]
)
def test_every_explicit_legacy_strategy_is_rejected(strategy, confidence):
    with pytest.raises(UnsupportedPostcardPolicy, match="Remove legacy policies"):
        validate_collection_request(BASE | {"strategy": strategy} | confidence)


@pytest.mark.parametrize(
    "legacy",
    [
        {"best_guess_confidence": 10},
        {"best_guess_confidence": None},
        {"confidence_threshold": 90},
        {"confidence_threshold": None},
        {"sighting": {}},
        {"sighting": None},
        {"media_species_items": []},
        {"mediaSpeciesItems": []},
        {"species_ids": []},
    ],
)
def test_unvalidated_confidence_selection_and_old_reports_are_rejected(legacy):
    with pytest.raises(UnsupportedPostcardPolicy):
        validate_collection_request(BASE | legacy)


@pytest.mark.parametrize(
    "policy", [{}, {"selection_policy": None}, {"selection_policy": "recognized"}]
)
def test_server_selection_requires_explicit_migration_consent(policy):
    with pytest.raises(UnsupportedPostcardPolicy, match="Explicit selection_policy"):
        validate_collection_request({"postcard_id": "card-a"} | policy)


@pytest.mark.parametrize("share", [False, True])
@pytest.mark.parametrize("alias", [False, True])
def test_boolean_share_alias_must_agree_with_canonical_field(share, alias):
    data = BASE | {"share": share, "share_media": alias}
    if share != alias:
        with pytest.raises(ValueError, match="conflict"):
            validate_collection_request(data)
    else:
        request = validate_collection_request(data)
        assert request.share is share
        assert request.legacy_aliases == ("share_media",)


@pytest.mark.parametrize("field", ["share", "share_media", "identify"])
@pytest.mark.parametrize("value", ["false", 0, 1, None])
def test_sharing_is_not_enabled_by_truthy_string_coercion(field, value):
    with pytest.raises(ValueError, match="must be a boolean"):
        validate_collection_request(BASE | {field: value})


@pytest.mark.parametrize(
    "identifiers",
    [
        {},
        {"postcard_id": ""},
        {"postcard_id": " card-a "},
        {"postcard_id": 123},
        {"postcard": "card-a"},
        {"postcard": {}},
        {"postcard_id": "card-a", "postcard": {"id": "card-b"}},
    ],
)
def test_missing_or_conflicting_postcard_identifiers_fail(identifiers):
    with pytest.raises(ValueError):
        validate_collection_request({"selection_policy": "server"} | identifiers)


@pytest.mark.parametrize(
    "data",
    [
        BASE,
        BASE | {"postcard": {"id": "card-a"}},
        {"postcard": {"id": "card-a"}, "selection_policy": "server"},
    ],
)
def test_id_alias_preserves_exact_identity_and_private_default(data):
    request = validate_collection_request(data)
    assert request.postcard_id == "card-a"
    assert request.selection_policy == "server"
    assert request.share is False
    assert request.identify is False
    assert request.legacy_aliases == (("postcard",) if "postcard" in data else ())


def test_explicit_target_selectors_are_retained_for_account_validation():
    request = validate_collection_request(
        BASE
        | {
            "config_entry_id": "entry-a",
            "account_id": "account-a",
            "device_id": "device-a",
            "feeder_id": "feeder-a",
            "share_media": True,
        }
    )
    assert (
        request.config_entry_id,
        request.account_id,
        request.device_id,
        request.feeder_id,
    ) == ("entry-a", "account-a", "device-a", "feeder-a")
    assert request.share is True


def test_identification_requires_a_separate_explicit_boolean():
    request = validate_collection_request(BASE | {"identify": True})
    assert request.identify is True
    assert request.share is False


@pytest.mark.parametrize(
    "extra", [{"config_entry_id": ""}, {"feeder_id": None}, {"unknown_option": True}]
)
def test_invalid_targets_and_unknown_options_are_not_silently_ignored(extra):
    with pytest.raises(ValueError):
        validate_collection_request(BASE | extra)
