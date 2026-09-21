#!/usr/bin/env python3
"""Mirror the UPF public timetable into an .ics feed and push-alert on notable changes.

Stdlib only. Runs from GitHub Actions (.github/workflows/update.yml), but works locally:

    UPF_URL='https://gestioacademica.upf.edu/...' FORCE=1 python3 upf_calendar.py

Configuration is via environment variables, see Config below.
"""

from __future__ import annotations

import datetime as dt
import http.cookiejar
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

MADRID = ZoneInfo("Europe/Madrid")
ROOT = Path(__file__).resolve().parent
UNIVERSITY_SUFFIX = re.compile(r"\s*\(Universitat Pompeu Fabra\)\s*$")
TRACKED = ("course", "type", "group", "room", "start", "end", "lecturers", "obs", "comment")
META = ("first_seen", "updated", "seq", "log")
LOG_KEEP = 5
LOG_MAX_AGE_DAYS = 14
DROP_GUARD = 0.5  # refuse a fetch that has fewer than this fraction of the previous class events


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name) or default


class Config:
    def __init__(self) -> None:
        self.url = _env("UPF_URL")
        self.ntfy_topic = _env("NTFY_TOPIC")  # empty -> alerts are printed instead of pushed
        self.term_start = dt.date.fromisoformat(_env("TERM_START", "2026-09-14"))
        self.term_end = dt.date.fromisoformat(_env("TERM_END", "2026-12-20"))  # inclusive
        self.baseline_mode = _env("BASELINE_MODE", "fixed")  # fixed | previous_week
        self.baseline_week = dt.date.fromisoformat(_env("BASELINE_WEEK", "2026-09-28"))  # a Monday
        self.last_minute_days = int(_env("LAST_MINUTE_DAYS", "1"))  # 1 = today + tomorrow
        self.attempts = int(_env("MAX_ATTEMPTS", "5"))
        self.retry_minutes = float(_env("RETRY_MINUTES", "15"))
        self.gate_hours = {int(h) for h in _env("GATE_HOURS", "9,20").split(",")}
        self.force = _env("FORCE") not in ("", "0")
        self.out_dir = Path(_env("OUT_DIR", str(ROOT)))

    @property
    def ics_path(self) -> Path:
        return self.out_dir / "docs" / "upf.ics"

    @property
    def state_path(self) -> Path:
        return self.out_dir / "state.json"


def now_madrid() -> dt.datetime:
    override = _env("NOW_OVERRIDE")  # e.g. 2026-09-21T20:07, for testing
    if override:
        return dt.datetime.fromisoformat(override).replace(tzinfo=MADRID)
    return dt.datetime.now(MADRID)


# --------------------------------------------------------------------------- fetching


class FetchError(Exception):
    pass


def fetch_raw(url: str, start: dt.date, end: dt.date) -> list[dict]:
    """Load the page (for the session cookie), then call the JSON endpoint the page itself uses."""
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    opener.addheaders = [("User-Agent", "upf-calendar-sync/1.0 (personal timetable mirror)")]
    epoch = lambda d: int(dt.datetime.combine(d, dt.time(), MADRID).timestamp())  # noqa: E731
    try:
        opener.open(url, timeout=30).read()
        base = url.split("?")[0].rsplit("/", 1)[0]
        query = urllib.parse.urlencode(
            {"rnd": "1", "start": epoch(start), "end": epoch(end + dt.timedelta(days=1))}
        )
        body = opener.open(f"{base}/%5BAjax%5DselecionarRangoHorarios?{query}", timeout=60).read()
    except (urllib.error.URLError, OSError) as exc:
        raise FetchError(f"network error: {exc}") from exc
    try:
        data = json.loads(body.decode("iso-8859-15"))
    except ValueError as exc:
        raise FetchError("response is not JSON (link token expired or session problem?)") from exc
    if isinstance(data, dict):
        raise FetchError(f"server error: {data.get('error', data)}")
    if data and isinstance(data[-1], dict) and "start" not in data[-1]:
        data.pop()  # trailing {"mostrarMensaje": ...} bookkeeping object
    return data


