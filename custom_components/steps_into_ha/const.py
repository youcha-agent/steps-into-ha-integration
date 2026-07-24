"""Constants for the Steps Into HA integration."""

from __future__ import annotations

DOMAIN = "steps_into_ha"

# Config entry keys
CONF_PERSON = "person"
CONF_WEBHOOK_ID = "webhook_id"
CONF_WEBHOOK_URL = "webhook_url"
CONF_CLOUDHOOK = "cloudhook"

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
