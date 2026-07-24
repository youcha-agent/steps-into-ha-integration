"""Config flow for Steps Into HA.

The whole point of this integration: the user types a name, Home Assistant mints the
webhook and shows a QR code. Nothing to paste into configuration.yaml, no restart, and
no hand-chosen webhook ID acting as a weak shared secret.
"""

from __future__ import annotations

import json
import re
from contextlib import suppress
from typing import Any
from urllib.parse import urlsplit

import voluptuous as vol
from homeassistant.components import webhook
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector
from homeassistant.helpers.network import NoURLAvailableError, get_url

from .const import (
    CONF_CLOUDHOOK,
    CONF_CUSTOM_URL,
    CONF_PERSON,
    CONF_URL_SOURCE,
    CONF_WEBHOOK_ID,
    CONF_WEBHOOK_URL,
    DOMAIN,
    PAIRING_VERSION,
    URL_SOURCE_AUTO,
    URL_SOURCE_CLOUD,
    URL_SOURCE_CUSTOM,
    URL_SOURCE_EXTERNAL,
    URL_SOURCE_INTERNAL,
    URL_SOURCE_PREFERENCE,
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


async def async_cloudhook_url(hass: HomeAssistant, webhook_id: str) -> str | None:
    """The Nabu Casa cloudhook for this webhook, or None if there isn't one.

    A cloudhook works from anywhere with no port forwarding, no reverse proxy and no
    local-HTTP exception on the phone, so it's the best address when it exists.
    """
    if "cloud" not in hass.config.components:
        return None

    from homeassistant.components import cloud

    if not cloud.async_active_subscription(hass):
        return None

    try:
        return await cloud.async_get_or_create_cloudhook(hass, webhook_id)
    except (cloud.CloudNotAvailable, cloud.CloudNotConnected):
        # A cloud hiccup must not block adding a person; the local URL still works.
        return None


async def async_resolve_webhook_url(
    hass: HomeAssistant, webhook_id: str
) -> tuple[str, bool]:
    """Return the URL the phone should post to, and whether it's a cloudhook.

    The preference order when nobody has expressed one — cloudhook, then whatever
    `webhook.async_generate_url` finds (external before internal).
    """
    if (cloudhook := await async_cloudhook_url(hass, webhook_id)) is not None:
        return cloudhook, True

    return webhook.async_generate_url(hass, webhook_id), False


async def async_url_candidates(
    hass: HomeAssistant, webhook_id: str
) -> dict[str, str]:
    """Every address this webhook can be reached at, keyed by URL_SOURCE_*.

    `webhook.async_generate_url` already prefers the external URL — but only when one is
    actually configured in Settings → System → Network. Anyone reaching Home Assistant
    through a reverse proxy it doesn't know about silently gets the internal address,
    which then only works at home. Listing the candidates explicitly turns that into a
    visible choice instead of a surprise.
    """
    candidates: dict[str, str] = {}

    if (cloudhook := await async_cloudhook_url(hass, webhook_id)) is not None:
        candidates[URL_SOURCE_CLOUD] = cloudhook

    path = webhook.async_generate_path(webhook_id)

    with suppress(NoURLAvailableError):
        candidates[URL_SOURCE_EXTERNAL] = (
            get_url(hass, allow_internal=False, allow_cloud=False) + path
        )

    with suppress(NoURLAvailableError):
        candidates[URL_SOURCE_INTERNAL] = (
            get_url(hass, allow_external=False, allow_cloud=False) + path
        )

    return candidates


def preferred_source(candidates: dict[str, str]) -> str:
    """Which candidate the picker should land on when nothing has been chosen.

    Falls back to `custom` when Home Assistant knows of no address at all — the user has
    to type one, and that's a fair thing to ask at that point.
    """
    for source in URL_SOURCE_PREFERENCE:
        if source in candidates:
            return source
    return URL_SOURCE_CUSTOM


def normalize_custom_url(value: str, webhook_id: str) -> str | None:
    """Turn what the user typed into a full webhook URL, or None if it isn't one.

    Accepts either the complete endpoint or just the base address — pasting
    `https://ha.example.com` from a browser's address bar is the obvious thing to do, so
    the webhook path gets appended rather than rejected. Mirrors what the iOS app's
    config-file mode does with its address/ID pair.
    """
    trimmed = value.strip()
    if not trimmed:
        return None

    split = urlsplit(trimmed)
    if split.scheme not in ("http", "https") or not split.netloc:
        return None

    path = webhook.async_generate_path(webhook_id)
    if path in split.path:
        return trimmed

    return trimmed.rstrip("/") + path


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
    """A display-only form: QrCodeSelector renders the code and returns no input.

    Scale and error correction are tuned for a phone camera pointed at a screen. Module
    size dominates scan reliability, and a screen doesn't smudge or tear the way print
    does — so trade quartile-level redundancy (which costs modules) for a physically
    bigger code.
    """
    return vol.Schema(
        {
            vol.Optional(FIELD_QR): selector.QrCodeSelector(
                selector.QrCodeSelectorConfig(
                    data=pairing_payload(person, url),
                    scale=10,
                    error_correction_level=selector.QrErrorCorrectionLevel.MEDIUM,
                )
            )
        }
    )


def _url_schema(
    candidates: dict[str, str], default_source: str, default_custom: str
) -> vol.Schema:
    """The address picker: the discovered candidates, plus always a free-text option."""
    options = [source for source in URL_SOURCE_PREFERENCE if source in candidates]
    options.append(URL_SOURCE_CUSTOM)

    return vol.Schema(
        {
            vol.Required(CONF_URL_SOURCE, default=default_source): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=options,
                    mode=selector.SelectSelectorMode.LIST,
                    translation_key="url_source",
                )
            ),
            vol.Optional(CONF_CUSTOM_URL, default=default_custom): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.URL)
            ),
        }
    )


