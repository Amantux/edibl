"""Config-flow behaviour for the Edibl integration.

Focus: the add-on↔integration token contract — hassio discovery threads the
advertised key into the entry, an empty re-discovery never wipes a good token,
and a rotated token heals the existing entry. Plus the manual auth path.
"""
from homeassistant.config_entries import SOURCE_HASSIO, SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.hassio import HassioServiceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.edibl.const import CONF_HOST, CONF_TOKEN, DOMAIN

STATUS = "http://local-edibl:7746/api/v1/status"
DASHBOARD = "http://local-edibl:7746/api/v1/dashboard"


def _discovery(token=""):
    return HassioServiceInfo(
        config={"host": "local-edibl", "port": 7746, "token": token},
        name="Edibl", slug="edibl", uuid="edibl-addon-uuid",
    )


async def test_hassio_discovery_threads_token(hass, aioclient_mock):
    aioclient_mock.get(STATUS, status=200)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_HASSIO}, data=_discovery("edbl_secret"),
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_HOST] == "http://local-edibl:7746"
    assert result["data"][CONF_TOKEN] == "edbl_secret"


async def test_empty_rediscovery_does_not_wipe_token(hass, aioclient_mock):
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="edibl_addon",
        data={CONF_HOST: "http://local-edibl:7746", CONF_TOKEN: "edbl_good"},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_HASSIO}, data=_discovery(""),  # mint not ready
    )

    assert result["type"] is FlowResultType.ABORT
    assert entry.data[CONF_TOKEN] == "edbl_good"  # preserved, not overwritten with ""


async def test_rotated_token_heals_existing_entry(hass, aioclient_mock):
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="edibl_addon",
        data={CONF_HOST: "http://local-edibl:7746", CONF_TOKEN: "edbl_old"},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_HASSIO}, data=_discovery("edbl_new"),
    )

    assert result["type"] is FlowResultType.ABORT  # already configured…
    assert entry.data[CONF_TOKEN] == "edbl_new"     # …but the token was updated


async def test_manual_flow_reports_invalid_auth(hass, aioclient_mock):
    aioclient_mock.get(STATUS, status=200)
    aioclient_mock.get(DASHBOARD, status=401)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_HOST: "http://local-edibl:7746", CONF_TOKEN: "edbl_wrong"},
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
