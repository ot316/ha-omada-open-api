"""Tests for the track_clients and untrack_clients services."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import ServiceValidationError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.omada_open_api import async_setup
from custom_components.omada_open_api.const import CONF_SELECTED_CLIENTS, DOMAIN
from custom_components.omada_open_api.types import OmadaRuntimeData

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

TRACKED = "AA-BB-CC-DD-EE-01"
ONLINE_NEW = "AA-BB-CC-DD-EE-02"
ONLINE_NEW_2 = "AA-BB-CC-DD-EE-03"


def _entry(
    hass: HomeAssistant,
    selected: list[str],
    online: list[str] | None = None,
    state: ConfigEntryState = ConfigEntryState.LOADED,
) -> MockConfigEntry:
    """Add an Omada entry whose site coordinator reports the online clients."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Omada",
        data={},
        options={CONF_SELECTED_CLIENTS: list(selected)},
    )
    entry.add_to_hass(hass)
    entry.mock_state(hass, state)
    coordinator = MagicMock()
    coordinator.data = {
        "all_clients": [{"mac": mac, "name": mac} for mac in (online or [])]
    }
    entry.runtime_data = MagicMock(
        spec=OmadaRuntimeData, coordinators={"site": coordinator}
    )
    return entry


async def _call(
    hass: HomeAssistant, service: str, data: dict[str, Any]
) -> dict[str, Any] | None:
    await async_setup(hass, {})
    return await hass.services.async_call(
        DOMAIN, service, data, blocking=True, return_response=True
    )


async def test_track_clients_adds_given_macs(hass: HomeAssistant) -> None:
    """MACs in any common format are stored in the integration's format."""
    entry = _entry(hass, [TRACKED])

    response = await _call(
        hass, "track_clients", {"macs": ["aa:bb:cc:dd:ee:02", TRACKED]}
    )

    assert entry.options[CONF_SELECTED_CLIENTS] == [TRACKED, ONLINE_NEW]
    assert response == {"added": [ONLINE_NEW], "tracked": 2}


async def test_track_clients_all_connected_adds_only_new(
    hass: HomeAssistant,
) -> None:
    """all_connected tracks every connected client not yet tracked."""
    entry = _entry(hass, [TRACKED], online=[TRACKED, ONLINE_NEW, ONLINE_NEW_2])

    response = await _call(hass, "track_clients", {"all_connected": True})

    assert entry.options[CONF_SELECTED_CLIENTS] == [
        TRACKED,
        ONLINE_NEW,
        ONLINE_NEW_2,
    ]
    assert response == {"added": [ONLINE_NEW, ONLINE_NEW_2], "tracked": 3}


async def test_track_clients_nothing_new_leaves_options(
    hass: HomeAssistant,
) -> None:
    """No change means no options update (and so no reload)."""
    entry = _entry(hass, [TRACKED], online=[TRACKED])
    before = entry.options

    response = await _call(hass, "track_clients", {"all_connected": True})

    assert entry.options is before
    assert response == {"added": [], "tracked": 1}


async def test_untrack_clients_removes_macs(hass: HomeAssistant) -> None:
    """untrack_clients drops the given MACs, matching any separator."""
    entry = _entry(hass, [TRACKED, ONLINE_NEW])

    response = await _call(hass, "untrack_clients", {"macs": ["aabbccddee02"]})

    assert entry.options[CONF_SELECTED_CLIENTS] == [TRACKED]
    assert response == {"removed": [ONLINE_NEW], "tracked": 1}


@pytest.mark.parametrize(
    ("service", "data", "key"),
    [
        ("track_clients", {}, "no_clients_given"),
        ("track_clients", {"macs": ["not-a-mac"]}, "invalid_mac"),
        ("untrack_clients", {"macs": ["zz:bb:cc:dd:ee:ff"]}, "invalid_mac"),
    ],
)
async def test_client_services_validate_input(
    hass: HomeAssistant, service: str, data: dict[str, Any], key: str
) -> None:
    """Missing or malformed MACs raise a translated validation error."""
    _entry(hass, [TRACKED])

    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, service, data)

    assert err.value.translation_key == key


async def test_track_clients_needs_loaded_entry(hass: HomeAssistant) -> None:
    """A not-loaded entry is rejected like the other Omada services."""
    entry = _entry(hass, [], state=ConfigEntryState.NOT_LOADED)

    with pytest.raises(ServiceValidationError) as err:
        await _call(
            hass,
            "track_clients",
            {"config_entry_id": entry.entry_id, "macs": [ONLINE_NEW]},
        )

    assert err.value.translation_key == "config_entry_not_loaded"