def _address_list(candidates: dict[str, str]) -> str:
    """The candidate addresses as a markdown list, for the form description.

    Naming them in full makes the choice concrete — "external" means nothing until you
    can see it's the domain you actually use.
    """
    labels = {
        URL_SOURCE_CLOUD: "Home Assistant Cloud",
        URL_SOURCE_EXTERNAL: "External",
        URL_SOURCE_INTERNAL: "Internal",
    }
    lines = [
        f"- **{labels[source]}** — `{candidates[source]}`"
        for source in URL_SOURCE_PREFERENCE
        if source in candidates
    ]
    if not lines:
        return "_Home Assistant doesn't know any address for itself yet._"
    return "\n".join(lines)


class _UrlChoiceMixin:
    """The address picker, shared by the config flow and the options flow.

    Both need to ask the same question — the config flow when adding a person, the
    options flow when the answer needs revisiting — so the form and its validation live
    in one place.
    """

    hass: HomeAssistant

    _person: str
    _webhook_id: str
    _url: str
    _cloudhook: bool
    _source: str
    _custom_url: str
    _notice: str

    def _init_url_state(self) -> None:
        self._person = ""
        self._webhook_id = ""
        self._url = ""
        self._cloudhook = False
        self._source = URL_SOURCE_AUTO
        self._custom_url = ""
        self._notice = ""

    async def _async_url_form(
        self,
        step_id: str,
        user_input: dict[str, Any] | None,
        errors: dict[str, str] | None = None,
    ) -> ConfigFlowResult:
        """Render the picker, preselected from whatever is already known."""
        candidates = await async_url_candidates(self.hass, self._webhook_id)

        if self._source in (URL_SOURCE_AUTO, ""):
            default_source = preferred_source(candidates)
        elif self._source == URL_SOURCE_CUSTOM or self._source in candidates:
            default_source = self._source
        else:
            # The stored choice is gone — the external URL was cleared, or a Nabu Casa
            # subscription lapsed. Fall back, but say so rather than switching silently.
            default_source = preferred_source(candidates)
            self._notice = (
                "\n\n⚠️ The address this person was paired with is no longer available,"
                " so a different one is preselected. Re-scan the code on their phone"
                " after saving."
            )

        default_custom = self._custom_url or candidates.get(default_source, "")
        if user_input is not None:
            # Keep what they typed when re-rendering after a validation error.
            default_source = user_input.get(CONF_URL_SOURCE, default_source)
            default_custom = user_input.get(CONF_CUSTOM_URL, default_custom)

        return self.async_show_form(
            step_id=step_id,
            data_schema=_url_schema(candidates, default_source, default_custom),
            errors=errors or {},
            description_placeholders={
                "person": self._person,
                "addresses": _address_list(candidates),
                "notice": self._notice,
            },
        )

    async def _async_apply_url_choice(
        self, user_input: dict[str, Any]
    ) -> dict[str, str]:
        """Validate the submitted choice and record it. Returns any field errors."""
        candidates = await async_url_candidates(self.hass, self._webhook_id)
        source = user_input[CONF_URL_SOURCE]
        custom = (user_input.get(CONF_CUSTOM_URL) or "").strip()

        if source == URL_SOURCE_CUSTOM:
            if not custom:
                return {CONF_CUSTOM_URL: "custom_url_required"}
            url = normalize_custom_url(custom, self._webhook_id)
            if url is None:
                return {CONF_CUSTOM_URL: "invalid_url"}
        elif source in candidates:
            url = candidates[source]
        else:
            # Only reachable if the address disappeared between rendering the form and
            # submitting it.
            return {CONF_URL_SOURCE: "url_unavailable"}

        self._url = url
        self._source = source
        self._custom_url = custom
        self._cloudhook = source == URL_SOURCE_CLOUD
        self._notice = ""
        return {}

    def _entry_data(self) -> dict[str, Any]:
        """The entry payload for the choice that was just made."""
        return {
            CONF_PERSON: self._person,
            CONF_WEBHOOK_ID: self._webhook_id,
            CONF_WEBHOOK_URL: self._url,
            CONF_CLOUDHOOK: self._cloudhook,
            CONF_URL_SOURCE: self._source,
            CONF_CUSTOM_URL: self._custom_url,
        }

    def _qr_form(self, step_id: str) -> ConfigFlowResult:
        """The pairing QR, identical in both flows."""
        return self.async_show_form(
            step_id=step_id,
            data_schema=_qr_schema(self._person, self._url),
            description_placeholders={
                "person": self._person,
                "url": self._url,
                "webhook_id": self._webhook_id,
            },
        )


