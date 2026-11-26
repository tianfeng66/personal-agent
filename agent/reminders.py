"""后台调度：每 20 秒检查一次到期提醒，推送系统通知和微信；每天自动备份一次数据库。

程序没运行时不会提醒；下次启动时，错过的提醒会立刻补发。
"""

import sys
import threading
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

from .integrations import IntegrationError, notify
from .storage import Store

BACKUP_KEEP_DAYS = 14


def deliver(cfg, reminder: Dict[str, Any]) -> List[str]:
    channels = []
    if notify.desktop_notify("私人工作助理 · 提醒", reminder["content"]):
        channels.append("系统通知")
    if cfg.wechat_ready:
        try:
            channels += notify.wechat_push(
                cfg, "提醒：" + reminder["content"][:20],
                "{}\n\n提醒时间：{}".format(reminder["content"], reminder["remind_at"]))
        except IntegrationError as e:
            print("[提醒] 微信推送失败：{}".format(e), file=sys.stderr)
    print("[提醒] {} → {}".format(reminder["content"], "、".join(channels) or "仅网页"),
          file=sys.stderr)
    return channels


class ReminderScheduler(threading.Thread):
    def __init__(self, cfg, interval: int = 20):
        super().__init__(name="reminders", daemon=True)
        self.cfg = cfg
        self.interval = interval
        self._halt = threading.Event()

    def stop(self) -> None:
        self._halt.set()

    def run(self) -> None:
        store = Store(self.cfg.db_path)  # SQLite 连接不能跨线程，单独开一个
        try:
            while True:
                self.tick(store)
                if self._halt.wait(self.interval):
                    break
        finally:
            store.close()

    def tick(self, store: Store) -> None:
        try:
            for r in store.due_reminders():
                deliver(self.cfg, r)
                store.mark_reminder_sent(r["id"])
        except Exception as e:  # 调度线程不能因单次异常退出
            print("[提醒] 检查失败：{}".format(e), file=sys.stderr)
        try:
            daily_backup(store, self.cfg.data_dir / "backups")
        except Exception as e:
            print("[备份] 失败：{}".format(e), file=sys.stderr)


def daily_backup(store: Store, backup_dir: Path, keep: int = BACKUP_KEEP_DAYS) -> Optional[Path]:
    """每天备份一次数据库，只保留最近 keep 份。今天已经备份过则什么都不做。"""
    dest = backup_dir / "agent-{}.db".format(date.today().isoformat())
    if dest.exists():
        return None
    store.backup_to(dest)
    old = sorted(backup_dir.glob("agent-*.db"))[:-keep]
    for p in old:
        p.unlink()
    print("[备份] 已保存 {}".format(dest), file=sys.stderr)
    return dest
