"""Constants for the Steps Into HA integration."""

from __future__ import annotations

DOMAIN = "steps_into_ha"

# Config entry keys
CONF_PERSON = "person"
CONF_WEBHOOK_ID = "webhook_id"
CONF_WEBHOOK_URL = "webhook_url"
CONF_CLOUDHOOK = "cloudhook"
CONF_URL_SOURCE = "url_source"
CONF_CUSTOM_URL = "custom_url"

# Which address the QR code hands the phone. Recorded on the entry so that re-opening
# Configure doesn't silently revert a deliberate choice.
#
# AUTO means "written before this was a choice" — entries from 1.0.2 and earlier. It is
# never offered in the picker; it only ever means "no preference recorded, use the
# default".
URL_SOURCE_AUTO = "auto"
URL_SOURCE_CLOUD = "cloud"
URL_SOURCE_EXTERNAL = "external"
URL_SOURCE_INTERNAL = "internal"
URL_SOURCE_CUSTOM = "custom"

# What the picker preselects when nothing has been chosen yet. Whichever of these
# reaches Home Assistant from outside the house wins, because the phone syncs from
# wherever it happens to be.
URL_SOURCE_PREFERENCE = (URL_SOURCE_CLOUD, URL_SOURCE_EXTERNAL, URL_SOURCE_INTERNAL)

# Webhook payload keys, matching StepPayload in the iOS app.
ATTR_STEPS = "steps"
ATTR_PERSON = "person"
ATTR_TIMESTAMP = "timestamp"

# Version marker in the pairing QR code, so the app can reject a payload from a
# future integration it doesn't understand instead of silently misreading it.
PAIRING_VERSION = 1

UNIT_STEPS = "steps"


def signal_new_steps(webhook_id: str) -> str:
    """Dispatcher signal carrying a validated payload to one entry's entities."""
    return f"{DOMAIN}_new_steps_{webhook_id}"
