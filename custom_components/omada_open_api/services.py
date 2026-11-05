"""Services that manage which Omada clients are tracked.

``track_clients`` adds clients to the tracked-client option (by MAC, or every
currently connected client with ``all_connected``) and ``untrack_clients``
removes them, so clients can be tracked without opening the options flow. A
changed option reloads the entry through the existing update listener, which
creates the new client devices and removes deselected ones; an unchanged
option is left alone so nothing reloads.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
import probatio as vol

from .clients import normalize_client_mac
from .const import CONF_SELECTED_CLIENTS, DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.util.json import JsonValueType

    from .types import OmadaConfigEntry

ATTR_CONFIG_ENTRY_ID = "config_entry_id"
ATTR_MACS = "macs"
ATTR_ALL_CONNECTED = "all_connected"

SERVICE_TRACK_CLIENTS = "track_clients"
SERVICE_UNTRACK_CLIENTS = "untrack_clients"

_MAC_HEX = re.compile(r"^[0-9A-F]{12}$")

TRACK_CLIENTS_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Optional(ATTR_MACS, default=list): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional(ATTR_ALL_CONNECTED, default=False): cv.boolean,
    }
)
UNTRACK_CLIENTS_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Required(ATTR_MACS): vol.All(cv.ensure_list, [cv.string]),
    }
)


def async_get_loaded_entry(
    hass: HomeAssistant, config_entry_id: str | None
) -> OmadaConfigEntry:
    """Return the requested loaded Omada entry, or the first loaded one.

    Raises:
        ServiceValidationError: No loaded entry, an unknown entry, or an entry
            that isn't loaded.

    """
    if not config_entry_id:
        loaded = hass.config_entries.async_loaded_entries(DOMAIN)
        if not loaded:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="no_loaded_entry",
            )
        config_entry_id = loaded[0].entry_id
    entry = hass.config_entries.async_get_entry(config_entry_id)
    if not entry or entry.domain != DOMAIN:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="config_entry_not_found",
            translation_placeholders={"config_entry_id": config_entry_id},
        )
    if entry.state is not ConfigEntryState.LOADED:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="config_entry_not_loaded",
            translation_placeholders={"config_entry_id": config_entry_id},
        )
    return entry


def _option_mac(mac: str) -> str:
    """Return a MAC in the option's format (AA-BB-CC-DD-EE-FF).

    Raises:
        ServiceValidationError: The value isn't a MAC address.

    """
    hexed = normalize_client_mac(mac.strip())
    if not _MAC_HEX.match(hexed):
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_mac",
            translation_placeholders={"mac": mac},
        )
    return "-".join(hexed[i : i + 2] for i in range(0, 12, 2))


def _connected_macs(entry: OmadaConfigEntry) -> list[str]:
    """Return the MACs of every client the sites' last poll saw connected."""
    macs: list[str] = []
    for coordinator in entry.runtime_data.coordinators.values():
        for client in (coordinator.data or {}).get("all_clients", []):
            mac = client.get("mac")
            if isinstance(mac, str) and mac:
                macs.append(_option_mac(mac))
    return macs


def _set_selected(
    hass: HomeAssistant, entry: OmadaConfigEntry, selected: list[str]
) -> None:
    """Store a new tracked-client list; the update listener reloads the entry."""
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_SELECTED_CLIENTS: selected}
    )


def async_setup_services(hass: HomeAssistant) -> None:
    """Register the client tracking services."""

    async def track_clients(call: ServiceCall) -> ServiceResponse:
        """Add clients to the tracked-client option."""
        entry = async_get_loaded_entry(hass, call.data.get(ATTR_CONFIG_ENTRY_ID))
        requested = [_option_mac(mac) for mac in call.data[ATTR_MACS]]
        if call.data[ATTR_ALL_CONNECTED]:
            requested += _connected_macs(entry)
        elif not requested:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="no_clients_given",
            )
        selected: list[str] = list(entry.options.get(CONF_SELECTED_CLIENTS, []))
        known = {normalize_client_mac(mac) for mac in selected}
        added: list[str] = []
        for mac in requested:
            if normalize_client_mac(mac) not in known:
                known.add(normalize_client_mac(mac))
                added.append(mac)
        if added:
            _set_selected(hass, entry, selected + added)
        added_json: list[JsonValueType] = list(added)
        return {"added": added_json, "tracked": len(selected) + len(added)}

    async def untrack_clients(call: ServiceCall) -> ServiceResponse:
        """Remove clients from the tracked-client option."""
        entry = async_get_loaded_entry(hass, call.data.get(ATTR_CONFIG_ENTRY_ID))
        drop = {normalize_client_mac(_option_mac(m)) for m in call.data[ATTR_MACS]}
        selected: list[str] = list(entry.options.get(CONF_SELECTED_CLIENTS, []))
        kept = [m for m in selected if normalize_client_mac(m) not in drop]
        removed = [m for m in selected if normalize_client_mac(m) in drop]
        if removed:
            _set_selected(hass, entry, kept)
        removed_json: list[JsonValueType] = list(removed)
        return {"removed": removed_json, "tracked": len(kept)}

    hass.services.async_register(
        DOMAIN,
        SERVICE_TRACK_CLIENTS,
        track_clients,
        schema=TRACK_CLIENTS_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_UNTRACK_CLIENTS,
        untrack_clients,
        schema=UNTRACK_CLIENTS_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
