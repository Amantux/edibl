"""Constants for the Edibl integration."""

DOMAIN = "edibl"

CONF_HOST = "host"
CONF_TOKEN = "token"
CONF_UPDATE_INTERVAL = "update_interval"

DEFAULT_HOST = "http://homeassistant.local:7746"
DEFAULT_SCAN_INTERVAL = 300  # seconds
MIN_SCAN_INTERVAL = 30
MAX_SCAN_INTERVAL = 3600

SERVICE_ADD_TO_SHOPPING = "add_to_shopping_list"
