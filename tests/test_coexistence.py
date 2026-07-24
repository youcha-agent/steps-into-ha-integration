"""Running the integration alongside a hand-written `template:` webhook.

Plenty of people will land here: they set the app up the old way, then add the
integration for a second family member — or migrate one phone at a time. Both routes have
to work at once, and the one case that can't work (the same webhook ID twice) has to fail
in a way someone can act on.
"""

from __future__ import annotations

import pytest
from homeassistant.components import webhook
from homeassistant.config_entries import ConfigEntryState
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.steps_into_ha.const import (
    CONF_CLOUDHOOK,
    CONF_PERSON,
    CONF_WEBHOOK_ID,
    CONF_WEBHOOK_URL,
    DOMAIN,
)

from .conftest import WEBHOOK_ID

YAML_WEBHOOK_ID = "family_steps_1"  # 8+ chars, so it passes the format check and
# actually reaches the collision test


@pytest.fixture
async def yaml_webhook(hass):
    """A `template:` trigger sensor, exactly as the app's config-file route documents."""
    assert await async_setup_component(
        hass,
        "template",
        {
            "template": [
                {
                    "trigger": [
                        {
                            "platform": "webhook",
                            "webhook_id": YAML_WEBHOOK_ID,
                            "allowed_methods": ["POST"],
                            "local_only": False,
                        }
                    ],
                    "sensor": [
                        {
                            "name": "Person 1 Steps",
                            "unique_id": "human_1_webhook_sensor",
                            "state": "{{ trigger.json.steps | int(0) }}",
                            "unit_of_measurement": "steps",
                            "state_class": "total_increasing",
                        }
                    ],
                }
            ]
        },
    )
    await hass.async_block_till_done()


async def test_both_routes_run_side_by_side(
    hass, yaml_webhook, setup_entry, hass_client_no_auth
):
    """A YAML person and an integration person, updating independently."""
    client = await hass_client_no_auth()

    assert (
        await client.post(f"/api/webhook/{YAML_WEBHOOK_ID}", json={"steps": 1111})
    ).status == 200
    assert (
        await client.post(f"/api/webhook/{WEBHOOK_ID}", json={"steps": 2222})
    ).status == 200
    await hass.async_block_till_done()

    assert hass.states.get("sensor.person_1_steps").state == "1111"
    assert hass.states.get("sensor.dad_steps").state == "2222"


async def test_routes_do_not_cross_talk(
    hass, yaml_webhook, setup_entry, hass_client_no_auth
):
    """Posting to one webhook must leave the other person's count alone."""
    client = await hass_client_no_auth()

    await client.post(f"/api/webhook/{WEBHOOK_ID}", json={"steps": 2222})
    await hass.async_block_till_done()
    assert hass.states.get("sensor.person_1_steps").state == "unknown"

    await client.post(f"/api/webhook/{YAML_WEBHOOK_ID}", json={"steps": 1111})
    await hass.async_block_till_done()
    assert hass.states.get("sensor.dad_steps").state == "2222"


async def test_flow_rejects_a_webhook_id_owned_by_yaml(hass, yaml_webhook):
    """The one genuine conflict, caught before an entry is created."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user", "show_advanced_options": True}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PERSON: "Dad", CONF_WEBHOOK_ID: YAML_WEBHOOK_ID}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_WEBHOOK_ID: "webhook_id_in_use"}


async def test_probe_does_not_leak_a_registration(hass):
    """The taken-check must leave the ID free for the entry that follows."""
    from custom_components.steps_into_ha.config_flow import async_is_webhook_id_taken

    assert async_is_webhook_id_taken(hass, "brand-new-id") is False
    # Would raise "Handler is already defined!" if the probe hadn't cleaned up.
    webhook.async_register(hass, DOMAIN, "real", "brand-new-id", lambda *_: None)


async def test_entry_fails_legibly_if_yaml_claims_the_id_later(hass, yaml_webhook):
    """YAML added after the entry: fail with a message, not a traceback.

    The flow can't prevent this — the config file changes independently.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Dad",
        unique_id=YAML_WEBHOOK_ID,
        data={
            CONF_PERSON: "Dad",
            CONF_WEBHOOK_ID: YAML_WEBHOOK_ID,
            CONF_WEBHOOK_URL: f"http://10.0.0.2:8123/api/webhook/{YAML_WEBHOOK_ID}",
            CONF_CLOUDHOOK: False,
        },
    )
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert "already in use" in entry.reason
    # The YAML sensor is untouched — the integration failing must not break the old setup.
    assert hass.states.get("sensor.person_1_steps") is not None


async def test_generated_ids_never_collide_with_yaml(hass, yaml_webhook):
    """The default path can't hit this at all: 64 random hex characters."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PERSON: "Dad"}
    )
    assert result["step_id"] == "url"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], result["data_schema"]({})
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "connect"
    assert result["description_placeholders"]["webhook_id"] != YAML_WEBHOOK_ID