def normalize(raw: dict) -> dict | None:
    """Raw API event -> flat tracked event. Returns None for anything unusable."""
    if "start" not in raw or "end" not in raw:
        return None
    start = raw["start"][:16].replace(" ", "T")
    end = raw["end"][:16].replace(" ", "T")
    holiday = bool(raw.get("festivoNoLectivo")) or "reseId" not in raw
    if holiday:
        title = re.sub(r"[\s-]+$", "", raw.get("title", "")) or "No classes"
        return {
            "key": f"h-{start[:10]}-{raw.get('className', '')}",
            "kind": "holiday", "course": title, "code": 0, "type": "", "group": "", "room": "",
            "start": start, "end": end, "lecturers": [], "obs": "", "comment": "",
        }
    lecturers = [
        p if isinstance(p, str) else str(p.get("nombre") or p.get("name") or p) for p in raw.get("profesores") or []
    ]
    return {
        "key": f"r{raw['reseId']}",
        "kind": "class",
        "course": UNIVERSITY_SUFFIX.sub("", raw.get("title", "")).strip(),
        "code": raw.get("codAsignatura", 0),
        "type": (raw.get("tipologia") or "").strip(),
        "group": str(raw.get("grup") or "").strip(),
        "room": (raw.get("aula") or "").strip(),
        "start": start, "end": end,
        "lecturers": lecturers,
        "obs": (raw.get("observacion") or "").strip() if raw.get("publicarObservacion") == "S" else "",
        "comment": (raw.get("comentario") or "").strip() if raw.get("publicarComentario") == "S" else "",
    }


def load_events(cfg: Config, prev_class_count: int) -> dict[str, dict]:
    events: dict[str, dict] = {}
    for raw in fetch_raw(cfg.url, cfg.term_start, cfg.term_end):
        ev = normalize(raw)
        if ev and cfg.term_start <= dt.date.fromisoformat(ev["start"][:10]) <= cfg.term_end:
            events[ev["key"]] = ev
    classes = sum(1 for e in events.values() if e["kind"] == "class")
    if classes == 0:
        raise FetchError("fetch returned no class events")
    if prev_class_count >= 10 and classes < prev_class_count * DROP_GUARD:
        raise FetchError(f"suspicious drop: {classes} class events, previously {prev_class_count}")
    return events


def fetch_with_retry(cfg: Config, prev_class_count: int) -> dict[str, dict]:
    last: Exception | None = None
    for attempt in range(1, cfg.attempts + 1):
        try:
            return load_events(cfg, prev_class_count)
        except FetchError as exc:
            last = exc
            print(f"attempt {attempt}/{cfg.attempts} failed: {exc}", flush=True)
            if attempt < cfg.attempts:
                time.sleep(cfg.retry_minutes * 60)
    raise FetchError(f"{cfg.attempts} attempts failed, last error: {last}")


# --------------------------------------------------------------------------- comparing

DAY_FMT = "%a %-d %b"


def when(ev: dict) -> str:
    d = dt.datetime.fromisoformat(ev["start"])
    return f"{d.strftime(DAY_FMT)} {ev['start'][11:16]}–{ev['end'][11:16]}"


def _list(v: list[str]) -> str:
    return ", ".join(v) or "—"


def describe_change(old: dict, new: dict) -> str:
    parts = []
    if (old["start"], old["end"]) != (new["start"], new["end"]):
        if old["start"][:10] == new["start"][:10]:
            parts.append(f"time {old['start'][11:16]}–{old['end'][11:16]} → {new['start'][11:16]}–{new['end'][11:16]}")
        else:
            parts.append(f"moved {when(old)} → {when(new)}")
    for field, label in (("room", "room"), ("type", "type"), ("group", "group"), ("course", "course")):
        if old[field] != new[field]:
            parts.append(f"{label} {old[field] or '—'} → {new[field] or '—'}")
    if old["lecturers"] != new["lecturers"]:
        parts.append(f"lecturers {_list(old['lecturers'])} → {_list(new['lecturers'])}")
    for field, label in (("obs", "observation"), ("comment", "comment")):
        if old[field] != new[field]:
            parts.append(f"{label}: {new[field] or 'removed'}")
    return "; ".join(parts)


