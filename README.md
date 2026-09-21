# UPF timetable → Apple Calendar

`upf_calendar.py` reads the UPF public timetable (the same JSON the web page uses), writes `docs/upf.ics`
for the whole semester, and pushes an alert (ntfy) on last-minute changes and unusual additions.
GitHub Actions runs it at ~09:00 and ~20:00 Madrid time and publishes the feed with GitHub Pages.
Subscribe to `https://<user>.github.io/<repo>/upf.ics` in Apple Calendar (File > New Calendar Subscription).

- **Last-minute change**: a session today/tomorrow changed, was cancelled or was added since the last run.
- **Unusual addition**: a newly appeared session (anywhere in the semester) whose
  (course, type, weekday, start, end) is not in the baseline week. Everything is also logged in the event Notes.
- **Failures**: 5 attempts, 15 minutes apart; then an urgent push + GitHub's failure email. The feed is never
  overwritten with bad data (empty or >50% smaller fetch counts as a failure).

## Settings (repo variables, all optional)
`BASELINE_MODE` = `fixed` (default, week `BASELINE_WEEK`=2026-09-28) or `previous_week`;
`LAST_MINUTE_DAYS` (default 1 = today+tomorrow). Change with `gh variable set NAME --body VALUE`.
Secrets: `UPF_URL` (the timetable link), `NTFY_TOPIC`. Semester dates: `TERM_START`/`TERM_END` in `upf_calendar.py`.

## Local use
`UPF_URL=... FORCE=1 OUT_DIR=/tmp/out python3 upf_calendar.py` · tests: `python3 -m unittest discover -s tests`
(Times are floating Madrid wall-clock times, which is right as long as the calendar is used in that timezone.)
