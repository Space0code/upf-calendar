# UPF timetable → your calendar

Turns the UPF public timetable into a calendar feed that stays up to date by itself,
and pings your phone when a class changes at the last minute.

Currently set up for the EMAI Master (year 1), semester ending 20 Dec 2026.
Unofficial, not affiliated with UPF, maintained as a hobby project.

## Just want the calendar?

Subscribe to the feed (read-only, refreshed twice a day, around 09:00 and 20:00 Madrid time):

```
https://space0code.github.io/upf-calendar/upf.ics
```

- **Apple Calendar (Mac):** File → New Calendar Subscription → paste the URL with `webcal://` instead of
  `https://`. Choose Location: iCloud (so it syncs to your iPhone) and Auto-refresh: Every hour.
- **Google Calendar:** Settings → Add calendar → From URL. Google refreshes these on its own schedule,
  sometimes many hours late.
- **Outlook:** Add calendar → Subscribe from web.

Each event has the room as location, and its Notes list any changes since it was first published.
The feed uses plain Madrid wall-clock times, so it's only correct while your device is on Madrid time.

## Want alerts on your phone?

Alerts go through [ntfy](https://ntfy.sh) and only work on your own copy, because they need your private topic.
Fork the repo and set it up (5 minutes):

1. **Fork** this repo, then delete `state.json` and `docs/upf.ics` in your fork so it starts from a clean slate.
2. **Get your timetable link:** the long `gestioacademica.upf.edu/...` URL of your programme's public timetable.
3. **Pick a private topic name** like `upf-yourname-x7k2q9` (anyone who knows the name can read it, so make it random),
   install the official **ntfy** app and subscribe to it.
4. **Add two secrets** (Settings → Secrets and variables → Actions):
   `UPF_URL` (your timetable link) and `NTFY_TOPIC` (your topic).
5. **Enable Pages:** Settings → Pages → Source: GitHub Actions.
6. **Run it once:** Actions → Update UPF timetable → Run workflow. You should get a "feed initialised" push,
   and your feed lives at `https://<your-username>.github.io/<repo-name>/upf.ics`.

Change the semester with the `TERM_START` / `TERM_END` variables (see below).

## What triggers an alert

- **Last-minute change:** a class today or tomorrow was moved, re-roomed, cancelled or added since the last run.
- **Unusual addition:** a newly published session, anywhere in the semester, that doesn't fit the regular weekly
  pattern (same course, type, weekday and times), for example an extra lecture or an exam.
  The pattern comes from one baseline week: a fixed one (default 28 Sep – 4 Oct 2026) or the previous week.

Everything else, like a change three weeks away, is silent and only logged in the event's Notes.

If the timetable can't be fetched, it retries 5 times, 15 minutes apart. After that you get an urgent push
and GitHub emails you about the failed run. A failed or empty fetch never overwrites the last good feed.

## Settings

Repository **variables** (all optional):

| Variable | Default | Meaning |
|---|---|---|
| `TERM_START` / `TERM_END` | `2026-09-14` / `2026-12-20` | Semester window included in the feed (inclusive) |
| `BASELINE_MODE` | `fixed` | `fixed` or `previous_week` |
| `BASELINE_WEEK` | `2026-09-28` | Monday of the baseline week when the mode is `fixed` |
| `LAST_MINUTE_DAYS` | `1` | `1` = today and tomorrow |

Repository **secrets:** `UPF_URL` (required), `NTFY_TOPIC` (optional; without it, alerts are only printed in the run log).

## Contributing

PRs are welcome: fork, branch, open a PR. Ideas that would be great:

- **Direct Google Calendar sync** (Google's own `.ics` subscription refreshes slowly)
- Other notification channels (Telegram, email, Discord)
- Support for other UPF programmes or a multi-programme setup
- Smarter "unusual" detection

```
python3 -m unittest discover -s tests          # tests, no network needed
UPF_URL='https://gestioacademica.upf.edu/...' FORCE=1 OUT_DIR=/tmp/upf python3 upf_calendar.py   # live dry run
```

It's a single stdlib-only Python file (no dependencies to install). On a Mac, use `/usr/bin/python3` if your Python
fails with SSL certificate errors. Using an AI coding agent? Point it at [`AGENTS.md`](AGENTS.md).

`state.json` and `docs/upf.ics` are written by the workflow. Don't edit or commit them in PRs.

## How it works

The timetable page is a JavaScript calendar. This script loads the page once for a session cookie, then calls the same
JSON endpoint the page uses. It compares the result with the previous run (`state.json`), decides what deserves a push,
and writes `docs/upf.ics`. GitHub Actions runs it on a schedule, commits the results and publishes `docs/` with GitHub Pages.

MIT licensed.
