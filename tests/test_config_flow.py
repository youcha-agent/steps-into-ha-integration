"""Tests for the Steps Into HA config flow."""

from __future__ import annotations

import json
from contextlib import contextmanager
from unittest.mock import Mock, patch

import pytest
import yarl
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.http import current_request
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.steps_into_ha.config_flow import (
    async_resolve_webhook_url,
    async_url_candidates,
    is_home_only_url,
    normalize_custom_url,
    pairing_payload,
)
from custom_components.steps_into_ha.const import (
    CONF_CLOUDHOOK,
    CONF_CUSTOM_URL,
    CONF_PERSON,
    CONF_URL_SOURCE,
    CONF_WEBHOOK_ID,
    CONF_WEBHOOK_URL,
    DOMAIN,
    URL_SOURCE_CLOUD,
    URL_SOURCE_CUSTOM,
    URL_SOURCE_DETECTED,
    URL_SOURCE_EXTERNAL,
    URL_SOURCE_INTERNAL,
)

from .conftest import CLOUDHOOK_URL, WEBHOOK_ID

EXTERNAL_BASE = "https://ha.example.com"
PROXY_BASE = "https://ha.proxy.example.com"


@contextmanager
def browsing_at(url: str, **headers: str):
    """Run the block as if the admin had loaded the page at `url`.

    The config flow is served over the REST API, so `current_request` is set for real in
    production. Tests and scripts drive the flow with no request in context at all, which
    is why every test that doesn't use this sees no detected address.
    """
    request = Mock(url=yarl.URL(url), headers=headers)
    token = current_request.set(request)
    try:
        yield
    finally:
        current_request.reset(token)


@pytest.fixture(autouse=True)
def mock_setup_entry():
    """Don't actually start the integration during flow tests."""
    with patch(
        "custom_components.steps_into_ha.async_setup_entry", return_value=True
    ) as mock:
        yield mock


