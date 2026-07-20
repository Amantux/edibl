"""Polling coordinator for the Edibl REST API."""
from __future__ import annotations

import logging
from datetime import timedelta

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)
_TIMEOUT = aiohttp.ClientTimeout(total=15)


class EdiblCoordinator(DataUpdateCoordinator):
    """Fetch the dashboard + lifecycle feed from an Edibl instance."""

    def __init__(self, hass: HomeAssistant, host: str, token: str | None) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.host = host.rstrip("/")
        self._session = async_get_clientsession(hass)
        self._headers = {"Authorization": f"Bearer {token}"} if token else {}

    async def _get(self, path: str) -> dict:
        url = f"{self.host}/api/v1{path}"
        async with self._session.get(url, headers=self._headers, timeout=_TIMEOUT) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def _async_update_data(self) -> dict:
        try:
            dashboard = await self._get("/dashboard")
            try:
                lifecycle = (await self._get("/dashboard/lifecycle")).get("items", [])
            except aiohttp.ClientError:
                lifecycle = []
            return {"dashboard": dashboard, "lifecycle": lifecycle}
        except aiohttp.ClientResponseError as err:
            raise UpdateFailed(f"Edibl API returned {err.status}") from err
        except (aiohttp.ClientError, TimeoutError) as err:
            raise UpdateFailed(f"Edibl unreachable: {err}") from err

    async def async_add_to_shopping_list(self, name: str, quantity: float, unit: str) -> None:
        """POST an item onto the Edibl shopping list."""
        url = f"{self.host}/api/v1/shopping"
        payload = {"name": name, "quantity": quantity, "unit": unit}
        async with self._session.post(
            url, json=payload, headers=self._headers, timeout=_TIMEOUT
        ) as resp:
            resp.raise_for_status()
