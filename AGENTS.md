# AGENTS.md

Instructions for AI coding agents (Claude Code, Codex, OpenClaw, etc.) working in this repo.
Humans: see README.md.

## What this is
`upf_calendar.py` mirrors the UPF public timetable into `docs/upf.ics` and sends ntfy push alerts on notable changes.
GitHub Actions (`.github/workflows/update.yml`) runs it twice a day, commits `state.json` + `docs/upf.ics`,
and publishes `docs/` with GitHub Pages. Everything lives in one stdlib-only file, top to bottom:
config → fetching (`fetch_raw`, `normalize`, `fetch_with_retry`) → diff (`diff`) → baseline and alert rules
(`baseline`, `classify`) → state records (`build_records`) → ICS output (`render_ics`) → alerts and IO (`send_push`, `run`).

## Commands
```
python3 -m unittest discover -s tests -v                  # must pass; no network
UPF_URL='<timetable link>' FORCE=1 OUT_DIR=/tmp/upf python3 upf_calendar.py   # live run into a scratch dir
```
- Always set `OUT_DIR` for local runs so the real `state.json` / `docs/upf.ics` are not touched.
- Set `NOW_OVERRIDE=2026-09-21T20:07` to simulate the clock, `FORCE=1` to skip the 09:xx/20:xx run gate,
  `MAX_ATTEMPTS=1` to avoid 15-minute retry sleeps.
- On macOS the Python from PlatformIO or similar may lack CA certificates; use `/usr/bin/python3`.
- Leave `NTFY_TOPIC` unset when testing: alerts are then printed instead of pushed. The main deployment's topic is public and shared with the whole class, so never publish test messages to it; use your own topic.

## Hard rules
- **Never edit or commit `state.json` or `docs/upf.ics`.** The workflow owns them; PR edits cause merge conflicts and CI rejects them.
- **Stdlib only, Python 3.9+.** No new dependencies, no `pip install` step in the workflow. Avoid 3.10+ APIs
  (e.g. `Path.write_text(newline=...)`, `match`, runtime `X | Y` types).
- **Never put secrets in the repo, logs or output.** `UPF_URL` and `NTFY_TOPIC` come from environment variables / GitHub secrets only.
- **A bad fetch must never overwrite the last good feed.** Fetch failures raise `FetchError`, `run` exits non-zero and writes nothing.
- **Keep the output deterministic.** The `.ics` and `state.json` must be byte-identical when the timetable is unchanged
  (no "now" timestamps in the feed, sorted output). Otherwise the bot commits noise every run.
- **Alerts are at-least-once.** If a push fails, the lines go to `pending` in `state.json` and are retried next run.
- **Tests must not use the network.** Use synthetic events (see `ev()` in `tests/test_upf_calendar.py`) and mock `fetch_raw` / `send_push`.
- Any change to `diff`, `classify`, `baseline` or `render_ics` needs a test in `tests/`.

## Things that are easy to get wrong
- The data comes from a JSON endpoint the timetable page calls (`[Ajax]selecionarRangoHorarios`). It needs the session cookie from
  loading the page first. Responses are ISO-8859-15 and end with a bookkeeping object (`mostrarMensaje`) that is not an event.
- Holidays / "No classes" entries have no `reseId`, so they are keyed by date, and are emitted as all-day events.
  Real sessions are keyed by `reseId`, which is unique per session.
- Times are naive Europe/Madrid wall-clock times (floating in the `.ics`, no `TZID`). This is intentional.
- The workflow's cron fires at two UTC hours per slot to cover summer/winter time, and the script gates on the Madrid hour.
  Do not "simplify" this to one cron entry.
- "Last-minute" means the session is not over and starts today or within `LAST_MINUTE_DAYS` days. "Unusual" means a *newly appeared*
  session whose (course, type, weekday, start, end) is not in the baseline week; sessions already present when the feed was first seeded never alert.
- The first run with no `state.json` seeds silently (and sends one "feed initialised" push).

## Adding features
- **New output target (e.g. Google Calendar API):** add it as an extra step that consumes the records from `build_records`; keep
  `render_ics` and the Pages feed working. Gate it behind its own env var/secret so forks without it are unaffected.
- **New notification channel:** put it next to `send_push`, selected by env var, keep the ntfy path as the default.
- **New setting:** add it to `Config` with a default, pass it through the workflow env (repo variable), and document it in the README table.
- Keep it one file unless it clearly outgrows that; if you split, keep `upf_calendar.py` as the entry point the workflow calls.
- Match the surrounding style: short functions, comments only for non-obvious reasons.

## Pull requests
Work on a branch in a fork, keep PRs small and focused, make sure the tests pass (CI runs them on Python 3.9 and 3.12),
and update the README if user-facing behaviour or settings change. Never push to `main`.