async def _reach_url_step(hass, person: str = "Dad", **context):
    """Run the name step and stop on the address picker."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user", **context}
    )
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PERSON: person}
    )


async def _accept_default_address(manager, result):
    """Submit the address picker with whatever it preselected.

    Takes the flow manager because the same picker is served by the config flow
    (`hass.config_entries.flow`) and the options flow (`hass.config_entries.options`).

    Only usable where the default is a real address — with no external URL and no
    cloudhook the picker deliberately lands on an empty `custom`, which won't submit.
    Use `_choose_internal_address` for tests that are only passing through the picker.
    """
    defaults = result["data_schema"]({})
    return await manager.async_configure(
        result["flow_id"],
        {
            CONF_URL_SOURCE: defaults[CONF_URL_SOURCE],
            CONF_CUSTOM_URL: defaults[CONF_CUSTOM_URL],
        },
    )


async def _choose_internal_address(manager, result):
    """Submit the address picker on the internal address, explicitly.

    The picker no longer preselects internal — syncing at home only has to be a decision,
    not the consequence of pressing Submit — so tests whose subject is something else say
    out loud which address they mean.
    """
    return await manager.async_configure(
        result["flow_id"], {CONF_URL_SOURCE: URL_SOURCE_INTERNAL}
    )


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
    assert result["step_id"] == "url"

    result = await _choose_internal_address(hass.config_entries.flow, result)
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
    result = await _reach_url_step(hass, "  Dad  ")
    result = await _choose_internal_address(hass.config_entries.flow, result)
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
    assert result["step_id"] == "url"

    result = await _choose_internal_address(hass.config_entries.flow, result)
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
    result = await _reach_url_step(hass)
    result = await _choose_internal_address(hass.config_entries.flow, result)

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
    result = await _reach_url_step(hass)
    result = await _accept_default_address(hass.config_entries.flow, result)
    assert result["description_placeholders"]["url"] == CLOUDHOOK_URL

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["data"][CONF_WEBHOOK_URL] == CLOUDHOOK_URL
    assert result["data"][CONF_CLOUDHOOK] is True
    assert result["data"][CONF_URL_SOURCE] == URL_SOURCE_CLOUD


# ---------------------------------------------------------------------------
# Choosing the address
# ---------------------------------------------------------------------------


async def test_candidates_omit_external_when_unset(hass) -> None:
    """Most installs never fill in the external URL; don't offer one that isn't there."""
    candidates = await async_url_candidates(hass, WEBHOOK_ID)

    assert URL_SOURCE_EXTERNAL not in candidates
    assert candidates[URL_SOURCE_INTERNAL] == (
        f"http://10.0.0.2:8123/api/webhook/{WEBHOOK_ID}"
    )
    assert URL_SOURCE_CLOUD not in candidates


async def test_candidates_include_external_when_set(hass) -> None:
    """Both addresses are offered when Home Assistant knows both."""
    hass.config.external_url = EXTERNAL_BASE

    candidates = await async_url_candidates(hass, WEBHOOK_ID)

    assert candidates[URL_SOURCE_EXTERNAL] == f"{EXTERNAL_BASE}/api/webhook/{WEBHOOK_ID}"
    assert candidates[URL_SOURCE_INTERNAL] == (
        f"http://10.0.0.2:8123/api/webhook/{WEBHOOK_ID}"
    )


async def test_candidates_include_cloudhook_when_subscribed(hass, mock_cloud) -> None:
    """A cloudhook is a candidate in its own right, not a replacement for the others."""
    candidates = await async_url_candidates(hass, WEBHOOK_ID)

    assert candidates[URL_SOURCE_CLOUD] == CLOUDHOOK_URL
    assert URL_SOURCE_INTERNAL in candidates


async def test_external_is_preselected(hass) -> None:
    """The whole point: if there's an address that works from outside, default to it."""
    hass.config.external_url = EXTERNAL_BASE

    result = await _reach_url_step(hass)

    assert result["step_id"] == "url"
    assert result["data_schema"]({})[CONF_URL_SOURCE] == URL_SOURCE_EXTERNAL


async def test_cloud_is_preselected_over_external(hass, mock_cloud) -> None:
    """A cloudhook needs no port forwarding at all, so it outranks a plain domain."""
    hass.config.external_url = EXTERNAL_BASE

    result = await _reach_url_step(hass)

    assert result["data_schema"]({})[CONF_URL_SOURCE] == URL_SOURCE_CLOUD


async def test_custom_is_preselected_when_only_internal_exists(hass) -> None:
    """The regression this exists to prevent, reported from a real pairing.

    With Home Assistant behind a reverse proxy it can't discover, internal is the only
    address it knows — and preselecting it meant pressing Submit handed the phone a
    192.168.x.x URL that stopped working the moment its owner left the house. Land on
    custom instead, so the home-only answer has to be given rather than defaulted into.
    """
    result = await _reach_url_step(hass)

    assert result["data_schema"]({})[CONF_URL_SOURCE] == URL_SOURCE_CUSTOM
    # Still offered, just not by default — plenty of people only want this at home.
    options = result["data_schema"].schema[CONF_URL_SOURCE].config["options"]
    assert URL_SOURCE_INTERNAL in options
    # And the form says out loud why it didn't pick one for you.
    assert "away from home" in result["description_placeholders"]["addresses"]


async def test_no_home_only_warning_when_external_exists(hass) -> None:
    """The warning is about having nothing better, so don't cry wolf."""
    hass.config.external_url = EXTERNAL_BASE

    result = await _reach_url_step(hass)

    assert "away from home" not in result["description_placeholders"]["addresses"]


