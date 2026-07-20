"""The Edibl integration — sensors + a shopping-list service over Edibl's REST API."""
from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
import homeassistant.helpers.config_validation as cv

from .const import CONF_HOST, CONF_TOKEN, DOMAIN, SERVICE_ADD_TO_SHOPPING
from .coordinator import EdiblCoordinator

_LOGGER = logging.getLogger(__name__)
PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.TODO]

_ADD_SHOPPING_SCHEMA = vol.Schema(
    {
        vol.Required("name"): cv.string,
        vol.Optional("quantity", default=1): vol.Coerce(float),
        vol.Optional("unit", default="count"): cv.string,
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Edibl from a config entry."""
    coordinator = EdiblCoordinator(hass, entry.data[CONF_HOST], entry.data.get(CONF_TOKEN))
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _async_register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        if not hass.data[DOMAIN]:
            hass.services.async_remove(DOMAIN, SERVICE_ADD_TO_SHOPPING)
    return unloaded


def _async_register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_ADD_TO_SHOPPING):
        return

    async def _handle_add(call: ServiceCall) -> None:
        coordinators = list(hass.data.get(DOMAIN, {}).values())
        if not coordinators:
            raise HomeAssistantError("No Edibl instance configured")
        try:
            await coordinators[0].async_add_to_shopping_list(
                call.data["name"], call.data["quantity"], call.data["unit"]
            )
        except Exception as err:  # noqa: BLE001 — surface as a service error
            raise HomeAssistantError(f"Edibl request failed: {err}") from err

    hass.services.async_register(
        DOMAIN, SERVICE_ADD_TO_SHOPPING, _handle_add, schema=_ADD_SHOPPING_SCHEMA
    )
