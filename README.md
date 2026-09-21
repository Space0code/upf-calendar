# UPF timetable → your calendar

Keeps a calendar feed of the EMAI year-1 timetable (semester until 20 Dec 2026) up to date, and sends a phone push
when a class changes at the last minute. Unofficial, hobby project.

## Use it

**1. Calendar feed** (read-only, re-checked around 09:00 and 20:00 Madrid time):

```
https://space0code.github.io/upf-calendar/upf.ics
```

- **Apple Calendar:** File → New Calendar Subscription, use `webcal://…` instead of `https://…`. Location: iCloud, refresh: hourly.
- **Google Calendar:** Add calendar → From URL (Google refreshes slowly, sometimes many hours).
- **Outlook:** Add calendar → Subscribe from web.

**2. Phone alerts (optional):** install the free [ntfy](https://ntfy.sh) app (iPhone or Android) and subscribe to the topic
`upf-timetable-ab06cc1d6b`.

You get a push when a class **today or tomorrow** changes (time, room, cancelled, added), or when a **new session outside the
regular weekly pattern** appears (extra lecture, exam). The pattern is the week of 28 Sep – 4 Oct. Everything else is only
noted in the event's Notes. You also get an urgent push if the timetable can't be fetched 5 times in a row.

## Your own copy (other programme, own alerts)

Fork, delete `state.json` and `docs/upf.ics`, then in Settings:
add secrets `UPF_URL` (your programme's public timetable link) and `NTFY_TOPIC` (your own topic, not the shared one),
set Pages → Source: GitHub Actions, and run the "Update UPF timetable" workflow once.

Optional repo variables: `TERM_START`, `TERM_END` (default 2026-09-14 / 2026-12-20), `BASELINE_MODE` (`fixed` or `previous_week`),
`BASELINE_WEEK` (Monday of the fixed baseline week), `LAST_MINUTE_DAYS` (default 1 = today and tomorrow).

## Contributing

PRs welcome: fork, branch, open a PR. Wanted: direct Google Calendar sync, other notification channels, other programmes.

```
python3 -m unittest discover -s tests    # no network, no dependencies
```

Don't edit `state.json` / `docs/upf.ics` (the workflow writes them). Using an AI coding agent? See [`AGENTS.md`](AGENTS.md).
MIT licensed.