async def test_choosing_internal_still_works(hass) -> None:
    """Discouraged is not disallowed: home-only sync is a legitimate choice."""
    result = await _reach_url_step(hass)
    result = await _choose_internal_address(hass.config_entries.flow, result)

    assert result["step_id"] == "connect"
    webhook_id = result["description_placeholders"]["webhook_id"]
    expected = f"http://10.0.0.2:8123/api/webhook/{webhook_id}"
    assert result["description_placeholders"]["url"] == expected

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["data"][CONF_WEBHOOK_URL] == expected
    assert result["data"][CONF_URL_SOURCE] == URL_SOURCE_INTERNAL


async def test_choosing_external_puts_it_in_the_qr(hass) -> None:
    """Picking external must actually change what the phone gets handed."""
    hass.config.external_url = EXTERNAL_BASE
    result = await _reach_url_step(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL_SOURCE: URL_SOURCE_EXTERNAL}
    )
    webhook_id = result["description_placeholders"]["webhook_id"]
    expected = f"{EXTERNAL_BASE}/api/webhook/{webhook_id}"

    payload = json.loads(result["data_schema"].schema["qr"].config["data"])
    assert payload["url"] == expected

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["data"][CONF_WEBHOOK_URL] == expected
    assert result["data"][CONF_URL_SOURCE] == URL_SOURCE_EXTERNAL
    assert result["data"][CONF_CLOUDHOOK] is False


async def test_custom_base_url_gets_the_webhook_path(hass) -> None:
    """Pasting what's in the browser address bar is the obvious thing to do."""
    result = await _reach_url_step(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_URL_SOURCE: URL_SOURCE_CUSTOM, CONF_CUSTOM_URL: "https://ha.example.com/"},
    )
    assert result["step_id"] == "connect"

    webhook_id = result["description_placeholders"]["webhook_id"]
    assert result["description_placeholders"]["url"] == (
        f"https://ha.example.com/api/webhook/{webhook_id}"
    )


async def test_custom_full_url_is_left_alone(hass) -> None:
    """Someone who pastes the complete endpoint must not get the path twice."""
    # Advanced mode so the webhook ID is known before the address is chosen.
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user", "show_advanced_options": True}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PERSON: "Dad", CONF_WEBHOOK_ID: "dad-4f2a91"}
    )
    full = "https://ha.example.com/api/webhook/dad-4f2a91"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL_SOURCE: URL_SOURCE_CUSTOM, CONF_CUSTOM_URL: full}
    )
    assert result["description_placeholders"]["url"] == full


@pytest.mark.parametrize(
    "bad",
    ["homeassistant.local:8123", "file:///etc/passwd", "https://"],
    ids=["no_scheme", "wrong_scheme", "no_host"],
)
async def test_invalid_custom_url_rejected(hass, bad: str) -> None:
    """A bad address here means a phone that silently never syncs."""
    result = await _reach_url_step(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL_SOURCE: URL_SOURCE_CUSTOM, CONF_CUSTOM_URL: bad}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "url"
    assert result["errors"] == {CONF_CUSTOM_URL: "invalid_url"}


@pytest.mark.parametrize("empty", ["", "   "], ids=["empty", "whitespace"])
async def test_custom_selected_without_a_url(hass, empty: str) -> None:
    """Picking Custom and leaving the box empty is a different mistake to a bad URL."""
    result = await _reach_url_step(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL_SOURCE: URL_SOURCE_CUSTOM, CONF_CUSTOM_URL: empty}
    )
    assert result["errors"] == {CONF_CUSTOM_URL: "custom_url_required"}


def test_normalize_custom_url_trims_trailing_slashes() -> None:
    """No double slash before the webhook path."""
    assert normalize_custom_url("https://ha.example.com///", "abc") == (
        "https://ha.example.com/api/webhook/abc"
    )


# ---------------------------------------------------------------------------
# Options flow
# ---------------------------------------------------------------------------


async def test_options_flow_reshows_qr(hass, config_entry: MockConfigEntry) -> None:
    """ "I got a new phone" shouldn't mean deleting and recreating the person."""
    config_entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    assert result["description_placeholders"]["person"] == "Dad"

    result = await _choose_internal_address(hass.config_entries.options, result)
    assert result["step_id"] == "connect"
    assert "qr" in result["data_schema"].schema


