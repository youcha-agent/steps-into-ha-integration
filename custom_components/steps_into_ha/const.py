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

# The address the admin's browser is talking to right now, read off the live request.
# Someone behind a reverse proxy Home Assistant was never told about reaches the setup page
# on their real outside address while `hass.config.external_url` sits empty — so the one
# address that would work is the one nothing else can see.
#
# Picker-only, and **never persisted**: it comes from a request header, so re-resolving it
# on a later visit from a different address would silently move a paired URL. Choosing it
# stores `custom` with the resolved address instead. See `_async_apply_url_choice`.
URL_SOURCE_DETECTED = "detected"

# The order addresses are listed and ranked in. Whichever of these reaches Home Assistant
# from outside the house wins, because the phone syncs from wherever it happens to be.
# Detected sits below external: both work away from home, but an address the admin
# deliberately configured beats one inferred from a header.
URL_SOURCE_PREFERENCE = (
    URL_SOURCE_CLOUD,
    URL_SOURCE_EXTERNAL,
    URL_SOURCE_DETECTED,
    URL_SOURCE_INTERNAL,
)

# What the picker will preselect on its own. Internal is deliberately absent: it stays
# listed and one click away, but an address that only works at home has to be chosen
# on purpose rather than collected by pressing Submit. Without this, anyone reaching Home
# Assistant through a reverse proxy it can't discover is handed a 192.168.x.x URL by
# default — which is exactly how a phone ends up syncing only on home Wi-Fi.
#
# Detected is safe to preselect: it is only ever offered when it works away from home, and
# it is printed in full in the form description before anyone can submit it.
URL_SOURCE_PRESELECT = (URL_SOURCE_CLOUD, URL_SOURCE_EXTERNAL, URL_SOURCE_DETECTED)

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
