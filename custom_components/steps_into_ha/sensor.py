"""Sensor entities for Steps Into HA."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_STEPS,
    ATTR_TIMESTAMP,
    CONF_PERSON,
    CONF_WEBHOOK_ID,
    DOMAIN,
    UNIT_STEPS,
    signal_new_steps,
)

_LOGGER = logging.getLogger(__name__)


def _device_info(entry: ConfigEntry) -> DeviceInfo:
    """One device per person, so both entities group under their name."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.data[CONF_PERSON],
        manufacturer="Steps Into HA",
        model="Apple Health",
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the step count and last-sync sensors."""
    async_add_entities([StepsSensor(entry), LastSyncSensor(entry)])


class StepsSensor(RestoreSensor):
    """Today's step count as pushed by the phone.

    TOTAL_INCREASING matches the value the app actually sends — a running total for the
    day that resets at local midnight — and is what lets Home Assistant keep long-term
    statistics and handle the reset. This mirrors the template sensor it replaces, so
    existing ApexCharts dashboards keep working once the entity ID is repointed.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "steps"
    _attr_native_unit_of_measurement = UNIT_STEPS
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_icon = "mdi:walk"

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialise the sensor."""
        self._webhook_id: str = entry.data[CONF_WEBHOOK_ID]
        self._attr_unique_id = f"{entry.entry_id}_steps"
        self._attr_device_info = _device_info(entry)

    async def async_added_to_hass(self) -> None:
        """Restore the last count and start listening for pushes.

        Restoring matters: the phone only syncs about hourly, so without this a Home
        Assistant restart would leave the sensor unknown for up to an hour.
        """
        await super().async_added_to_hass()

        if (last_data := await self.async_get_last_sensor_data()) is not None:
            self._attr_native_value = last_data.native_value

        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_new_steps(self._webhook_id), self._handle_payload
            )
        )

    @callback
    def _handle_payload(self, payload: dict[str, Any]) -> None:
        """Store a validated step count."""
        self._attr_native_value = payload[ATTR_STEPS]
        self.async_write_ha_state()


class LastSyncSensor(SensorEntity):
    """When the phone last reached Home Assistant.

    The honest answer to "is my phone actually syncing?" — which the old setup gave no
    way to check short of watching the step count for an hour.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "last_sync"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialise the sensor."""
        self._webhook_id: str = entry.data[CONF_WEBHOOK_ID]
        self._attr_unique_id = f"{entry.entry_id}_last_sync"
        self._attr_device_info = _device_info(entry)

    async def async_added_to_hass(self) -> None:
        """Start listening for pushes."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_new_steps(self._webhook_id), self._handle_payload
            )
        )

    @callback
    def _handle_payload(self, payload: dict[str, Any]) -> None:
        """Prefer the app's reading time, fall back to arrival time."""
        stamp = None
        if (raw := payload.get(ATTR_TIMESTAMP)) is not None:
            stamp = dt_util.parse_datetime(raw)
            if stamp is None:
                _LOGGER.debug("Unparseable timestamp %r; using arrival time", raw)

        self._attr_native_value = stamp or dt_util.utcnow()
        self.async_write_ha_state()
