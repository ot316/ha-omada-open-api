"""Tests for listing blocked clients through the insight clients endpoint.

Controllers that only offer the v1 clients endpoint (e.g. 5.15) list online
clients only and never report a block, so a blocked client drops out of
``/clients``. ``insight/clients?filters.block=true`` lists every blocked
client (online or not), which keeps blocked clients controllable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.omada_open_api.api import OmadaApiClient, OmadaApiError
from custom_components.omada_open_api.coordinator import OmadaClientCoordinator
from custom_components.omada_open_api.switch import OmadaClientBlockSwitch

from .conftest import SAMPLE_CLIENT_WIRELESS, TEST_SITE_ID, TEST_SITE_NAME

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

INSIGHT_URL = (
    "https://controller.local/openapi/v1/controller-id/sites/site-id/insight/clients"
)

BLOCKED_ROW = {
    "mac": "E0-2B-E9-96-12-2F",
    "name": "DESKTOP-1BSI973",
    "block": True,
    "lastSeen": 1793828100000,
    "wireless": True,
    "guest": False,
}


def _api_client(side_effect: list[Any]) -> OmadaApiClient:
    client = OmadaApiClient.__new__(OmadaApiClient)
    client._api_url = "https://controller.local"
    client._omada_id = "controller-id"
    client._use_v1_clients = True
    client._authenticated_request = AsyncMock(side_effect=side_effect)
    return client


@pytest.mark.asyncio
async def test_get_blocked_clients_filters_insight_clients_by_block() -> None:
    """The blocked list comes from insight/clients with the block filter."""
    client = _api_client(
        [{"errorCode": 0, "result": {"totalRows": 1, "data": [BLOCKED_ROW]}}]
    )

    assert await client.get_blocked_clients("site-id") == [BLOCKED_ROW]

    call = client._authenticated_request.await_args_list[0]
    assert call.args == ("get", INSIGHT_URL)
    assert call.kwargs["params"]["filters.block"] == "true"
    assert call.kwargs["params"]["page"] == 1


@pytest.mark.asyncio
async def test_get_blocked_clients_reads_every_page() -> None:
    """All pages are fetched until totalRows rows have been read."""
    other = {**BLOCKED_ROW, "mac": "E2-74-BA-19-3D-E9", "name": "Pixel-11"}
    client = _api_client(
        [
            {"errorCode": 0, "result": {"totalRows": 2, "data": [BLOCKED_ROW]}},
            {"errorCode": 0, "result": {"totalRows": 2, "data": [other]}},
        ]
    )

    rows = await client.get_blocked_clients("site-id", page_size=1)

    assert [r["mac"] for r in rows] == ["E0-2B-E9-96-12-2F", "E2-74-BA-19-3D-E9"]
    pages = [
        c.kwargs["params"]["page"]
        for c in client._authenticated_request.await_args_list
    ]
    assert pages == [1, 2]


def _coordinator(
    hass: HomeAssistant, api: MagicMock, macs: list[str]
) -> OmadaClientCoordinator:
    return OmadaClientCoordinator(
        hass=hass,
        api_client=api,
        site_id=TEST_SITE_ID,
        site_name=TEST_SITE_NAME,
        selected_client_macs=macs,
    )


async def test_hidden_blocked_client_is_kept_as_blocked(
    hass: HomeAssistant, mock_api_client: MagicMock
) -> None:
    """A tracked client missing from /clients but listed as blocked stays in."""
    mock_api_client.get_blocked_clients = AsyncMock(return_value=[BLOCKED_ROW])
    coordinator = _coordinator(
        hass, mock_api_client, ["11-22-33-44-55-AA", "E0-2B-E9-96-12-2F"]
    )

    await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    blocked = coordinator.data["E0-2B-E9-96-12-2F"]
    assert blocked["blocked"] is True
    assert blocked["active"] is False
    assert blocked["name"] == "DESKTOP-1BSI973"
    assert blocked["last_seen"] == 1793828100000
    assert coordinator.data["11-22-33-44-55-AA"]["blocked"] is False


async def test_online_client_listed_as_blocked_is_marked_blocked(
    hass: HomeAssistant, mock_api_client: MagicMock
) -> None:
    """The blocked list wins over /clients, which never reports a block."""
    mock_api_client.get_blocked_clients = AsyncMock(
        return_value=[{**BLOCKED_ROW, "mac": SAMPLE_CLIENT_WIRELESS["mac"]}]
    )
    coordinator = _coordinator(hass, mock_api_client, ["11-22-33-44-55-AA"])

    await coordinator.async_refresh()

    client = coordinator.data["11-22-33-44-55-AA"]
    assert client["blocked"] is True
    assert client["name"] == "Phone"


async def test_untracked_blocked_client_is_not_added(
    hass: HomeAssistant, mock_api_client: MagicMock
) -> None:
    """Only tracked clients are added from the blocked list."""
    mock_api_client.get_blocked_clients = AsyncMock(return_value=[BLOCKED_ROW])
    coordinator = _coordinator(hass, mock_api_client, ["11-22-33-44-55-AA"])

    await coordinator.async_refresh()

    assert "E0-2B-E9-96-12-2F" not in coordinator.data


async def test_blocked_list_failure_keeps_client_data(
    hass: HomeAssistant, mock_api_client: MagicMock
) -> None:
    """A failing blocked list is not fatal: /clients data is still returned."""
    mock_api_client.get_blocked_clients = AsyncMock(
        side_effect=OmadaApiError("HTTP 404: Not Found", http_status=404)
    )
    coordinator = _coordinator(hass, mock_api_client, ["11-22-33-44-55-AA"])

    await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    assert "11-22-33-44-55-AA" in coordinator.data


async def test_switch_for_hidden_blocked_client_is_available_and_off(
    hass: HomeAssistant, mock_api_client: MagicMock
) -> None:
    """A blocked client never seen online gets a usable switch, off.

    No remembered or restored state is needed: the blocked list alone keeps
    the client in the coordinator data, e.g. a client blocked before Home
    Assistant was set up or from the Omada UI.
    """
    mock_api_client.get_clients = AsyncMock(
        return_value={"data": [], "totalRows": 0, "currentPage": 1}
    )
    mock_api_client.get_blocked_clients = AsyncMock(return_value=[BLOCKED_ROW])
    coordinator = _coordinator(hass, mock_api_client, ["E0-2B-E9-96-12-2F"])
    await coordinator.async_refresh()

    switch = OmadaClientBlockSwitch(coordinator, "E0-2B-E9-96-12-2F")

    assert switch.available is True
    assert switch.is_on is False
    assert switch.device_info is not None
    assert switch.device_info["name"] == "DESKTOP-1BSI973"


async def test_blocked_list_timeout_keeps_client_data(
    hass: HomeAssistant, mock_api_client: MagicMock
) -> None:
    """A timed-out blocked list is not fatal either."""
    mock_api_client.get_blocked_clients = AsyncMock(side_effect=TimeoutError)
    coordinator = _coordinator(hass, mock_api_client, ["11-22-33-44-55-AA"])

    await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    assert "11-22-33-44-55-AA" in coordinator.data