class StepsIntoHAConfigFlow(_UrlChoiceMixin, ConfigFlow, domain=DOMAIN):
    """Add one person (one phone) per config entry."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise the in-progress flow state."""
        self._init_url_state()

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
                return await self.async_step_url()

        schema: dict[Any, Any] = {vol.Required(CONF_PERSON): str}
        if self.show_advanced_options:
            schema[vol.Optional(CONF_WEBHOOK_ID)] = str

        return self.async_show_form(
            step_id="user", data_schema=vol.Schema(schema), errors=errors
        )

    async def async_step_url(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask which address to put in the QR code."""
        if user_input is not None:
            if not (errors := await self._async_apply_url_choice(user_input)):
                return await self.async_step_connect()
            return await self._async_url_form("url", user_input, errors)

        return await self._async_url_form("url", None)

    async def async_step_connect(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the QR code and the plain URL, then finish."""
        if user_input is not None:
            return self.async_create_entry(title=self._person, data=self._entry_data())

        return self._qr_form("connect")

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Let the user pull the QR code back up, or change the address."""
        return StepsIntoHAOptionsFlow()


class StepsIntoHAOptionsFlow(_UrlChoiceMixin, OptionsFlow):
    """Change the address, or re-show the QR for a new phone or a reinstalled app."""

    def __init__(self) -> None:
        """Initialise from the entry on first use of the picker."""
        self._init_url_state()
        self._loaded = False

    def _load_from_entry(self) -> None:
        """Seed the picker from what this person is currently paired with."""
        if self._loaded:
            return
        entry = self.config_entry
        self._person = entry.data[CONF_PERSON]
        self._webhook_id = entry.data[CONF_WEBHOOK_ID]
        self._url = entry.data.get(CONF_WEBHOOK_URL, "")
        self._cloudhook = entry.data.get(CONF_CLOUDHOOK, False)
        # Entries written before the address was a choice carry no source at all.
        self._source = entry.data.get(CONF_URL_SOURCE, URL_SOURCE_AUTO)
        self._custom_url = entry.data.get(CONF_CUSTOM_URL, "")
        self._loaded = True

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm or change which address this person's phone posts to.

        The previous version recomputed the URL here and overwrote whatever was stored,
        which would throw away a deliberate choice on every visit. Now the stored source
        is re-resolved instead, so only the address behind that choice can move.
        """
        self._load_from_entry()

        if user_input is not None:
            if not (errors := await self._async_apply_url_choice(user_input)):
                return await self.async_step_connect()
            return await self._async_url_form("init", user_input, errors)

        return await self._async_url_form("init", None)

    async def async_step_connect(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Persist the choice and show the QR to scan."""
        entry = self.config_entry
        data = {**entry.data, **self._entry_data()}
        if data != dict(entry.data):
            # Triggers async_reload_entry via the entry's update listener.
            self.hass.config_entries.async_update_entry(entry, data=data)

        if user_input is not None:
            return self.async_create_entry(data={})

        return self._qr_form("connect")
