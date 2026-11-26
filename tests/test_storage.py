import sqlite3
import threading
import unittest
from datetime import date, datetime, timedelta

from agent.reminders import daily_backup
from agent.storage import Store
from tests.helpers import temp_config


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.cfg = temp_config()
        self.s = Store(self.cfg.db_path)

    def tearDown(self):
        self.s.close()

    def test_task_lifecycle(self):
        t = self.s.add_task("发周报", priority="high", due_date="2026-01-02")
        self.assertEqual(t["status"], "todo")
        done = self.s.update_task(t["id"], status="done")
        self.assertIsNotNone(done["completed_at"])
        self.assertEqual(self.s.list_tasks("open"), [])
        reopened = self.s.update_task(t["id"], status="todo")
        self.assertIsNone(reopened["completed_at"])

    def test_validation(self):
        with self.assertRaises(ValueError):
            self.s.add_task("")
        with self.assertRaises(ValueError):
            self.s.add_task("x", due_date="明天")
        with self.assertRaises(ValueError):
            self.s.add_task("x", priority="urgent")

    def test_daily_summary(self):
        t = self.s.add_task("a")
        self.s.update_task(t["id"], status="done")
        self.s.add_worklog("评审会", minutes=60)
        self.s.add_worklog("写文档", minutes=30)
        summ = self.s.daily_summary()
        self.assertEqual(len(summ["completed_tasks"]), 1)
        self.assertEqual(summ["total_logged_minutes"], 90)

    def test_reminders(self):
        soon = (datetime.now() + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M")
        r = self.s.add_reminder("喝水", soon)
        self.assertEqual(self.s.due_reminders(), [])
        with self.assertRaises(ValueError):
            self.s.add_reminder("x", "2020-01-01 10:00")
        # 模拟到期
        self.s.conn.execute("UPDATE reminders SET remind_at = '2020-01-01 00:00'")
        self.assertEqual([x["id"] for x in self.s.due_reminders()], [r["id"]])
        self.s.mark_reminder_sent(r["id"])
        self.assertEqual(len(self.s.pop_unseen_reminders()), 1)
        self.assertEqual(self.s.pop_unseen_reminders(), [])
        with self.assertRaises(ValueError):
            self.s.cancel_reminder(r["id"])  # 已提醒过的不能取消

    def test_drafts_cannot_be_sent_twice(self):
        d = self.s.add_draft("a@b.com", "主题", "正文")
        self.s.set_draft_status(d["id"], "sent")
        with self.assertRaises(ValueError):
            self.s.set_draft_status(d["id"], "sent")
        with self.assertRaises(ValueError):
            self.s.add_draft("a@b.com", "主题\nBcc: x@y.com", "正文")

    def test_chat_history_roundtrip(self):
        self.s.add_chat_message({"role": "user", "content": "你好"})
        self.s.add_chat_message({"role": "assistant", "content": "",
                                 "tool_calls": [{"id": "1", "type": "function",
                                                 "function": {"name": "list_tasks", "arguments": "{}"}}]})
        self.s.add_chat_message({"role": "tool", "tool_call_id": "1", "content": "[]"})
        msgs = self.s.load_chat_messages(10)
        self.assertEqual([m["role"] for m in msgs], ["user", "assistant", "tool"])
        self.assertEqual(msgs[1]["tool_calls"][0]["function"]["name"], "list_tasks")
        self.assertEqual(msgs[2]["tool_call_id"], "1")
        self.s.clear_chat()
        self.assertEqual(self.s.load_chat_messages(10), [])

    def test_thread_safety(self):
        errors = []

        def worker(n):
            try:
                for i in range(30):
                    self.s.add_task("t{}-{}".format(n, i))
                    self.s.list_tasks("all")
            except Exception as e:  # 收集线程内的异常
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(self.s.list_tasks("all")), 150)

    def test_daily_backup(self):
        self.s.add_task("要备份的任务")
        backup_dir = self.cfg.data_dir / "backups"
        for i in range(3):  # 造几份旧备份，测试只保留最近 2 份
            (backup_dir).mkdir(parents=True, exist_ok=True)
            (backup_dir / "agent-2020-01-0{}.db".format(i + 1)).write_bytes(b"")
        dest = daily_backup(self.s, backup_dir, keep=2)
        self.assertEqual(dest.name, "agent-{}.db".format(date.today().isoformat()))
        self.assertIsNone(daily_backup(self.s, backup_dir, keep=2))  # 当天不重复备份
        self.assertEqual(len(list(backup_dir.glob("agent-*.db"))), 2)
        conn = sqlite3.connect(str(dest))
        self.assertEqual(conn.execute("SELECT title FROM tasks").fetchone()[0], "要备份的任务")
        conn.close()


if __name__ == "__main__":
    unittest.main()