def _pair_moved(removed: list[dict], added: list[dict]) -> list[tuple[dict, dict]]:
    """Pair removed/added classes that are almost certainly the same session that was edited
    (in case the server issues a new id on edit): same course/type/group, first same day, then same ISO week."""
    pairs: list[tuple[dict, dict]] = []
    signature = lambda e: (e["code"], e["type"], e["group"])  # noqa: E731
    for scope in (lambda e: e["start"][:10], lambda e: dt.date.fromisoformat(e["start"][:10]).isocalendar()[:2]):
        buckets: dict[tuple, tuple[list, list]] = {}
        for e in removed:
            buckets.setdefault((signature(e), scope(e)), ([], []))[0].append(e)
        for e in added:
            buckets.setdefault((signature(e), scope(e)), ([], []))[1].append(e)
        for rem, add in buckets.values():
            if len(rem) == 1 and len(add) == 1:
                pairs.append((rem[0], add[0]))
                removed.remove(rem[0])
                added.remove(add[0])
    return pairs


def diff(prev: dict[str, dict], new: dict[str, dict]) -> list[dict]:
    """Changes as dicts: {type: added|removed|changed, old, new, text}."""
    changes = []
    for key, ev in new.items():
        if key in prev:
            text = describe_change(prev[key], ev)
            if text:
                changes.append({"type": "changed", "old": prev[key], "new": ev, "text": text})
    removed = [e for k, e in prev.items() if k not in new and e["kind"] == "class"]
    added = [e for k, e in new.items() if k not in prev and e["kind"] == "class"]
    for old, cur in _pair_moved(removed, added):
        changes.append({"type": "changed", "old": old, "new": cur, "text": describe_change(old, cur) or "edited"})
    changes += [{"type": "removed", "old": e, "new": None, "text": "Cancelled"} for e in removed]
    changes += [{"type": "added", "old": None, "new": e, "text": "Added"} for e in added]
    for k, e in prev.items():  # holidays appearing/disappearing are tracked too, never paired
        if e["kind"] == "holiday" and k not in new:
            changes.append({"type": "removed", "old": e, "new": None, "text": "Removed"})
    for k, e in new.items():
        if e["kind"] == "holiday" and k not in prev:
            changes.append({"type": "added", "old": None, "new": e, "text": "Added"})
    return changes


# --------------------------------------------------------------------------- baseline + alert rules


def _monday(d: dt.date) -> dt.date:
    return d - dt.timedelta(days=d.weekday())


def slot(ev: dict) -> tuple:
    d = dt.date.fromisoformat(ev["start"][:10])
    return (ev["code"], ev["type"], d.weekday(), ev["start"][11:16], ev["end"][11:16])


def week_slots(events: dict[str, dict], monday: dt.date) -> set[tuple]:
    days = {(monday + dt.timedelta(days=i)).isoformat() for i in range(7)}
    return {slot(e) for e in events.values() if e["kind"] == "class" and e["start"][:10] in days}


def baseline(events: dict[str, dict], cfg: Config, today: dt.date) -> tuple[set[tuple], str]:
    if cfg.baseline_mode == "previous_week":
        monday = _monday(today) - dt.timedelta(days=7)
        slots = week_slots(events, monday)
        if slots:
            return slots, f"previous week (from {monday})"
        print(f"previous week {monday} has no classes; falling back to fixed baseline week")
    return week_slots(events, cfg.baseline_week), f"fixed week (from {cfg.baseline_week})"


def in_window(ev: dict | None, now: dt.datetime, days: int) -> bool:
    """Session still relevant (not yet over) and starting today or within `days` days."""
    if ev is None:
        return False
    naive_now = now.replace(tzinfo=None)
    return (
        dt.datetime.fromisoformat(ev["end"]) >= naive_now
        and dt.date.fromisoformat(ev["start"][:10]) <= now.date() + dt.timedelta(days=days)
    )


def day_label(ev: dict, now: dt.datetime) -> str:
    d = dt.date.fromisoformat(ev["start"][:10])
    delta = (d - now.date()).days
    return {0: "today", 1: "tomorrow"}.get(delta, when(ev))


def classify(changes: list[dict], slots: set[tuple], now: dt.datetime, days: int) -> list[dict]:
    """Set c['text'] (for the event notes), c['alert'] (push or not) and c['urgent'], c['line']."""
    for c in changes:
        ev = c["new"] or c["old"]
        off = c["type"] == "added" and ev["kind"] == "class" and slot(ev) not in slots
        window = in_window(c["old"], now, days) or in_window(c["new"], now, days)
        if c["type"] == "added":
            c["text"] = "Added — outside the regular weekly pattern" if off else "Added"
            unusual = off and dt.datetime.fromisoformat(ev["end"]) >= now.replace(tzinfo=None)
            c["alert"], c["urgent"] = window or unusual, window
            room = f", room {ev['room']}" if ev["room"] else ""
            kind = f"{ev['type']} · " if ev["type"] and ev["type"] != "Theory" else ""
            tail = " (outside the regular weekly pattern)" if off else ""
            c["line"] = f"New session: {kind}{ev['course']} — {when(ev)}{room}{tail}"
        elif c["type"] == "removed":
            c["alert"], c["urgent"] = window, window
            c["line"] = f"Cancelled: {ev['course']} — {when(ev)}"
        else:
            c["alert"], c["urgent"] = window, window
            c["line"] = f"Changed ({day_label(c['new'], now)}): {ev['course']} — {c['text']}"
    return changes


