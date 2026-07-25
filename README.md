# Steps Into HA — Home Assistant integration

[![HACS Custom][hacs-badge]][hacs]
[![Validate][validate-badge]][validate]

## 🎯 Goal

**Your family's daily step counts, on your Home Assistant dashboard.**

## ✅ 5 steps

Do these five things. Nothing else on this page is required.

**1.** Install this integration in HACS → **restart Home Assistant.**

**2.** Go to **Settings → Devices & Services → Add Integration → Steps Into HA.**
Type a person's name.

**3.** Pick the **address** their phone should use. Pick one that says *works from anywhere*.
→ A **QR code** appears.

**4.** Install the **[Steps Into HA app][apprepo]** on that person's iPhone. Allow Health access.

**5.** In the app, **scan the QR code.**

**Done.** You now have `sensor.<name>_steps`, updating about once an hour.

**Another person?** Repeat steps 2–5. One entry per phone.

**Stuck?** Everything below is detail. [Which address do I pick?](#which-address-step-3) ·
[It's not working](#troubleshooting) · [Make a chart](#a-family-chart)

--------------------------------------------------------------------------------

<!-- TODO: screenshot of the QR pairing step and a family bar chart -->

## What this is

Pairs with the [**Steps Into HA**][apprepo] iOS app, which reads your step count from Apple
Health and pushes it to your own Home Assistant — no third-party servers, no account, no
subscription.

> **The iOS app is awaiting App Store review.** This integration works today with any build
> of the app.
>
> **If your build has no QR scanner** — it asks for a "Home Assistant address" and a
> "Webhook ID" as separate fields — turn on **Advanced Mode** in your Home Assistant user
> profile *before* step 2. The setup form then lets you pick your own webhook ID, short
> enough to type by hand. Make it unguessable: it's the only thing protecting the sensor.

---

## Which address? (step 3)

The phone sends its step count from wherever it happens to be — the shops, work, school. So
the address in the QR code has to reach Home Assistant **from outside your home**, or that
person will only sync when they're on your Wi-Fi.

| Option | Use it when |
|---|---|
| **Home Assistant Cloud** | You have a Nabu Casa subscription. A cloudhook is created for you: works from anywhere, no port forwarding, nothing exposed to the internet. Preselected when available. |
| **External** | You've filled in Settings → System → Network → **External URL**. Preselected when there's no cloudhook. |
| **The address you're using right now** | Offered when you opened this page on an address Home Assistant doesn't otherwise know — through a reverse proxy, say. It's the address in your browser's bar, so if that works from outside, so will this. |
| **Internal** | You only want syncing at home, or you're testing. Never preselected — an address that stops working away from home has to be chosen on purpose. |
| **Custom** | Anything else: your own domain, DuckDNS, Tailscale. Paste just the base address — `https://ha.example.com` — and the webhook path is added for you. |

**Reaching Home Assistant through a reverse proxy?** It has no way to discover that on its
own, so **External** stays missing until you fill in Settings → System → Network →
**External URL**. Worth doing — it fixes every other integration that hands out a link too.
Until then, set up the phone while you're browsing through the proxy and its address is
offered directly; or pick **Custom** and paste it.

If nothing Home Assistant can find works away from home, the form lands on **Custom** with
the box prefilled where possible, and says so — rather than preselecting an address that
only works on your Wi-Fi.

You can change this later from the integration's **Configure** button, which shows a fresh
QR code to re-scan.

> **No address step at all?** You're on a version before 1.1.0, which paired phones to
> whatever address Home Assistant happened to pick — usually the internal one. Update in
> HACS and **restart Home Assistant**; the step won't appear until the restart. Then open
> **Configure** on each person, choose the address, and re-scan the new code.

---

## What you get

Each person becomes a device with two entities:

| Entity | What it is |
|---|---|
| `sensor.<name>_steps` | Today's step count. `state_class: total_increasing`, so long-term statistics and the midnight reset work correctly. |
| `sensor.<name>_last_sync` | When that phone last reached Home Assistant. Diagnostic — the quick answer to "is it actually syncing?" |

The step count survives a Home Assistant restart, rather than going unknown until the phone's
next hourly push.

---

## A family chart

Install [ApexCharts Card][apexcharts] from HACS and add this as a manual card:

```yaml
type: custom:apexcharts-card
header:
  show: true
  title: Family Steps
  show_states: true
  colorize_states: true
graph_span: 7d
span:
  end: day
chart_type: bar
apex_config:
  chart:
    height: 320
  plotOptions:
    bar:
      columnWidth: 70%
  dataLabels:
    enabled: false
  xaxis:
    labels:
      format: ddd
all_series_config:
  type: column
  group_by:
    # A day's step count is cumulative, so take the highest value seen that day.
    func: max
    duration: 1d
series:
  - entity: sensor.person_1_steps
    name: Person 1
    color: "#34b95c"
  - entity: sensor.person_2_steps
    name: Person 2
    color: "#3486eb"
```

Edit the `series` list to match your family's entity IDs.

---

## Running alongside the YAML setup

You can use both at once — the integration for some family members, a `template:` webhook
block in `configuration.yaml` for others. They're independent, and each person's sensor
updates on its own.

The **one** thing you can't do is give both the same `webhook_id`. Home Assistant allows a
single owner per webhook, so the second one to load fails. You'll only ever hit this if you
hand-pick an ID under Advanced Mode; the generated ones are 64 random hex characters. The
setup form refuses an ID that's already taken, and if a `configuration.yaml` block claims
one later, the integration fails with a message saying so rather than a traceback.

## Upgrading from the YAML setup

Earlier versions of the app asked you to paste a `template:` block into
`configuration.yaml`. To move over:

1. Add each person through the integration (step 2 above).
2. Point your dashboard cards at the new entity IDs.
3. Delete the old `template:` webhook blocks from `configuration.yaml` and restart.

History from the old sensors stays under their old entity IDs; it doesn't move to the new
ones. If you'd rather keep an unbroken chart, rename the old entities out of the way *before*
adding the integration and give the new sensors the old IDs.

---

## Troubleshooting

**The app says "Sent" but nothing appears.** Check the integration's *Last sync* entity. If it
never updates, the phone isn't reaching Home Assistant — most often because the QR code holds
your internal URL and the phone is off your Wi-Fi.

**HTTP 400 from the app.** The webhook rejected the payload. Enable debug logging to see why:

```yaml
logger:
  logs:
    custom_components.steps_into_ha: debug
```

**HTTP 404 or 405.** The webhook ID doesn't match, or something is sending a `GET`. Only
`POST` is accepted. Re-open **Configure** on the integration to see the correct URL.

**A step count that never moves.** iOS throttles HealthKit background delivery to roughly
hourly. That's an app-side constraint, not something this integration can change.

---

## Privacy

This integration receives step counts sent directly from your phone to your Home Assistant.
It makes no outbound connections, has no analytics, and talks to no third-party service. See
the app's [privacy policy][privacy].

## Manual installation

Copy `custom_components/steps_into_ha/` into your Home Assistant `config/custom_components/`
directory and restart.

## Development

```bash
python -m venv .venv && .venv/bin/pip install -r requirements_test.txt
.venv/bin/pytest                       # unit + webhook tests, in-process Home Assistant
.venv/bin/ruff check custom_components tests

# Boots a real Home Assistant against a throwaway config dir, drives the config flow,
# and posts over a real socket — including a restart to check state restore.
PATH="$PWD/.venv/bin:$PATH" ./scripts/run_live_check.sh
```

## Related

- [Steps Into HA iOS app][apprepo] — App Store listing pending review

[apprepo]: https://github.com/youcha-agent/steps-into-ha
[privacy]: https://github.com/youcha-agent/steps-into-ha/blob/main/PRIVACY.md
[apexcharts]: https://github.com/RomRider/apexcharts-card
[hacs]: https://hacs.xyz
[hacs-badge]: https://img.shields.io/badge/HACS-Custom-41BDF5.svg
[validate]: https://github.com/youcha-agent/steps-into-ha-integration/actions/workflows/validate.yml
[validate-badge]: https://github.com/youcha-agent/steps-into-ha-integration/actions/workflows/validate.yml/badge.svg