async def test_options_flow_refreshes_changed_url(
    hass, config_entry: MockConfigEntry
) -> None:
    """A stale URL in the QR would silently pair the phone to nowhere."""
    config_entry.add_to_hass(hass)
    hass.config.internal_url = "http://10.0.0.9:8123"

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await _choose_internal_address(hass.config_entries.options, result)

    expected = f"http://10.0.0.9:8123/api/webhook/{WEBHOOK_ID}"
    assert result["description_placeholders"]["url"] == expected
    assert config_entry.data[CONF_WEBHOOK_URL] == expected


async def test_options_flow_asks_again_for_a_pre_choice_entry(
    hass, config_entry: MockConfigEntry
) -> None:
    """The upgrade path for anyone paired before the address was a choice.

    Their entry carries `auto` and a URL that came out of `async_generate_url` — which,
    with no external URL configured, means the internal one. Opening Configure has to be
    the moment they get asked, not a form that re-offers what they already have.
    """
    config_entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(config_entry.entry_id)

    assert result["data_schema"]({})[CONF_URL_SOURCE] == URL_SOURCE_CUSTOM
    assert result["data_schema"]({})[CONF_CUSTOM_URL] == ""
    assert "away from home" in result["description_placeholders"]["addresses"]

    # Pasting a reverse-proxy base is enough; the webhook path is appended.
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_URL_SOURCE: URL_SOURCE_CUSTOM, CONF_CUSTOM_URL: EXTERNAL_BASE},
    )

    expected = f"{EXTERNAL_BASE}/api/webhook/{WEBHOOK_ID}"
    assert result["description_placeholders"]["url"] == expected
    assert config_entry.data[CONF_WEBHOOK_URL] == expected
    assert config_entry.data[CONF_URL_SOURCE] == URL_SOURCE_CUSTOM


async def test_options_flow_preselects_stored_source(
    hass, config_entry: MockConfigEntry
) -> None:
    """Re-opening Configure comes back on the choice that was made, not the default."""
    config_entry.add_to_hass(hass)
    hass.config.external_url = EXTERNAL_BASE
    hass.config_entries.async_update_entry(
        config_entry,
        data={**config_entry.data, CONF_URL_SOURCE: URL_SOURCE_INTERNAL},
    )

    result = await hass.config_entries.options.async_init(config_entry.entry_id)

    # External exists and would otherwise win — the stored choice has to beat it.
    assert result["data_schema"]({})[CONF_URL_SOURCE] == URL_SOURCE_INTERNAL


async def test_options_flow_keeps_a_custom_url(
    hass, config_entry: MockConfigEntry
) -> None:
    """The regression this exists to prevent: Configure used to clobber a custom URL."""
    config_entry.add_to_hass(hass)
    custom = f"https://ha.example.com/api/webhook/{WEBHOOK_ID}"
    hass.config_entries.async_update_entry(
        config_entry,
        data={
            **config_entry.data,
            CONF_WEBHOOK_URL: custom,
            CONF_URL_SOURCE: URL_SOURCE_CUSTOM,
            CONF_CUSTOM_URL: custom,
        },
    )

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["data_schema"]({})[CONF_URL_SOURCE] == URL_SOURCE_CUSTOM
    assert result["data_schema"]({})[CONF_CUSTOM_URL] == custom

    result = await _accept_default_address(hass.config_entries.options, result)
    assert result["description_placeholders"]["url"] == custom
    assert config_entry.data[CONF_WEBHOOK_URL] == custom


async def test_options_flow_falls_back_when_source_vanishes(
    hass, config_entry: MockConfigEntry
) -> None:
    """A lapsed subscription shouldn't strand the entry on an address that's gone."""
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        config_entry,
        data={**config_entry.data, CONF_URL_SOURCE: URL_SOURCE_CLOUD},
    )

    result = await hass.config_entries.options.async_init(config_entry.entry_id)

    # Falls back the same way a fresh flow lands: nothing here reaches this phone from
    # outside, so ask rather than quietly re-pair it to a home-only address.
    assert result["data_schema"]({})[CONF_URL_SOURCE] == URL_SOURCE_CUSTOM
    assert result["description_placeholders"]["notice"]


# ---------------------------------------------------------------------------
# The address the browser is using
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://192.168.1.5:8123/api/webhook/x", True),
        ("http://10.0.0.2:8123/api/webhook/x", True),
        ("http://172.20.0.4:8123/api/webhook/x", True),
        ("http://127.0.0.1:8123/api/webhook/x", True),
        ("http://169.254.4.4:8123/api/webhook/x", True),
        ("http://homeassistant.local:8123/api/webhook/x", True),
        ("http://localhost:8123/api/webhook/x", True),
        ("https://ha.example.com/api/webhook/x", False),
        # A hostname that merely starts like a private range is not one.
        ("https://10.example.com/api/webhook/x", False),
        ("", False),
    ],
)
def test_is_home_only_url(url: str, expected: bool) -> None:
    """Same rule as WebhookURL.isHomeOnly in the app, so both ends agree."""
    assert is_home_only_url(url) is expected


async def test_detected_address_offered_when_no_external_url(hass) -> None:
    """The reason this exists, reported from a real setup.

    Home Assistant behind a reverse proxy it was never told about has no external URL to
    find — but the admin is looking at this very form through that proxy, so the request
    carries the address `hass.config` doesn't have.
    """
    with browsing_at(f"{PROXY_BASE}/config/integrations"):
        candidates = await async_url_candidates(hass, WEBHOOK_ID)

    assert candidates[URL_SOURCE_DETECTED] == f"{PROXY_BASE}/api/webhook/{WEBHOOK_ID}"
    assert URL_SOURCE_EXTERNAL not in candidates


async def test_detected_address_is_preselected_and_named(hass) -> None:
    """It has to land on it, not merely offer it — an empty Custom box is where we were."""
    with browsing_at(f"{PROXY_BASE}/config/integrations"):
        result = await _reach_url_step(hass)

    assert result["data_schema"]({})[CONF_URL_SOURCE] == URL_SOURCE_DETECTED
    addresses = result["description_placeholders"]["addresses"]
    assert PROXY_BASE in addresses
    # Something here works away from home now, so the warning must stand down.
    assert "away from home" not in addresses


@pytest.mark.parametrize(
    "url",
    [
        "http://192.168.1.5:8123/",
        "http://homeassistant.local:8123/",
        "http://localhost:8123/",
    ],
)
async def test_detected_address_skipped_when_home_only(hass, url: str) -> None:
    """Browsing from the sofa detects the internal address, which helps nobody."""
    with browsing_at(url):
        candidates = await async_url_candidates(hass, WEBHOOK_ID)

    assert URL_SOURCE_DETECTED not in candidates


async def test_detected_address_skipped_when_it_duplicates_another(hass) -> None:
    """Once the external URL is configured, this is usually the same address again."""
    hass.config.external_url = EXTERNAL_BASE

    with browsing_at(f"{EXTERNAL_BASE}/config/integrations"):
        candidates = await async_url_candidates(hass, WEBHOOK_ID)

    assert URL_SOURCE_DETECTED not in candidates
    assert candidates[URL_SOURCE_EXTERNAL] == (
        f"{EXTERNAL_BASE}/api/webhook/{WEBHOOK_ID}"
    )


