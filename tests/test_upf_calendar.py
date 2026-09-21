import datetime as dt
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import upf_calendar as u  # noqa: E402

NOW = dt.datetime(2026, 9, 21, 20, 7, tzinfo=u.MADRID)  # Monday evening


def ev(key="r1", start="2026-09-22T10:30", end="2026-09-22T13:00", room="52.121", course="RL", type_="Theory", **kw):
    base = {"key": key, "kind": "class", "course": course, "code": 1, "type": type_, "group": "1", "room": room,
            "start": start, "end": end, "lecturers": [], "obs": "", "comment": ""}
    return {**base, **kw}


def feed(*events):
    return {e["key"]: e for e in events}


SLOTS = {u.slot(ev())}  # (course 1, Theory, Tuesday, 10:30, 13:00)


class Normalize(unittest.TestCase):
    RAW = {"title": "Reinforcement learning (Universitat Pompeu Fabra)", "aula": "52.S27", "tipologia": "Theory",
           "grup": "1", "codAsignatura": 32810, "reseId": 5, "start": "2026-09-29 10:30:00",
           "end": "2026-09-29 13:00:00", "profesores": [], "publicarObservacion": "N", "observacion": "secret"}

    def test_class(self):
        e = u.normalize(self.RAW)
        self.assertEqual((e["key"], e["course"], e["room"], e["kind"]), ("r5", "Reinforcement learning", "52.S27", "class"))
        self.assertEqual(e["obs"], "")  # unpublished observations stay hidden
        self.assertEqual(u.normalize({**self.RAW, "publicarObservacion": "S"})["obs"], "secret")

    def test_holiday_has_no_reseid(self):
        h = u.normalize({"title": "Holiday - ", "start": "2026-10-12 10:30:00", "end": "2026-10-12 20:00:00",
                         "className": "festiu", "festivoNoLectivo": True})
        self.assertEqual((h["kind"], h["course"], h["key"]), ("holiday", "Holiday", "h-2026-10-12-festiu"))

    def test_garbage(self):
        self.assertIsNone(u.normalize({"title": "x"}))


class Diff(unittest.TestCase):
    def kinds(self, prev, new):
        return sorted((c["type"], c["text"]) for c in u.diff(prev, new))

    def test_no_change(self):
        self.assertEqual(u.diff(feed(ev()), feed(ev())), [])

    def test_room_and_time(self):
        self.assertEqual(self.kinds(feed(ev()), feed(ev(room="52.119"))), [("changed", "room 52.121 → 52.119")])
        out = self.kinds(feed(ev()), feed(ev(start="2026-09-22T14:30", end="2026-09-22T17:00")))
        self.assertEqual(out, [("changed", "time 10:30–13:00 → 14:30–17:00")])

    def test_added_removed(self):
        self.assertEqual([c["type"] for c in u.diff(feed(ev()), feed(ev(), ev(key="r2", start="2026-09-24T10:30")))], ["added"])
        self.assertEqual([c["type"] for c in u.diff(feed(ev(), ev(key="r2")), feed(ev()))], ["removed"])

    def test_reissued_id_same_day_is_a_change_not_cancel_plus_add(self):
        out = u.diff(feed(ev()), feed(ev(key="r9", start="2026-09-22T14:30", end="2026-09-22T17:00")))
        self.assertEqual([c["type"] for c in out], ["changed"])
        self.assertEqual(out[0]["old"]["key"], "r1")

    def test_reissued_id_moved_within_week(self):
        out = u.diff(feed(ev()), feed(ev(key="r9", start="2026-09-24T10:30", end="2026-09-24T13:00")))
        self.assertEqual([(c["type"], c["text"][:5]) for c in out], [("changed", "moved")])

    def test_ambiguous_pairing_is_not_guessed(self):
        prev = feed(ev(), ev(key="r2", start="2026-09-22T14:30", end="2026-09-22T17:00"))
        new = feed(ev(key="r8", start="2026-09-22T09:00", end="2026-09-22T10:00"),
                   ev(key="r9", start="2026-09-22T18:00", end="2026-09-22T19:00"))
        self.assertEqual(sorted(c["type"] for c in u.diff(prev, new)), ["added", "added", "removed", "removed"])


class Alerts(unittest.TestCase):
    def alerts(self, prev, new, now=NOW, slots=SLOTS):
        return [c for c in u.classify(u.diff(prev, new), slots, now, 1) if c["alert"]]

    def test_change_tomorrow_alerts_but_two_days_out_does_not(self):
        self.assertEqual(len(self.alerts(feed(ev()), feed(ev(room="X")))), 1)
        far = dict(start="2026-09-23T10:30", end="2026-09-23T13:00")
        self.assertEqual(self.alerts(feed(ev(**far)), feed(ev(room="X", **far))), [])

    def test_change_to_a_finished_session_is_ignored(self):
        done = dict(start="2026-09-21T10:30", end="2026-09-21T13:00")
        self.assertEqual(self.alerts(feed(ev(**done)), feed(ev(room="X", **done))), [])

    def test_cancellation_tomorrow_alerts(self):
        out = self.alerts(feed(ev()), {})
        self.assertEqual((out[0]["type"], out[0]["urgent"]), ("removed", True))

    def test_unusual_addition_alerts_anywhere_in_term(self):
        exam = ev(key="r2", start="2026-12-14T11:30", end="2026-12-14T14:00", type_="Exam")
        out = self.alerts(feed(ev()), feed(ev(), exam))
        self.assertEqual((len(out), out[0]["urgent"]), (1, False))
        self.assertIn("outside the regular weekly pattern", out[0]["line"])

    def test_regular_addition_far_out_is_silent_but_noted(self):
        regular = ev(key="r2", start="2026-11-03T10:30", end="2026-11-03T13:00")  # a Tuesday slot
        changes = u.classify(u.diff(feed(ev()), feed(ev(), regular)), SLOTS, NOW, 1)
        self.assertFalse(changes[0]["alert"])
        self.assertEqual(changes[0]["text"], "Added")

    def test_regular_addition_tomorrow_alerts(self):
        self.assertEqual(len(self.alerts(feed(), feed(ev()))), 1)

    def test_past_unusual_addition_is_ignored(self):
        old_exam = ev(key="r2", start="2026-09-10T11:30", end="2026-09-10T14:00", type_="Exam")
        self.assertEqual(self.alerts(feed(ev()), feed(ev(), old_exam)), [])


