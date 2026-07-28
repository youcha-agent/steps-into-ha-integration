"""The Steps Into HA integration.

Receives a daily step count pushed by the Steps Into HA iOS app and exposes it as a
sensor. Everything the old copy-paste `template:` webhook block did, minus the YAML
and the restart.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from aiohttp import web
from aiohttp.web import Request, Response
from homeassistant.components import webhook
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import (
    ATTR_PERSON,
    ATTR_STEPS,
    ATTR_TIMESTAMP,
    CONF_CLOUDHOOK,
    CONF_WEBHOOK_ID,
    DOMAIN,
    signal_new_steps,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]

# The upper bound is the important half. The sensor is TOTAL_INCREASING, so one absurd
# reading is absorbed into long-term statistics permanently — a later, smaller value
# cannot correct it, and every historical chart stays skewed. 200,000 is roughly four
# times the highest plausible human day, so it never rejects real data.
MAX_STEPS = 200_000

# ALLOW_EXTRA so a future app version can add fields without breaking older
# integration installs.
WEBHOOK_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_STEPS): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=MAX_STEPS)
        ),
        vol.Optional(ATTR_PERSON): cv.string,
        vol.Optional(ATTR_TIMESTAMP): cv.string,
    },
    extra=vol.ALLOW_EXTRA,
)


async def handle_webhook(
    hass: HomeAssistant, webhook_id: str, request: Request
) -> Response:
    """Handle one step push from the app.

    Deliberately answers 400 on a bad body. The template-sensor setup this replaces
    returned 200 for anything, so the app reported "Sent" even when Home Assistant
    stored nothing.
    """
    try:
        payload: dict[str, Any] = WEBHOOK_SCHEMA(await request.json())
    except ValueError:
        _LOGGER.warning("Webhook %s received a body that isn't valid JSON", webhook_id)
        return web.json_response({"error": "invalid_json"}, status=400)
    except vol.Invalid as err:
        _LOGGER.warning("Webhook %s received an unusable payload: %s", webhook_id, err)
        return web.json_response({"error": str(err)}, status=400)

    async_dispatcher_send(hass, signal_new_steps(webhook_id), payload)
    return web.json_response({"ok": True})


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one person from a config entry."""
    webhook_id = entry.data[CONF_WEBHOOK_ID]
    try:
        webhook.async_register(
            hass,
            DOMAIN,
            entry.title,
            webhook_id,
            handle_webhook,
            allowed_methods=["POST"],
            # The phone syncs from wherever it happens to be, and requests arriving via a
            # reverse proxy or Nabu Casa count as remote. local_only would drop those
            # silently. The generated webhook ID is the secret instead.
            local_only=False,
        )
    except ValueError as err:
        # Home Assistant allows exactly one owner per webhook ID. The config flow checks
        # for this, but a `template:` block can be added to configuration.yaml afterwards
        # — so fail with something a human can act on rather than a traceback.
        raise ConfigEntryError(
            f"Webhook ID {webhook_id} is already in use, most likely by a webhook trigger"
            " in configuration.yaml. Remove that block, or delete this person and add"
            " them again to get a different ID."
        ) from err

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    webhook.async_unregister(hass, entry.data[CONF_WEBHOOK_ID])
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload when the entry is updated (e.g. the webhook URL changed)."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Clean up the cloudhook when the user deletes the entry."""
    if not entry.data.get(CONF_CLOUDHOOK):
        return
    if "cloud" not in hass.config.components:
        return

    from homeassistant.components import cloud

    try:
        await cloud.async_delete_cloudhook(hass, entry.data[CONF_WEBHOOK_ID])
    except cloud.CloudNotAvailable:
        _LOGGER.debug(
            "Cloud unavailable; cloudhook for %s will be orphaned", entry.title
        )
