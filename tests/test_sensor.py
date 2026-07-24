"""Tests for the Steps Into HA sensors."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import ATTR_DEVICE_CLASS, ATTR_UNIT_OF_MEASUREMENT
from homeassistant.core import State
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache_with_extra_data,
)

from .conftest import WEBHOOK_ID

PATH = f"/api/webhook/{WEBHOOK_ID}"


async def test_entities_created(hass, setup_entry) -> None:
    """One person gives one device with a step count and a sync heartbeat."""
    steps = hass.states.get("sensor.dad_steps")
    assert steps is not None
    assert steps.attributes[ATTR_UNIT_OF_MEASUREMENT] == "steps"
    assert steps.attributes["state_class"] is SensorStateClass.TOTAL_INCREASING

    last_sync = hass.states.get("sensor.dad_last_sync")
    assert last_sync is not None
    assert last_sync.attributes[ATTR_DEVICE_CLASS] == SensorDeviceClass.TIMESTAMP


async def test_entities_share_one_device(hass, setup_entry) -> None:
    """Both entities group under the person's name in the UI."""
    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)

    entry = entity_registry.async_get("sensor.dad_steps")
    assert entry is not None
    device = device_registry.async_get(entry.device_id)
    assert device.name == "Dad"

    sync_entry = entity_registry.async_get("sensor.dad_last_sync")
    assert sync_entry.device_id == entry.device_id


async def test_last_sync_uses_payload_timestamp(
    hass, setup_entry, hass_client_no_auth
) -> None:
    """Report when the reading was taken, not when it happened to arrive."""
    client = await hass_client_no_auth()

    await client.post(
        PATH, json={"steps": 500, "timestamp": "2026-07-21T18:03:00+00:00"}
    )
    await hass.async_block_till_done()

    assert hass.states.get("sensor.dad_last_sync").state == "2026-07-21T18:03:00+00:00"


async def test_last_sync_falls_back_on_bad_timestamp(
    hass, setup_entry, hass_client_no_auth
) -> None:
    """An unparseable timestamp shouldn't lose the sync entirely."""
    client = await hass_client_no_auth()

    await client.post(PATH, json={"steps": 500, "timestamp": "not-a-date"})
    await hass.async_block_till_done()

    state = hass.states.get("sensor.dad_last_sync")
    assert state.state not in ("unknown", "unavailable")
    assert hass.states.get("sensor.dad_steps").state == "500"


async def test_step_count_survives_restart(hass, config_entry: MockConfigEntry) -> None:
    """The phone only syncs hourly — a restart must not blank the count.

    The template sensor this replaces had no restore, so a Home Assistant restart left
    the chart with a hole until the next background push.
    """
    mock_restore_cache_with_extra_data(
        hass,
        (
            (
                State("sensor.dad_steps", "8432"),
                {"native_value": 8432, "native_unit_of_measurement": "steps"},
            ),
        ),
    )

    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("sensor.dad_steps").state == "8432"


async def test_unique_ids_are_stable(hass, setup_entry) -> None:
    """Entity IDs must survive reloads, or dashboards break."""
    entity_registry = er.async_get(hass)
    entry = entity_registry.async_get("sensor.dad_steps")
    assert entry.unique_id == f"{setup_entry.entry_id}_steps"