# --------------------------------------------------------------------------- state records


def build_records(
    prev: dict[str, dict] | None, new: dict[str, dict], changes: list[dict], now: dt.datetime
) -> dict[str, dict]:
    """Attach bookkeeping (first_seen/updated/seq/change log) to each current event."""
    stamp = now.strftime("%Y-%m-%dT%H:%M")
    fresh = lambda ev, log: {**ev, "first_seen": stamp, "updated": stamp, "seq": 0, "log": log}  # noqa: E731
    if prev is None:
        return {k: fresh(e, []) for k, e in new.items()}
    by_new = {c["new"]["key"]: c for c in changes if c["new"]}
    cutoff = (now - dt.timedelta(days=LOG_MAX_AGE_DAYS)).strftime("%Y-%m-%dT%H:%M")
    records = {}
    for key, ev in new.items():
        change = by_new.get(key)
        if change is None and key in prev:
            records[key] = {**ev, **{m: prev[key][m] for m in META}}
        elif change is not None and change["old"] is not None:
            old = prev[change["old"]["key"]]
            log = [*old["log"], {"at": stamp, "text": change["text"]}]
            records[key] = {**ev, "first_seen": old["first_seen"], "updated": stamp, "seq": old["seq"] + 1,
                            "log": [e for e in log if e["at"] >= cutoff][-LOG_KEEP:]}
        else:
            text = change["text"] if change else "Added"
            records[key] = fresh(ev, [{"at": stamp, "text": text}])
    return records


# --------------------------------------------------------------------------- ICS


def _esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\;").replace(",", "\\,").replace("\r\n", "\n").replace("\n", "\\n")


def _fold(line: str) -> str:
    out, cur, size = [], "", 0
    for ch in line:
        n = len(ch.encode())
        if size + n > 75:
            out.append(cur)
            cur, size = " " + ch, 1 + n
        else:
            cur, size = cur + ch, size + n
    out.append(cur)
    return "\r\n".join(out)


def _stamp_utc(local: str) -> str:
    d = dt.datetime.fromisoformat(local).replace(tzinfo=MADRID).astimezone(dt.timezone.utc)
    return d.strftime("%Y%m%dT%H%M%SZ")


def _compact(local: str) -> str:
    return local.replace("-", "").replace(":", "") + "00"


def _fmt_log_time(at: str) -> str:
    return dt.datetime.fromisoformat(at).strftime("%-d %b %H:%M")


def notes(rec: dict, slots: set[tuple]) -> str:
    lines = []
    head = " · ".join(x for x in (rec["type"], f"group {rec['group']}" if rec["group"] else "") if x)
    if head:
        lines.append(head)
    if rec["room"]:
        lines.append(f"Room: {rec['room']}")
    if rec["lecturers"]:
        lines.append(f"Lecturers: {', '.join(rec['lecturers'])}")
    if rec["obs"]:
        lines.append(f"Observation: {rec['obs']}")
    if rec["comment"]:
        lines.append(f"Comment: {rec['comment']}")
    if rec["kind"] == "class" and slots and slot(rec) not in slots:
        lines.append("Outside the regular weekly pattern.")
    if rec["log"]:
        lines.append("")
        lines.append("Changes:")
        lines += [f"• {_fmt_log_time(e['at'])} — {e['text']}" for e in rec["log"]]
    return "\n".join(lines)


