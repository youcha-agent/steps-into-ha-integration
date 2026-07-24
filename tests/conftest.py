"""Fixtures for the Steps Into HA tests."""

from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.steps_into_ha.const import (
    CONF_CLOUDHOOK,
    CONF_PERSON,
    CONF_URL_SOURCE,
    CONF_WEBHOOK_ID,
    CONF_WEBHOOK_URL,
    DOMAIN,
    URL_SOURCE_AUTO,
)

WEBHOOK_ID = "0123456789abcdef0123456789abcdef"
PERSON = "Dad"
CLOUDHOOK_URL = "https://hooks.nabu.casa/abc123"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Make Home Assistant load custom_components/ during tests."""
    yield


@pytest.fixture(autouse=True)
def known_urls(hass):
    """Give Home Assistant a URL so webhook.async_generate_url can resolve one."""
    hass.config.internal_url = "http://10.0.0.2:8123"
    return hass


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """A configured person, as written by a version before the address was a choice."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=PERSON,
        unique_id=WEBHOOK_ID,
        data={
            CONF_PERSON: PERSON,
            CONF_WEBHOOK_ID: WEBHOOK_ID,
            CONF_WEBHOOK_URL: f"http://10.0.0.2:8123/api/webhook/{WEBHOOK_ID}",
            CONF_CLOUDHOOK: False,
            CONF_URL_SOURCE: URL_SOURCE_AUTO,
        },
    )


@pytest.fixture
def mock_cloud(hass, monkeypatch):
    """Stand in for homeassistant.components.cloud.

    The real cloud component can't be imported here — it pulls in camera and
    conversation dependencies that aren't part of the test harness — and the behaviour
    under test is our own branching around it, not Nabu Casa's.
    """
    import homeassistant.components as ha_components

    class CloudNotAvailable(HomeAssistantError):
        """Cloud is not available."""

    class CloudNotConnected(CloudNotAvailable):
        """Cloud is available but not connected."""

    stub = ModuleType("homeassistant.components.cloud")
    stub.CloudNotAvailable = CloudNotAvailable
    stub.CloudNotConnected = CloudNotConnected
    stub.async_active_subscription = MagicMock(return_value=True)
    stub.async_get_or_create_cloudhook = AsyncMock(return_value=CLOUDHOOK_URL)
    stub.async_delete_cloudhook = AsyncMock()

    monkeypatch.setitem(sys.modules, "homeassistant.components.cloud", stub)
    monkeypatch.setattr(ha_components, "cloud", stub, raising=False)
    hass.config.components.add("cloud")
    return stub


@pytest.fixture
async def setup_entry(hass, config_entry) -> MockConfigEntry:
    """Add and set up the config entry."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry
