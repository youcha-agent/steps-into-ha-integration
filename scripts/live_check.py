"""Live check: boot a real Home Assistant, run the real config flow, POST over TCP.

Unlike the pytest suite this starts an actual HomeAssistant instance against a real
config directory with real .storage persistence, so it also proves the restart/restore
path works.
"""

import asyncio
import json
import sys
from pathlib import Path

import aiohttp

CONFIG_DIR = Path(sys.argv[1])
PORT = 8123
BASE = f"http://127.0.0.1:{PORT}"

from homeassistant import config_entries, core, loader  # noqa: E402
from homeassistant.auth import auth_manager_from_config  # noqa: E402
from homeassistant.bootstrap import async_load_base_functionality  # noqa: E402
from homeassistant.setup import async_setup_component  # noqa: E402

results = []


def check(label, ok, detail=""):
    results.append((label, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {label}{'  — ' + str(detail) if detail else ''}")


async def start_hass():
    hass = core.HomeAssistant(str(CONFIG_DIR))
    hass.config.config_dir = str(CONFIG_DIR)
    hass.config.internal_url = BASE
    hass.config.api = None
    loader.async_setup(hass)
    hass.config_entries = config_entries.ConfigEntries(hass, {})
    await loader.async_get_custom_components(hass)
    # Loads the entity/device/area registries, restore_state and config entries,
    # exactly as a real boot does.
    await async_load_base_functionality(hass)
    # Real boots create the auth manager after the registries, in core_config.
    hass.auth = await auth_manager_from_config(hass, [{"type": "homeassistant"}], [])
    assert await async_setup_component(hass, "http", {"http": {"server_port": PORT}})
    # http only binds its socket on the homeassistant_start event, so start last.
    await hass.async_start()
    await hass.async_block_till_done()
    return hass


async def main():
    # ---------- first boot: add a person through the real config flow ----------
    hass = await start_hass()

    result = await hass.config_entries.flow.async_init(
        "steps_into_ha", context={"source": "user"}
    )
    check("config flow opens", result["step_id"] == "user")

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"person": "Dad"}
    )
    check("flow reaches address step", result["step_id"] == "url")

    # Only the internal URL is configured here, and an address that stops working when
    # the phone leaves the house is never preselected — so the picker lands on custom
    # with an empty box and the description says why.
    defaults = result["data_schema"]({})
    check(
        "home-only address not preselected",
        defaults["url_source"] == "custom",
        defaults["url_source"],
    )
    check(
        "form warns nothing reaches from outside",
        "away from home" in result["description_placeholders"]["addresses"],
        result["description_placeholders"]["addresses"],
    )

    # Internal is still offered — this run then takes it deliberately, because the rest
    # of the check POSTs to it over a real socket.
    options = result["data_schema"].schema["url_source"].config["options"]
    check("internal still offered", "internal" in options, options)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"url_source": "internal"}
    )
    check("flow reaches connect step", result["step_id"] == "connect")

    url = result["description_placeholders"]["url"]
    webhook_id = result["description_placeholders"]["webhook_id"]
    check("webhook URL generated", url.startswith(BASE), url)

    qr_data = result["data_schema"].schema["qr"].config["data"]
    payload = json.loads(qr_data)
    check(
        "QR carries pairing JSON",
        payload["v"] == 1 and payload["person"] == "Dad" and payload["url"] == url,
        qr_data,
    )

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    check("entry created", result["type"] == "create_entry")
    await hass.async_block_till_done()

    entity_id = "sensor.dad_steps"
    check("sensor exists", hass.states.get(entity_id) is not None)

    # ---------- real HTTP over a real socket ----------
    async with aiohttp.ClientSession() as session:
        body = {"steps": 8432, "person": "Dad", "timestamp": "2026-07-21T18:03:00Z"}
        async with session.post(url, json=body) as resp:
            text = await resp.text()
            check("POST valid payload -> 200", resp.status == 200, text.strip())

        await hass.async_block_till_done()
        state = hass.states.get(entity_id)
        check("sensor updated to 8432", state.state == "8432", state.state)
        check(
            "last_sync from payload timestamp",
            hass.states.get("sensor.dad_last_sync").state.startswith("2026-07-21T18:03"),
            hass.states.get("sensor.dad_last_sync").state,
        )

        async with session.post(url, json={"steps": "banana"}) as resp:
            check("POST bad payload -> 400", resp.status == 400, await resp.text())

        async with session.get(url) as resp:
            check("GET -> 405", resp.status == 405)

        async with session.post(
            f"{BASE}/api/webhook/{'z' * 64}", json={"steps": 1}
        ) as resp:
            await resp.text()
        await hass.async_block_till_done()
        check(
            "guessed webhook id does not touch sensor",
            hass.states.get(entity_id).state == "8432",
        )

    # ---------- restart ----------
    await hass.async_stop()

    hass = await start_hass()
    entries = hass.config_entries.async_entries("steps_into_ha")
    check("entry persisted across restart", len(entries) == 1)
    if entries:
        await hass.config_entries.async_setup(entries[0].entry_id)
        await hass.async_block_till_done()
        state = hass.states.get(entity_id)
        check(
            "step count restored after restart",
            state is not None and state.state == "8432",
            state.state if state else "missing",
        )
        check(
            "webhook id stable across restart",
            entries[0].data["webhook_id"] == webhook_id,
        )
        check(
            "address choice recorded on entry",
            entries[0].data.get("url_source") == "internal",
            entries[0].data.get("url_source"),
        )

        # ---------- change the address, and check it sticks ----------
        # The reason this exists: Configure used to recompute the URL and overwrite
        # whatever was stored, silently reverting a deliberate choice on every visit.
        custom = f"https://ha.example.com/api/webhook/{webhook_id}"
        result = await hass.config_entries.options.async_init(entries[0].entry_id)
        check("options flow opens on the picker", result["step_id"] == "init")

        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"url_source": "custom", "custom_url": custom}
        )
        check("options flow reaches connect step", result["step_id"] == "connect")
        check(
            "custom address lands in the QR",
            json.loads(result["data_schema"].schema["qr"].config["data"])["url"]
            == custom,
        )

        await hass.config_entries.options.async_configure(result["flow_id"], {})
        await hass.async_block_till_done()

        # Re-open it: the stored choice must win over the internal URL, which is still
        # the only address Home Assistant can discover for itself.
        result = await hass.config_entries.options.async_init(entries[0].entry_id)
        defaults = result["data_schema"]({})
        check(
            "custom address survives re-opening Configure",
            defaults["url_source"] == "custom" and defaults["custom_url"] == custom,
            f"{defaults['url_source']} / {defaults['custom_url']}",
        )
        hass.config_entries.options.async_abort(result["flow_id"])

    await hass.async_stop()

    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


sys.exit(asyncio.run(main()))