def render_ics(records: dict[str, dict], slots: set[tuple]) -> str:
    out = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//upf-calendar//EN", "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH", "X-WR-CALNAME:UPF Timetable", "X-WR-TIMEZONE:Europe/Madrid",
        "REFRESH-INTERVAL;VALUE=DURATION:PT1H", "X-PUBLISHED-TTL:PT1H",
    ]
    for rec in sorted(records.values(), key=lambda r: (r["start"], r["key"])):
        title = rec["course"]
        if rec["kind"] == "class" and rec["type"] and rec["type"] != "Theory":
            title += f" [{rec['type']}]"
        out += ["BEGIN:VEVENT", f"UID:{rec['key']}@upf-timetable", f"DTSTAMP:{_stamp_utc(rec['updated'])}",
                f"LAST-MODIFIED:{_stamp_utc(rec['updated'])}", f"SEQUENCE:{rec['seq']}"]
        if rec["kind"] == "holiday":
            day = dt.date.fromisoformat(rec["start"][:10])
            out += [f"DTSTART;VALUE=DATE:{day:%Y%m%d}", f"DTEND;VALUE=DATE:{day + dt.timedelta(days=1):%Y%m%d}",
                    "TRANSP:TRANSPARENT"]
        else:
            out += [f"DTSTART:{_compact(rec['start'])}", f"DTEND:{_compact(rec['end'])}"]
        out.append(f"SUMMARY:{_esc(title)}")
        if rec["room"]:
            out.append(f"LOCATION:{_esc(rec['room'])}")
        body = notes(rec, slots)
        if body:
            out.append(f"DESCRIPTION:{_esc(body)}")
        out.append("END:VEVENT")
    out.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in out) + "\r\n"


# --------------------------------------------------------------------------- alerts + IO


def send_push(cfg: Config, title: str, lines: list[str], priority: int = 3, tags: tuple[str, ...] = ()) -> None:
    message = "\n".join(lines)[:3500]
    if not cfg.ntfy_topic:
        print(f"[ALERT p{priority}] {title}\n{message}", flush=True)
        return
    payload = {"topic": cfg.ntfy_topic, "title": title, "message": message, "priority": priority, "tags": list(tags)}
    req = urllib.request.Request(
        "https://ntfy.sh/", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
    )
    urllib.request.urlopen(req, timeout=20).read()


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as fh:  # newline="" keeps the CRLF line endings
        fh.write(text)
    tmp.replace(path)


def load_state(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def run(cfg: Config) -> int:
    now = now_madrid()
    if not cfg.force and now.hour not in cfg.gate_hours:
        print(f"{now:%H:%M} Madrid is outside the run hours {sorted(cfg.gate_hours)}; skipping")
        return 0
    if not cfg.url:
        print("UPF_URL is not set", file=sys.stderr)
        return 2

    state = load_state(cfg.state_path)
    prev = state["events"] if state else None
    prev_classes = sum(1 for e in prev.values() if e["kind"] == "class") if prev else 0
    try:
        events = fetch_with_retry(cfg, prev_classes)
    except FetchError as exc:
        send_push(cfg, "UPF timetable: fetch failing", [str(exc), "Feed left unchanged."], 5, ("rotating_light",))
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1

    slots, slots_desc = baseline(events, cfg, now.date())
    print(f"baseline: {slots_desc}, {len(slots)} slots")
    changes = classify(diff(prev, events), slots, now, cfg.last_minute_days) if prev is not None else []
    records = build_records(prev, events, changes, now)

    alerts = [c for c in changes if c["alert"]]
    lines = list(state.get("pending", [])) if state else []
    lines += [c["line"] for c in sorted(alerts, key=lambda c: (c["new"] or c["old"])["start"])]
    pending: list[str] = []
    try:
        if prev is None:
            send_push(cfg, "UPF timetable feed initialised", [f"{len(events)} events loaded. Alerts are working."], 2, ("calendar",))
        elif lines:
            urgent = any(c["urgent"] for c in alerts) or bool(state.get("pending"))
            send_push(cfg, f"UPF timetable: {len(lines)} alert{'s' * (len(lines) != 1)}", lines, 4 if urgent else 3, ("warning",))
    except (urllib.error.URLError, OSError) as exc:
        print(f"push failed ({exc}); will retry next run", file=sys.stderr)
        pending = lines

    heartbeat = "{}-W{:02d}".format(*now.isocalendar()[:2])
    new_state = {"version": 1, "heartbeat_week": heartbeat, "pending": pending, "events": records}
    write_atomic(cfg.ics_path, render_ics(records, slots))
    write_atomic(cfg.state_path, json.dumps(new_state, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
    print(f"ok: {len(records)} events, {len(changes)} changes, {len(alerts)} alerts")
    return 0


if __name__ == "__main__":
    sys.exit(run(Config()))
