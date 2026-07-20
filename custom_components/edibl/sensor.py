"""Edibl sensors — a read-out of the kitchen's state."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import EdiblCoordinator


@dataclass(frozen=True, kw_only=True)
class EdiblSensorDescription(SensorEntityDescription):
    """Describes an Edibl sensor."""

    value_fn: Callable[[dict], int]
    attrs_fn: Callable[[dict], dict] | None = None


def _totals(data: dict) -> dict:
    return (data.get("dashboard") or {}).get("totals") or {}


SENSORS: tuple[EdiblSensorDescription, ...] = (
    EdiblSensorDescription(
        key="items_in_stock", name="Items in stock", icon="mdi:fridge",
        native_unit_of_measurement="items", state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _totals(d).get("lots", 0),
    ),
    EdiblSensorDescription(
        key="expiring_soon", name="Expiring soon", icon="mdi:clock-alert-outline",
        native_unit_of_measurement="items", state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _totals(d).get("expiring", 0),
        attrs_fn=lambda d: {
            "items": [
                {
                    "name": (s.get("product") or {}).get("name"),
                    "days_to_expiry": s.get("daysToExpiry"),
                    "location": (s.get("location") or {}).get("name"),
                }
                for s in (d.get("dashboard") or {}).get("expiring", [])
            ]
        },
    ),
    EdiblSensorDescription(
        key="expired", name="Expired", icon="mdi:food-off-outline",
        native_unit_of_measurement="items", state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _totals(d).get("expired", 0),
    ),
    EdiblSensorDescription(
        key="products", name="Products", icon="mdi:tag-multiple-outline",
        native_unit_of_measurement="products", state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _totals(d).get("products", 0),
    ),
    EdiblSensorDescription(
        key="locations", name="Locations", icon="mdi:map-marker-outline",
        native_unit_of_measurement="locations", state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _totals(d).get("locations", 0),
    ),
    EdiblSensorDescription(
        key="wasted_products", name="Wasted products (learned)", icon="mdi:recycle",
        native_unit_of_measurement="products", state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: len(d.get("lifecycle") or []),
        attrs_fn=lambda d: {
            "suggestions": [
                {"product": r.get("productName"), "wasted": r.get("wasted"),
                 "suggestion": r.get("suggestion")}
                for r in (d.get("lifecycle") or [])
            ]
        },
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: EdiblCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        EdiblSensor(coordinator, entry.entry_id, desc) for desc in SENSORS
    )


class EdiblSensor(CoordinatorEntity[EdiblCoordinator], SensorEntity):
    """A single Edibl metric."""

    entity_description: EdiblSensorDescription
    _attr_has_entity_name = True

    def __init__(
        self, coordinator: EdiblCoordinator, entry_id: str,
        description: EdiblSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name="Edibl",
            manufacturer="Edibl",
            configuration_url=coordinator.host,
        )

    @property
    def native_value(self) -> int:
        return self.entity_description.value_fn(self.coordinator.data or {})

    @property
    def extra_state_attributes(self) -> dict | None:
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.coordinator.data or {})
