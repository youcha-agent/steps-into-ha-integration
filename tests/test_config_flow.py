"""Tests for the Steps Into HA config flow."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.steps_into_ha.config_flow import (
    async_resolve_webhook_url,
    pairing_payload,
)
from custom_components.steps_into_ha.const import (
    CONF_CLOUDHOOK,
    CONF_PERSON,
    CONF_WEBHOOK_ID,
    CONF_WEBHOOK_URL,
    DOMAIN,
)

from .conftest import CLOUDHOOK_URL, WEBHOOK_ID


@pytest.fixture(autouse=True)
def mock_setup_entry():
    """Don't actually start the integration during flow tests."""
    with patch(
        "custom_components.steps_into_ha.async_setup_entry", return_value=True
    ) as mock:
        yield mock


async def test_user_flow_generates_webhook(hass) -> None:
    """The happy path: type a name, get a webhook you never had to invent."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    # Without advanced mode the user is never asked for a webhook ID.
    assert CONF_WEBHOOK_ID not in result["data_schema"].schema

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PERSON: "Dad"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "connect"

    # webhook.async_generate_id() returns 64 hex characters — far past guessing, which
    # is the whole reason the user no longer picks this value themselves.
    generated_id = result["description_placeholders"]["webhook_id"]
    assert len(generated_id) == 64
    assert result["description_placeholders"]["url"].endswith(
        f"/api/webhook/{generated_id}"
    )

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Dad"
    assert result["data"][CONF_PERSON] == "Dad"
    assert result["data"][CONF_WEBHOOK_ID] == generated_id
    assert result["data"][CONF_CLOUDHOOK] is False


async def test_name_is_trimmed(hass) -> None:
    """Leading/trailing whitespace in the name shouldn't reach the device registry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PERSON: "  Dad  "}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["data"][CONF_PERSON] == "Dad"


async def test_blank_name_rejected(hass) -> None:
    """A whitespace-only name is not a name."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PERSON: "   "}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_PERSON: "invalid_person"}


async def test_advanced_mode_allows_custom_webhook_id(hass) -> None:
    """Advanced mode lets v1.0-app users pick an ID short enough to type."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user", "show_advanced_options": True}
    )
    assert CONF_WEBHOOK_ID in result["data_schema"].schema

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PERSON: "Dad", CONF_WEBHOOK_ID: "dad-4f2a91"}
    )
    assert result["step_id"] == "connect"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["data"][CONF_WEBHOOK_ID] == "dad-4f2a91"


@pytest.mark.parametrize(
    "bad_id",
    ["short", "has spaces", "human_1", "way/too:punctuated", "x" * 65],
    ids=["too_short", "spaces", "seven_chars", "bad_chars", "too_long"],
)
async def test_invalid_custom_webhook_id_rejected(hass, bad_id: str) -> None:
    """Guessable or malformed IDs are refused — the ID is the only secret."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user", "show_advanced_options": True}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PERSON: "Dad", CONF_WEBHOOK_ID: bad_id}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_WEBHOOK_ID: "invalid_webhook_id"}


async def test_duplicate_webhook_id_aborts(hass, config_entry) -> None:
    """Two people can't share a webhook ID — they'd overwrite each other."""
    config_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user", "show_advanced_options": True}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PERSON: "Mum", CONF_WEBHOOK_ID: WEBHOOK_ID}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_qr_encodes_pairing_payload(hass) -> None:
    """The QR carries versioned JSON so the app can prefill the name."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PERSON: "Dad"}
    )

    qr_selector = result["data_schema"].schema["qr"]
    payload = json.loads(qr_selector.config["data"])
    assert payload["v"] == 1
    assert payload["person"] == "Dad"
    assert payload["url"] == result["description_placeholders"]["url"]


def test_pairing_payload_is_compact() -> None:
    """Keep the QR dense enough to scan across a room."""
    encoded = pairing_payload("Dad", "https://hooks.nabu.casa/" + "a" * 32)
    assert " " not in encoded
    assert len(encoded) < 120


async def test_cloudhook_preferred_when_subscribed(hass, mock_cloud) -> None:
    """With Nabu Casa, hand the phone a cloudhook so remote sync just works."""
    url, is_cloudhook = await async_resolve_webhook_url(hass, WEBHOOK_ID)

    assert url == CLOUDHOOK_URL
    assert is_cloudhook is True
    mock_cloud.async_get_or_create_cloudhook.assert_awaited_once_with(hass, WEBHOOK_ID)


async def test_no_cloud_component_uses_local_url(hass) -> None:
    """Most installs have no cloud at all; that path must not touch it."""
    url, is_cloudhook = await async_resolve_webhook_url(hass, WEBHOOK_ID)

    assert url == f"http://10.0.0.2:8123/api/webhook/{WEBHOOK_ID}"
    assert is_cloudhook is False


async def test_local_url_when_not_subscribed(hass, mock_cloud) -> None:
    """Logged into cloud but unsubscribed: fall back rather than failing setup."""
    mock_cloud.async_active_subscription.return_value = False

    url, is_cloudhook = await async_resolve_webhook_url(hass, WEBHOOK_ID)

    assert url == f"http://10.0.0.2:8123/api/webhook/{WEBHOOK_ID}"
    assert is_cloudhook is False
    mock_cloud.async_get_or_create_cloudhook.assert_not_awaited()


@pytest.mark.parametrize("error", ["CloudNotAvailable", "CloudNotConnected"])
async def test_cloud_error_falls_back_to_local(hass, mock_cloud, error: str) -> None:
    """A cloud hiccup must not block adding a person."""
    mock_cloud.async_get_or_create_cloudhook.side_effect = getattr(mock_cloud, error)

    url, is_cloudhook = await async_resolve_webhook_url(hass, WEBHOOK_ID)

    assert url == f"http://10.0.0.2:8123/api/webhook/{WEBHOOK_ID}"
    assert is_cloudhook is False


async def test_cloudhook_url_stored_on_entry(hass, mock_cloud) -> None:
    """The full flow records that this entry owns a cloudhook, for cleanup later."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PERSON: "Dad"}
    )
    assert result["description_placeholders"]["url"] == CLOUDHOOK_URL

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["data"][CONF_WEBHOOK_URL] == CLOUDHOOK_URL
    assert result["data"][CONF_CLOUDHOOK] is True


async def test_options_flow_reshows_qr(hass, config_entry: MockConfigEntry) -> None:
    """ "I got a new phone" shouldn't mean deleting and recreating the person."""
    config_entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    assert result["description_placeholders"]["person"] == "Dad"
    assert "qr" in result["data_schema"].schema


async def test_options_flow_refreshes_changed_url(
    hass, config_entry: MockConfigEntry
) -> None:
    """A stale URL in the QR would silently pair the phone to nowhere."""
    config_entry.add_to_hass(hass)
    hass.config.internal_url = "http://10.0.0.9:8123"

    result = await hass.config_entries.options.async_init(config_entry.entry_id)

    expected = f"http://10.0.0.9:8123/api/webhook/{WEBHOOK_ID}"
    assert result["description_placeholders"]["url"] == expected
    assert config_entry.data[CONF_WEBHOOK_URL] == expected
