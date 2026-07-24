# Steps Into HA — Home Assistant integration

[![HACS Custom][hacs-badge]][hacs]
[![Validate][validate-badge]][validate]

Puts your iPhone's **daily step count** on your Home Assistant dashboard, so you can build a
family step leaderboard. Pairs with the [**Steps Into HA**][apprepo] iOS app, which reads
your step count from Apple Health and pushes it to your own Home Assistant — no third-party
servers, no account, no subscription.

> **The iOS app is awaiting App Store review.** This integration works today with any build
> of the app — see the note under step 3 if yours asks for the address and webhook ID as
> separate fields.

<!-- TODO: screenshot of the QR pairing step and a family bar chart -->

---

## Setup

**1. Install this integration** from HACS, then restart Home Assistant.

**2. Add a person:** Settings → Devices & Services → **Add Integration** → *Steps Into HA*.
Type a name. Home Assistant creates a private webhook for them.

**3. Choose the address** their phone should send to — see below. Home Assistant then shows
you a QR code.

**4. Install [Steps Into HA][apprepo]** on that person's iPhone, allow Apple Health access,
and scan the QR code.

That's it. You get `sensor.<name>_steps`, updated about once an hour in the background.

Repeat from step 2 for each family member — one entry per phone.

> **Using an older version of the app** that asks for a "Home Assistant address" and a
> "Webhook ID" in separate fields? Turn on **Advanced Mode** in your Home Assistant user
> profile before step 2. The setup form then lets you choose your own webhook ID, so you can
> pick something short enough to type. Make it unguessable — it's the only thing protecting
> the sensor.

### Which address? (step 3)

The phone sends its step count from wherever it happens to be — the shops, work, school. So
the address in the QR code has to reach Home Assistant **from outside your home**, or that
person will only sync when they're on your Wi-Fi.

| Option | Use it when |
|---|---|
| **Home Assistant Cloud** | You have a Nabu Casa subscription. A cloudhook is created for you: works from anywhere, no port forwarding, nothing exposed to the internet. Preselected when available. |
| **External** | You've filled in Settings → System → Network → **External URL**. Preselected when there's no cloudhook. |
| **Internal** | You only want syncing at home, or you're testing. |
| **Custom** | You reach Home Assistant through a **reverse proxy, your own domain, DuckDNS or Tailscale** — addresses Home Assistant can't discover for itself. |

Custom is the one to reach for if remote syncing isn't working. Home Assistant only knows
about addresses you've told it about, so a proxy you set up outside of it won't appear in
the list on its own. Paste just the base address — `https://ha.example.com` — and the
webhook path is added for you.

You can change this later from the integration's **Configure** button, which shows a fresh
QR code to re-scan.

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
