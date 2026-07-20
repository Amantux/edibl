"""Edibl shopping list as a Home Assistant To-do List entity.

Add items by voice ("add milk to the shopping list") or from any HA dashboard,
and check them off — it stays in sync with Edibl's shopping list.
"""
from __future__ import annotations

from homeassistant.components.todo import (
    TodoItem,
    TodoItemStatus,
    TodoListEntity,
    TodoListEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import EdiblCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: EdiblCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([EdiblShoppingList(coordinator, entry.entry_id)])


class EdiblShoppingList(CoordinatorEntity[EdiblCoordinator], TodoListEntity):
    """The Edibl shopping list, surfaced as a HA To-do list."""

    _attr_has_entity_name = True
    _attr_name = "Shopping list"
    _attr_icon = "mdi:cart-outline"
    _attr_supported_features = (
        TodoListEntityFeature.CREATE_TODO_ITEM
        | TodoListEntityFeature.UPDATE_TODO_ITEM
        | TodoListEntityFeature.DELETE_TODO_ITEM
    )

    def __init__(self, coordinator: EdiblCoordinator, entry_id: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_shopping_list"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name="Edibl",
            manufacturer="Edibl",
            configuration_url=coordinator.host,
        )

    @property
    def todo_items(self) -> list[TodoItem]:
        out: list[TodoItem] = []
        for i in (self.coordinator.data or {}).get("shopping", []):
            status = (
                TodoItemStatus.COMPLETED
                if i.get("status") == "purchased"
                else TodoItemStatus.NEEDS_ACTION
            )
            qty, unit = i.get("quantity"), i.get("unit")
            description = f"{qty} {unit}" if qty and qty != 1 else None
            out.append(TodoItem(
                uid=i["id"], summary=i.get("name") or "", status=status,
                description=description,
            ))
        return out

    async def async_create_todo_item(self, item: TodoItem) -> None:
        await self.coordinator.async_shopping_add(item.summary or "Item")
        await self.coordinator.async_request_refresh()

    async def async_update_todo_item(self, item: TodoItem) -> None:
        status = "purchased" if item.status == TodoItemStatus.COMPLETED else "needed"
        await self.coordinator.async_shopping_update(item.uid, item.summary or "", status)
        await self.coordinator.async_request_refresh()

    async def async_delete_todo_items(self, uids: list[str]) -> None:
        for uid in uids:
            await self.coordinator.async_shopping_delete(uid)
        await self.coordinator.async_request_refresh()
