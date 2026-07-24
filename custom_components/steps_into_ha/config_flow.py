"""Config flow for Steps Into HA.

The whole point of this integration: the user types a name, Home Assistant mints the
webhook and shows a QR code. Nothing to paste into configuration.yaml, no restart, and
no hand-chosen webhook ID acting as a weak shared secret.
"""

from __future__ import annotations

import json
import re
from typing import Any

import voluptuous as vol
from homeassistant.components import webhook
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector

from .const import (
    CONF_CLOUDHOOK,
    CONF_PERSON,
    CONF_WEBHOOK_ID,
    CONF_WEBHOOK_URL,
    DOMAIN,
    PAIRING_VERSION,
)

# A custom ID is offered only under "advanced mode", for people running the v1.0 app
# that still asks for the ID as a separate typed field. Minimum length because this
# value is the only thing protecting the sensor from anyone who can reach the URL.
WEBHOOK_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

FIELD_QR = "qr"


def pairing_payload(person: str, url: str) -> str:
    """Build the string encoded into the QR code.

    JSON rather than a bare URL for two reasons: the app can prefill the person's name,
    and someone who scans this with the stock iOS Camera sees inert text instead of
    being sent to a POST-only endpoint that would answer 405.
    """
    return json.dumps(
        {"v": PAIRING_VERSION, "url": url, "person": person}, separators=(",", ":")
    )


async def async_resolve_webhook_url(
    hass: HomeAssistant, webhook_id: str
) -> tuple[str, bool]:
    """Return the URL the phone should post to, and whether it's a cloudhook.

    With Nabu Casa the cloudhook wins: it works from anywhere with no port forwarding,
    no reverse proxy and no local-HTTP exception on the phone.
    """
    if "cloud" in hass.config.components:
        from homeassistant.components import cloud

        if cloud.async_active_subscription(hass):
            try:
                return await cloud.async_get_or_create_cloudhook(hass, webhook_id), True
            except (cloud.CloudNotAvailable, cloud.CloudNotConnected):
                pass

    return webhook.async_generate_url(hass, webhook_id), False


@callback
def async_is_webhook_id_taken(hass: HomeAssistant, webhook_id: str) -> bool:
    """Whether anything in Home Assistant already owns this webhook ID.

    Matters because a `template:` webhook trigger in configuration.yaml holds an ID too,
    and those aren't config entries — so the flow's own duplicate check can't see them.
    Someone running both setup routes could otherwise pick the same ID twice and get a
    config entry that just fails to start.

    There's no public "is this taken" helper, so register a no-op and immediately drop it.
    Both calls are synchronous with no await between them, so the momentary registration
    can't receive a request.
    """

    async def _probe(hass: HomeAssistant, webhook_id: str, request: Any) -> None:
        return None

    try:
        webhook.async_register(hass, DOMAIN, "probe", webhook_id, _probe)
    except ValueError:
        return True

    webhook.async_unregister(hass, webhook_id)
    return False


def _qr_schema(person: str, url: str) -> vol.Schema:
    """A display-only form: QrCodeSelector renders the code and returns no input."""
    return vol.Schema(
        {
            vol.Optional(FIELD_QR): selector.QrCodeSelector(
                selector.QrCodeSelectorConfig(
                    data=pairing_payload(person, url),
                    scale=6,
                    error_correction_level=selector.QrErrorCorrectionLevel.QUARTILE,
                )
            )
        }
    )


class StepsIntoHAConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add one person (one phone) per config entry."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise the in-progress flow state."""
        self._person: str = ""
        self._webhook_id: str = ""
        self._url: str = ""
        self._cloudhook: bool = False

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask who this entry is for, and mint the webhook."""
        errors: dict[str, str] = {}

        if user_input is not None:
            person = user_input[CONF_PERSON].strip()
            custom_id = (user_input.get(CONF_WEBHOOK_ID) or "").strip()

            if not person:
                errors[CONF_PERSON] = "invalid_person"
            if custom_id and not WEBHOOK_ID_PATTERN.match(custom_id):
                errors[CONF_WEBHOOK_ID] = "invalid_webhook_id"

            if not errors:
                webhook_id = custom_id or webhook.async_generate_id()
                await self.async_set_unique_id(webhook_id)
                self._abort_if_unique_id_configured()

            if not errors and async_is_webhook_id_taken(self.hass, webhook_id):
                # Only reachable for a hand-picked ID; a generated one is 64 random hex
                # characters. Almost always a clash with a template: block in YAML.
                errors[CONF_WEBHOOK_ID] = "webhook_id_in_use"

            if not errors:
                self._person = person
                self._webhook_id = webhook_id
                self._url, self._cloudhook = await async_resolve_webhook_url(
                    self.hass, webhook_id
                )
                return await self.async_step_connect()

        schema: dict[Any, Any] = {vol.Required(CONF_PERSON): str}
        if self.show_advanced_options:
            schema[vol.Optional(CONF_WEBHOOK_ID)] = str

        return self.async_show_form(
            step_id="user", data_schema=vol.Schema(schema), errors=errors
        )

    async def async_step_connect(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the QR code and the plain URL, then finish."""
        if user_input is not None:
            return self.async_create_entry(
                title=self._person,
                data={
                    CONF_PERSON: self._person,
                    CONF_WEBHOOK_ID: self._webhook_id,
                    CONF_WEBHOOK_URL: self._url,
                    CONF_CLOUDHOOK: self._cloudhook,
                },
            )

        return self.async_show_form(
            step_id="connect",
            data_schema=_qr_schema(self._person, self._url),
            description_placeholders={
                "person": self._person,
                "url": self._url,
                "webhook_id": self._webhook_id,
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> OptionsFlow:
        """Let the user pull the QR code back up later."""
        return StepsIntoHAOptionsFlow()


class StepsIntoHAOptionsFlow(OptionsFlow):
    """Re-shows the pairing QR — for a new phone, or a reinstalled app."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the QR again, refreshing the URL if it has changed."""
        if user_input is not None:
            return self.async_create_entry(data={})

        entry = self.config_entry
        webhook_id = entry.data[CONF_WEBHOOK_ID]

        # The right URL can change after setup — the user may have added Nabu Casa, or
        # changed their external URL. Recompute rather than showing a stale QR.
        url, cloudhook = await async_resolve_webhook_url(self.hass, webhook_id)
        if url != entry.data.get(CONF_WEBHOOK_URL):
            self.hass.config_entries.async_update_entry(
                entry,
                data={
                    **entry.data,
                    CONF_WEBHOOK_URL: url,
                    CONF_CLOUDHOOK: cloudhook,
                },
            )

        person = entry.data[CONF_PERSON]
        return self.async_show_form(
            step_id="init",
            data_schema=_qr_schema(person, url),
            description_placeholders={
                "person": person,
                "url": url,
                "webhook_id": webhook_id,
            },
        )
