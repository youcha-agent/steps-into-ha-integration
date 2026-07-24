"""Tests for the webhook endpoint.

These go over real HTTP through Home Assistant's aiohttp app, so they exercise the same
path the iOS app's HAWebhookClient takes.
"""

from __future__ import annotations

import pytest
from homeassistant.config_entries import ConfigEntryState

from custom_components.steps_into_ha.const import CONF_CLOUDHOOK

from .conftest import WEBHOOK_ID

PATH = f"/api/webhook/{WEBHOOK_ID}"


async def test_valid_payload_updates_sensor(hass, setup_entry, hass_client_no_auth):
    """The payload the app actually sends lands in the sensor."""
    client = await hass_client_no_auth()

    response = await client.post(
        PATH,
        json={
            "steps": 8432,
            "person": "Dad",
            "timestamp": "2026-07-21T18:03:00Z",
        },
    )

    assert response.status == 200
    assert await response.json() == {"ok": True}

    await hass.async_block_till_done()
    assert hass.states.get("sensor.dad_steps").state == "8432"


async def test_extra_fields_are_tolerated(hass, setup_entry, hass_client_no_auth):
    """A future app version adding fields must not break older installs."""
    client = await hass_client_no_auth()

    response = await client.post(
        PATH, json={"steps": 100, "person": "Dad", "distance_m": 812, "floors": 3}
    )

    assert response.status == 200
    await hass.async_block_till_done()
    assert hass.states.get("sensor.dad_steps").state == "100"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"person": "Dad"},
        {"steps": "banana"},
        {"steps": -5},
        {"steps": None},
    ],
    ids=["empty", "no_steps", "not_a_number", "negative", "null"],
)
async def test_bad_payload_returns_400(hass, setup_entry, hass_client_no_auth, payload):
    """Answer 400, not 200.

    The template sensor this replaces returned 200 for anything, so the app showed a
    green "Sent" while Home Assistant stored nothing.
    """
    client = await hass_client_no_auth()

    response = await client.post(PATH, json=payload)

    assert response.status == 400
    await hass.async_block_till_done()
    assert hass.states.get("sensor.dad_steps").state == "unknown"


async def test_malformed_json_returns_400(hass, setup_entry, hass_client_no_auth):
    """A truncated body shouldn't 500."""
    client = await hass_client_no_auth()

    response = await client.post(
        PATH, data="{not json", headers={"Content-Type": "application/json"}
    )

    assert response.status == 400
    assert (await response.json())["error"] == "invalid_json"


async def test_get_is_rejected(hass, setup_entry, hass_client_no_auth):
    """Only POST is registered, so a browser visit can't touch the sensor."""
    client = await hass_client_no_auth()

    response = await client.get(PATH)

    assert response.status == 405


async def test_unknown_webhook_id_changes_nothing(
    hass, setup_entry, hass_client_no_auth
):
    """A guessed ID gets nothing.

    Home Assistant answers 200 for unregistered webhook IDs on purpose, so a prober
    can't tell a valid ID from an invalid one. The check that matters is the state.
    """
    client = await hass_client_no_auth()

    await client.post("/api/webhook/not-a-real-webhook", json={"steps": 1})

    await hass.async_block_till_done()
    assert hass.states.get("sensor.dad_steps").state == "unknown"


async def test_unload_unregisters_webhook(hass, setup_entry, hass_client_no_auth):
    """After unloading, the URL must stop feeding the sensor."""
    client = await hass_client_no_auth()
    assert (await client.post(PATH, json={"steps": 10})).status == 200
    await hass.async_block_till_done()
    assert hass.states.get("sensor.dad_steps").state == "10"

    assert await hass.config_entries.async_unload(setup_entry.entry_id)
    await hass.async_block_till_done()
    assert setup_entry.state is ConfigEntryState.NOT_LOADED

    # Same 200-for-everything behaviour as above, so assert on the effect: the
    # handler is gone, so nothing is dispatched.
    await client.post(PATH, json={"steps": 99})
    await hass.async_block_till_done()
    assert hass.states.get("sensor.dad_steps").state != "99"


async def test_cloudhook_deleted_on_removal(hass, config_entry, mock_cloud):
    """Don't leave an orphaned cloudhook behind on the Nabu Casa side."""
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        config_entry, data={**config_entry.data, CONF_CLOUDHOOK: True}
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert await hass.config_entries.async_remove(config_entry.entry_id)
    await hass.async_block_till_done()

    mock_cloud.async_delete_cloudhook.assert_awaited_once_with(hass, WEBHOOK_ID)


async def test_no_cloudhook_no_delete(hass, setup_entry, mock_cloud):
    """A local-only entry must not call into cloud on removal."""
    assert await hass.config_entries.async_remove(setup_entry.entry_id)
    await hass.async_block_till_done()

    mock_cloud.async_delete_cloudhook.assert_not_awaited()


async def test_cloud_error_on_removal_is_survivable(hass, config_entry, mock_cloud):
    """A cloud outage must not block deleting a person from Home Assistant."""
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        config_entry, data={**config_entry.data, CONF_CLOUDHOOK: True}
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    mock_cloud.async_delete_cloudhook.side_effect = mock_cloud.CloudNotAvailable

    assert await hass.config_entries.async_remove(config_entry.entry_id)
    await hass.async_block_till_done()