async def test_detected_address_prefers_forwarded_headers(hass) -> None:
    """The common case: a proxy that terminates TLS and Home Assistant not told about it.

    Without `use_x_forwarded_for` and `trusted_proxies`, Home Assistant's own middleware
    doesn't rewrite the request — so the connection reads as plain http to an internal
    host, and only the headers know what the browser actually used.
    """
    with browsing_at(
        "http://10.0.0.2:8123/config/integrations",
        **{
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "ha.proxy.example.com",
        },
    ):
        candidates = await async_url_candidates(hass, WEBHOOK_ID)

    assert candidates[URL_SOURCE_DETECTED] == f"{PROXY_BASE}/api/webhook/{WEBHOOK_ID}"


async def test_choosing_detected_stores_it_as_a_custom_address(hass) -> None:
    """Picked once, frozen — `detected` is never written to the entry.

    It comes off a request header, so leaving it as the stored source would let a later
    visit to Configure from a different address silently re-point a working phone.
    """
    with browsing_at(f"{PROXY_BASE}/config/integrations"):
        result = await _reach_url_step(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_URL_SOURCE: URL_SOURCE_DETECTED}
        )

        webhook_id = result["description_placeholders"]["webhook_id"]
        expected = f"{PROXY_BASE}/api/webhook/{webhook_id}"
        payload = json.loads(result["data_schema"].schema["qr"].config["data"])
        assert payload["url"] == expected

        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["data"][CONF_WEBHOOK_URL] == expected
    assert result["data"][CONF_URL_SOURCE] == URL_SOURCE_CUSTOM
    assert result["data"][CONF_CUSTOM_URL] == expected
    assert result["data"][CONF_CLOUDHOOK] is False


async def test_detected_address_survives_configure_from_elsewhere(
    hass, config_entry: MockConfigEntry
) -> None:
    """Having been frozen as custom, a later visit from another address can't move it."""
    config_entry.add_to_hass(hass)
    paired = f"{PROXY_BASE}/api/webhook/{WEBHOOK_ID}"
    hass.config_entries.async_update_entry(
        config_entry,
        data={
            **config_entry.data,
            CONF_WEBHOOK_URL: paired,
            CONF_URL_SOURCE: URL_SOURCE_CUSTOM,
            CONF_CUSTOM_URL: paired,
        },
    )

    with browsing_at("https://other.example.com/config/integrations"):
        result = await hass.config_entries.options.async_init(config_entry.entry_id)
        assert result["data_schema"]({})[CONF_URL_SOURCE] == URL_SOURCE_CUSTOM
        assert result["data_schema"]({})[CONF_CUSTOM_URL] == paired

        result = await _accept_default_address(hass.config_entries.options, result)

    assert result["description_placeholders"]["url"] == paired
    assert config_entry.data[CONF_WEBHOOK_URL] == paired


async def test_custom_box_is_never_prefilled_with_a_home_only_address(
    hass, config_entry: MockConfigEntry
) -> None:
    """The second half of what was reported: Custom arrived pre-loaded with 192.168.x.x.

    Selecting Custom and pressing Submit then stored a home-only address as a deliberate
    custom choice — straight around the guard that exists to prevent exactly that.
    """
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        config_entry,
        data={**config_entry.data, CONF_URL_SOURCE: URL_SOURCE_INTERNAL},
    )

    result = await hass.config_entries.options.async_init(config_entry.entry_id)

    assert result["data_schema"]({})[CONF_URL_SOURCE] == URL_SOURCE_INTERNAL
    assert result["data_schema"]({})[CONF_CUSTOM_URL] == ""


async def test_custom_box_is_prefilled_with_the_detected_address(
    hass, config_entry: MockConfigEntry
) -> None:
    """One click from an entry stuck on internal to the address that actually works."""
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        config_entry,
        data={**config_entry.data, CONF_URL_SOURCE: URL_SOURCE_INTERNAL},
    )

    with browsing_at(f"{PROXY_BASE}/config/integrations"):
        result = await hass.config_entries.options.async_init(config_entry.entry_id)

    assert result["data_schema"]({})[CONF_CUSTOM_URL] == (
        f"{PROXY_BASE}/api/webhook/{WEBHOOK_ID}"
    )
