"""Config flow for Edibl."""
from __future__ import annotations

from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_HOST, CONF_TOKEN, DEFAULT_HOST, DOMAIN

_TIMEOUT = aiohttp.ClientTimeout(total=10)


class EdiblConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the Edibl config flow."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].rstrip("/")
            token = user_input.get(CONF_TOKEN, "").strip()
            error = await self._validate(host, token)
            if error is None:
                await self.async_set_unique_id(host)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title="Edibl", data={CONF_HOST: host, CONF_TOKEN: token}
                )
            errors["base"] = error

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=DEFAULT_HOST): str,
                vol.Optional(CONF_TOKEN, default=""): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def _validate(self, host: str, token: str) -> str | None:
        """Return None on success, else an error key."""
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(f"{host}/api/v1/status", timeout=_TIMEOUT) as resp:
                if resp.status != 200:
                    return "cannot_connect"
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            async with session.get(
                f"{host}/api/v1/dashboard", headers=headers, timeout=_TIMEOUT
            ) as resp:
                if resp.status == 401:
                    return "invalid_auth"
                if resp.status != 200:
                    return "cannot_connect"
        except (aiohttp.ClientError, TimeoutError):
            return "cannot_connect"
        return None
