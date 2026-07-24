"""Config flow for Edibl."""
from __future__ import annotations

from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_HOST,
    CONF_TOKEN,
    CONF_UPDATE_INTERVAL,
    DEFAULT_HOST,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)

_TIMEOUT = aiohttp.ClientTimeout(total=10)


class EdiblConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the Edibl config flow."""

    VERSION = 1

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry) -> EdiblOptionsFlow:
        return EdiblOptionsFlow()

    def __init__(self) -> None:
        self._discovered_host: str | None = None
        self._discovered_token: str = ""

    async def async_step_hassio(self, discovery_info) -> ConfigFlowResult:
        """Auto-discovery from the Edibl add-on (via the Supervisor)."""
        # NB: no blanket _async_current_entries() abort here — we dedupe on the
        # "edibl_addon" unique id below, so a re-fired discovery (e.g. after the
        # integration key was rotated) UPDATES the existing entry's token instead
        # of being dropped, which would strand it on the old key.
        cfg = getattr(discovery_info, "config", None) or {}
        host, port = cfg.get("host"), cfg.get("port", 7746)
        if not host:
            return self.async_abort(reason="cannot_connect")
        self._discovered_host = f"http://{host}:{port}"
        # The add-on advertises a long-lived API key so the integration is
        # authenticated on the direct (non-ingress) REST path — works whether or
        # not disable_auth is set on the add-on.
        self._discovered_token = str(cfg.get("token", "") or "")
        await self.async_set_unique_id("edibl_addon")
        # Refresh the stored host, and the token ONLY when the add-on actually
        # advertised one. The token mint is best-effort, so a re-fired discovery
        # can carry token="" (mint failed / not yet minted); overwriting a good
        # stored CONF_TOKEN with "" would 401 the next poll and silently break the
        # entry. An empty token still heals an entry that never had one, because a
        # missing key reads back as "" anyway.
        updates = {CONF_HOST: self._discovered_host}
        if self._discovered_token:
            updates[CONF_TOKEN] = self._discovered_token
        self._abort_if_unique_id_configured(updates=updates)
        # Confirm the add-on is actually reachable before offering setup, so the
        # discovered card never creates a dead entry (matches HomeHoard).
        if await self._reachable(self._discovered_host) is False:
            return self.async_abort(reason="cannot_connect")
        return await self.async_step_hassio_confirm()

    async def async_step_hassio_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm setting up the discovered Edibl add-on."""
        if user_input is not None:
            return self.async_create_entry(
                title="Edibl",
                data={CONF_HOST: self._discovered_host, CONF_TOKEN: self._discovered_token},
            )
        return self.async_show_form(
            step_id="hassio_confirm",
            description_placeholders={"host": self._discovered_host or ""},
        )

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

    async def _reachable(self, host: str) -> bool:
        """True if the add-on answers on its public status endpoint (no auth)."""
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(f"{host}/api/v1/status", timeout=_TIMEOUT) as resp:
                return resp.status == 200
        except (aiohttp.ClientError, TimeoutError):
            return False

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


class EdiblOptionsFlow(OptionsFlow):
    """Tune the poll interval after setup."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        current = int(
            self.config_entry.options.get(CONF_UPDATE_INTERVAL, DEFAULT_SCAN_INTERVAL)
        )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_UPDATE_INTERVAL, default=current): vol.All(
                        int, vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL)
                    ),
                }
            ),
        )
