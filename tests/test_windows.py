import json
import sys
import tempfile
import unittest
from pathlib import Path

from agent.config import Config
from agent.server import App


@unittest.skipUnless(sys.platform == "win32", "Windows calendar")
class WindowsCalendarTest(unittest.TestCase):
    def test_agent_event_survives_restart_and_appears_in_calendar_panel(self):
        with tempfile.TemporaryDirectory() as folder:
            cfg = Config("", "", "", Path(folder))
            app = App(cfg)
            try:
                self.assertTrue(app.status()["integrations"]["calendar_local"])
                result = app.agent.tools.run("calendar_add_event", json.dumps({"title": "交接会议", "start": "2026-11-26T23:30",
                                                           "location": "会议室"}))
                self.assertTrue(json.loads(result)["ok"], result)
                with self.assertRaises(ValueError):
                    app.calendar.add_event("错误时间", "2026-11-26 15:00", "2026-11-26 14:00")
            finally:
                app.store.close()
            app = App(cfg)
            try:
                events = app.route("GET", "/api/calendar", {"date": "2026-11-27"}, {})
                self.assertEqual(len(events), 1)
                self.assertEqual(events[0]["end"], "2026-11-27 00:30")
                self.assertEqual(events[0]["location"], "会议室")
            finally:
                app.store.close()