class Baseline(unittest.TestCase):
    def cfg(self, mode):
        with mock.patch.dict(os.environ, {"BASELINE_MODE": mode, "BASELINE_WEEK": "2026-09-28"}):
            return u.Config()

    def test_fixed_week(self):
        events = feed(ev(start="2026-09-29T10:30"), ev(key="r2", start="2026-10-06T14:30", end="2026-10-06T17:00"))
        slots, _ = u.baseline(events, self.cfg("fixed"), dt.date(2026, 10, 20))
        self.assertEqual({s[3] for s in slots}, {"10:30"})

    def test_previous_week_and_fallback(self):
        events = feed(ev(start="2026-09-29T10:30"), ev(key="r2", start="2026-10-06T14:30", end="2026-10-06T17:00"))
        slots, _ = u.baseline(events, self.cfg("previous_week"), dt.date(2026, 10, 12))  # prev week = 5-11 Oct
        self.assertEqual({s[3] for s in slots}, {"14:30"})
        slots, _ = u.baseline(events, self.cfg("previous_week"), dt.date(2026, 10, 26))  # empty week -> fixed
        self.assertEqual({s[3] for s in slots}, {"10:30"})


class Ics(unittest.TestCase):
    def test_render(self):
        rec = {**ev(course="Symbolic, reasoning; 1", comment="Bring ID\nline 2"), "first_seen": "x",
               "updated": "2026-09-21T20:07", "seq": 2, "log": [{"at": "2026-09-21T20:07", "text": "room A → B"}]}
        hol = {**ev(key="h1", course="Holiday", start="2026-09-24T10:30", end="2026-09-24T20:00", kind="holiday", room=""),
               "first_seen": "x", "updated": "2026-09-21T20:07", "seq": 0, "log": []}
        text = u.render_ics({"r1": rec, "h1": hol}, SLOTS)
        self.assertTrue(text.endswith("END:VCALENDAR\r\n"))
        self.assertNotIn("\n", text.replace("\r\n", ""))
        self.assertLessEqual(max(len(line.encode()) for line in text.split("\r\n")), 75)
        unfolded = text.replace("\r\n ", "")
        self.assertIn("SUMMARY:Symbolic\\, reasoning\; 1", unfolded)
        self.assertIn("• 21 Sep 20:07 — room A → B", unfolded.replace("\\n", "\n"))
        self.assertIn("DTSTART;VALUE=DATE:20260924", unfolded)
        self.assertIn("DTSTART:20260922T103000", unfolded)
        self.assertIn("SEQUENCE:2", unfolded)


class Run(unittest.TestCase):
    def test_gate_skips_off_hours(self):
        with mock.patch.dict(os.environ, {"NOW_OVERRIDE": "2026-09-21T14:00", "FORCE": "", "UPF_URL": "x"}):
            with mock.patch.object(u, "fetch_raw", side_effect=AssertionError("must not fetch")):
                self.assertEqual(u.run(u.Config()), 0)

    def test_failed_fetch_alerts_and_leaves_files_alone(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {"FORCE": "1", "UPF_URL": "x", "OUT_DIR": tmp, "MAX_ATTEMPTS": "3", "RETRY_MINUTES": "0"}
        ):
            with mock.patch.object(u, "fetch_raw", side_effect=u.FetchError("boom")) as fetch, \
                    mock.patch.object(u, "send_push") as push:
                self.assertEqual(u.run(u.Config()), 1)
            self.assertEqual(fetch.call_count, 3)
            self.assertEqual(push.call_args[0][1], "UPF timetable: fetch failing")
            self.assertFalse(Path(tmp, "docs", "upf.ics").exists())

    def test_empty_and_dropped_feeds_are_rejected(self):
        with mock.patch.dict(os.environ, {"UPF_URL": "x"}):
            cfg = u.Config()
        with mock.patch.object(u, "fetch_raw", return_value=[]):
            with self.assertRaises(u.FetchError):
                u.load_events(cfg, 0)
        raws = [{"title": "A", "reseId": i, "start": "2026-10-01 10:00:00", "end": "2026-10-01 11:00:00"} for i in range(3)]
        with mock.patch.object(u, "fetch_raw", return_value=raws):
            with self.assertRaises(u.FetchError):
                u.load_events(cfg, 20)


if __name__ == "__main__":
    unittest.main()
