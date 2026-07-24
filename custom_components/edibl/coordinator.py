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

    def __init__(
        self, hass: HomeAssistant, host: str, token: str | None,
        update_interval: int = DEFAULT_SCAN_INTERVAL,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=update_interval),
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
            try:
                shopping = await self._get("/shopping?status=all")
            except aiohttp.ClientError:
                shopping = []
            return {"dashboard": dashboard, "lifecycle": lifecycle, "shopping": shopping}
        except aiohttp.ClientResponseError as err:
            raise UpdateFailed(f"Edibl API returned {err.status}") from err
        except (aiohttp.ClientError, TimeoutError) as err:
            raise UpdateFailed(f"Edibl unreachable: {err}") from err

    async def _post(self, path: str, payload: dict) -> None:
        async with self._session.post(
            f"{self.host}/api/v1{path}", json=payload, headers=self._headers, timeout=_TIMEOUT
        ) as resp:
            resp.raise_for_status()

    async def async_add_to_shopping_list(self, name: str, quantity: float, unit: str) -> None:
        """POST an item onto the Edibl shopping list."""
        await self._post("/shopping", {"name": name, "quantity": quantity, "unit": unit})

    # --- shopping-list writes for the To-do entity --------------------------
    async def async_shopping_add(self, name: str) -> None:
        await self._post("/shopping", {"name": name})

    async def async_shopping_update(self, item_id: str, name: str, status: str) -> None:
        async with self._session.put(
            f"{self.host}/api/v1/shopping/{item_id}",
            json={"name": name, "status": status}, headers=self._headers, timeout=_TIMEOUT
        ) as resp:
            resp.raise_for_status()

    async def async_shopping_delete(self, item_id: str) -> None:
        async with self._session.delete(
            f"{self.host}/api/v1/shopping/{item_id}", headers=self._headers, timeout=_TIMEOUT
        ) as resp:
            resp.raise_for_status()
