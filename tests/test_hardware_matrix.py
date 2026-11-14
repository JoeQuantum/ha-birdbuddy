"""Offline hardware recognition and role behavior, never a hardware certification."""

from copy import deepcopy
from unittest.mock import MagicMock

from birdbuddy.client import BirdBuddy
from birdbuddy.feeder import FeederDeviceVersion, FeederHousingType, PowerProfile
import pytest

from custom_components.birdbuddy.const import DOMAIN
from custom_components.birdbuddy.binary_sensor import BirdBuddyChargingEntity
from custom_components.birdbuddy.device import BirdBuddyDevice
from custom_components.birdbuddy.select import BirdBuddyPowerProfileSelector
from custom_components.birdbuddy.sensor import (
    BirdBuddyBatteryEntity,
    BirdBuddyFoodStateEntity,
    BirdBuddySignalEntity,
    BirdBuddyTemperatureEntity,
)
from custom_components.birdbuddy.switch import (
    BirdBuddyAudioSwitch,
    BirdBuddyOffGridSwitch,
)
from custom_components.birdbuddy.update import BirdBuddyUpdate
from tests.fixtures import load_fixture, load_manifest


@pytest.mark.parametrize("version", ["V1", "V1_PRO", "V2", "V2_NOOK"])
@pytest.mark.parametrize(
    ("housing", "model"),
    [
        ("CLASSIC", "Birdbuddy Feeder"),
        ("HUMMINGBIRD", "Smart Hummingbird Feeder"),
        ("BIRD_BATH", "Smart Bird Bath"),
    ],
)
def test_schema_hardware_metadata_is_independent_from_firmware(version, housing, model):
    payload = load_fixture("account_owner_hardware.json")["feeders"][0]
    payload.update(version=version, housingType=housing, firmwareVersion="fixture-2.0")
    feeder = BirdBuddyDevice(payload)
    info = feeder.device_info
    assert info.get("identifiers") == {(DOMAIN, "feeder-a")}
    assert info.get("hw_version") == version
    assert info.get("sw_version") == "fixture-2.0"
    assert info.get("model_id") == housing
    assert info.get("model") == model
    assert feeder.version == "fixture-2.0"


@pytest.mark.parametrize("unknown", [None, "FUTURE_VALUE"])
def test_unknown_hardware_and_housing_preserve_device_identity(unknown):
    feeder = BirdBuddyDevice(
        {
            "id": "future-feeder",
            "__typename": "FeederForOwner",
            "version": unknown,
            "housingType": unknown,
        }
    )
    assert feeder.device_version is FeederDeviceVersion.UNKNOWN
    assert feeder.housing_type is FeederHousingType.UNKNOWN
    assert feeder.device_info.get("identifiers") == {(DOMAIN, "future-feeder")}
    assert feeder.device_info.get("model") == "Bird Buddy"
    assert feeder.device_info.get("hw_version") is None
    assert feeder.device_info.get("model_id") is None


@pytest.mark.parametrize(
    ("role", "owner"),
    [
        ("FeederForOwner", True),
        ("FeederForMember", False),
        ("FeederForMemberPending", False),
        ("FeederForPublic", False),
    ],
)
@pytest.mark.parametrize("supports_audio", [True, False, None])
def test_control_availability_requires_owner_without_inventing_missing_capabilities(
    role, owner, supports_audio
):
    payload = {"id": "feeder-a", "__typename": role, "powerProfile": "STANDARD_MODE"}
    if supports_audio is not None:
        payload["supportsAudio"] = supports_audio
    feeder = BirdBuddyDevice(payload)
    coordinator = MagicMock(feeders={feeder.id: feeder}, last_update_success=True)
    assert feeder.supports_audio is supports_audio
    assert BirdBuddyAudioSwitch(feeder, coordinator).available is (
        owner and supports_audio is not False
    )
    assert BirdBuddyOffGridSwitch(feeder, coordinator).available is owner
    assert BirdBuddyPowerProfileSelector(feeder, coordinator).available is owner
    assert BirdBuddyUpdate(feeder, coordinator).available is owner


def test_missing_power_profile_stays_unknown_even_with_smart_energy_state():
    feeder = BirdBuddyDevice(
        {
            "id": "feeder-a",
            "__typename": "FeederForOwner",
            "smartEnergy": {
                "__typename": "FeederSmartEnergyEnabled",
                "runningPowerProfile": "POWER_SAVER_MODE",
            },
        }
    )
    assert feeder.selected_power_profile is PowerProfile.UNKNOWN
    feeder["powerProfile"] = "STANDARD_MODE"
    assert feeder.selected_power_profile is PowerProfile.STANDARD


@pytest.mark.parametrize(
    ("filename", "roles"),
    [
        ("account_owner_hardware.json", {"FeederForOwner"}),
        ("account_member_pending.json", {"FeederForMember", "FeederForMemberPending"}),
        ("account_empty.json", set()),
    ],
)
def test_synthetic_account_shapes_reconcile_without_requests(filename, roles):
    client = BirdBuddy(refresh_token="synthetic-unused")
    payload = load_fixture(filename)
    assert client._save_me(payload)
    assert {feeder["__typename"] for feeder in client.feeders.values()} == roles
    assert client.user is not None
    assert client.user.id == payload["user"]["id"]
    assert (
        load_manifest()["fixtures"][filename]["origin"] == "synthetic_from_live_schema"
    )


def test_shared_feeder_keeps_account_specific_roles_and_independent_snapshots():
    first, second = load_fixture("accounts_shared_feeder.json")
    owner_client, member_client = (
        BirdBuddy(refresh_token="synthetic-unused"),
        BirdBuddy(refresh_token="synthetic-unused-second"),
    )
    assert owner_client._save_me(first)
    assert member_client._save_me(second)
    assert owner_client.feeders["feeder-a"].is_owner
    assert not member_client.feeders["feeder-a"].is_owner
    assert member_client.feeders["feeder-b"].is_owner
    assert owner_client.feeders["feeder-a"] is not member_client.feeders["feeder-a"]
    changed = deepcopy(second)
    changed["feeders"] = []
    assert member_client._save_me(changed)
    assert member_client.feeders == {}
    assert set(owner_client.feeders) == {"feeder-a"}


def test_nook_fixture_is_explicitly_synthetic_not_hardware_evidence():
    metadata = load_manifest()["fixtures"]["account_owner_hardware.json"]
    assert metadata["origin"] == "synthetic_from_live_schema"
    assert "not hardware-tested" in metadata["purpose"]
    assert "V2_NOOK" in metadata["purpose"]


def test_member_null_telemetry_is_unknown_in_home_assistant():
    feeder = BirdBuddyDevice(load_fixture("account_member_pending.json")["feeders"][0])
    coordinator = MagicMock(feeders={feeder.id: feeder}, last_update_success=True)
    for factory in (
        BirdBuddyBatteryEntity,
        BirdBuddySignalEntity,
        BirdBuddyFoodStateEntity,
        BirdBuddyTemperatureEntity,
    ):
        assert factory(feeder, coordinator).native_value is None
    assert BirdBuddyChargingEntity(feeder, coordinator).is_on is None
