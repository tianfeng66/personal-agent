"""SQLite 本地存储：任务、笔记、工作日志、长期记忆、提醒、邮件草稿、对话记录。

Store 是线程安全的：同一个连接允许多线程使用，所有公开方法都在一把可重入锁内执行。
"""

import functools
import json
import re
import sqlite3
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

PRIORITIES = ("high", "medium", "low")
STATUSES = ("todo", "doing", "done")

MAIN_AGENT_NAME = "总助理"
MAX_AGENTS = 10
# 自定义智能体可以被授权的工具类别（具体工具见 tools.TOOL_GROUPS）
AGENT_TOOL_GROUPS = ("tasks", "notes", "worklogs", "reminders", "calendar", "search", "email", "wechat")

SCHEMA = """
CREATE TABLE IF NOT EXISTS calendar_events (
    id INTEGER PRIMARY KEY, title TEXT NOT NULL, start TEXT NOT NULL,
    end TEXT NOT NULL, location TEXT NOT NULL, calendar TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    notes TEXT DEFAULT '',
    priority TEXT NOT NULL DEFAULT 'medium' CHECK (priority IN ('high','medium','low')),
    status TEXT NOT NULL DEFAULT 'todo' CHECK (status IN ('todo','doing','done')),
    due_date TEXT,
    project TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    tags TEXT DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS worklogs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    minutes INTEGER,
    project TEXT DEFAULT '',
    log_date TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    role TEXT NOT NULL,
    instructions TEXT NOT NULL DEFAULT '',
    tool_groups TEXT NOT NULL DEFAULT '[]',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    remind_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','sent','cancelled')),
    seen INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    sent_at TEXT
);
CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    tool_calls TEXT,
    tool_call_id TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS email_drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    to_addr TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','sent','discarded')),
    created_at TEXT NOT NULL,
    sent_at TEXT
);
"""

_PRIORITY_ORDER = "CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END"


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def today_str() -> str:
    return date.today().isoformat()


def validate_date(value: str) -> str:
    """校验 YYYY-MM-DD 格式，返回规范化字符串。"""
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date().isoformat()
    except (ValueError, AttributeError):
        raise ValueError("日期格式必须是 YYYY-MM-DD，收到: {!r}".format(value))


def now_minute() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def validate_datetime(value: str) -> str:
    """校验 YYYY-MM-DD HH:MM（也接受网页 datetime-local 的 T 分隔），返回规范化字符串。"""
    v = str(value or "").strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(v, fmt).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            pass
    raise ValueError("时间格式必须是 YYYY-MM-DD HH:MM，收到: {!r}".format(value))


class Store:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(str(db_path), timeout=10, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    @staticmethod
    def _rows(cur: sqlite3.Cursor) -> List[Dict[str, Any]]:
        return [dict(r) for r in cur.fetchall()]

    # ---------- tasks ----------
    def add_task(
        self,
        title: str,
        priority: str = "medium",
        due_date: Optional[str] = None,
        project: str = "",
        notes: str = "",
    ) -> Dict[str, Any]:
        title = (title or "").strip()
        if not title:
            raise ValueError("任务标题不能为空")
        if priority not in PRIORITIES:
            raise ValueError("priority 必须是 high/medium/low")
        if due_date:
            due_date = validate_date(due_date)
        cur = self.conn.execute(
            "INSERT INTO tasks (title, priority, due_date, project, notes, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (title, priority, due_date or None, project or "", notes or "", now_iso()),
        )
        self.conn.commit()
        return self.get_task(cur.lastrowid)

    def get_task(self, task_id: int) -> Dict[str, Any]:
        row = self.conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise ValueError("任务 #{} 不存在".format(task_id))
        return dict(row)

    def list_tasks(
        self,
        status: str = "open",
        project: Optional[str] = None,
        due_before: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """status: open(未完成) / todo / doing / done / all"""
        clauses, params = [], []  # type: List[str], List[Any]
        if status == "open":
            clauses.append("status != 'done'")
        elif status in STATUSES:
            clauses.append("status = ?")
            params.append(status)
        elif status != "all":
            raise ValueError("status 必须是 open/todo/doing/done/all")
        if project:
            clauses.append("project = ?")
            params.append(project)
        if due_before:
            clauses.append("due_date IS NOT NULL AND due_date <= ?")
            params.append(validate_date(due_before))
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = (
            "SELECT * FROM tasks {} ORDER BY "
            "CASE WHEN due_date IS NULL THEN 1 ELSE 0 END, due_date, {}, id"
        ).format(where, _PRIORITY_ORDER)
        return self._rows(self.conn.execute(sql, params))

    def update_task(self, task_id: int, **fields: Any) -> Dict[str, Any]:
        allowed = {"title", "notes", "priority", "status", "due_date", "project"}
        updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if not updates:
            return self.get_task(task_id)
        current = self.get_task(task_id)
        if "priority" in updates and updates["priority"] not in PRIORITIES:
            raise ValueError("priority 必须是 high/medium/low")
        if "status" in updates:
            if updates["status"] not in STATUSES:
                raise ValueError("status 必须是 todo/doing/done")
            if updates["status"] == "done" and current["status"] != "done":
                updates["completed_at"] = now_iso()
            elif updates["status"] != "done":
                updates["completed_at"] = None
        if "due_date" in updates:
            updates["due_date"] = validate_date(updates["due_date"]) if updates["due_date"] else None
        if "title" in updates and not str(updates["title"]).strip():
            raise ValueError("任务标题不能为空")
        # 列名来自白名单，值使用参数化
        set_sql = ", ".join("{} = ?".format(k) for k in updates)
        self.conn.execute(
            "UPDATE tasks SET {} WHERE id = ?".format(set_sql),
            list(updates.values()) + [task_id],
        )
        self.conn.commit()
        return self.get_task(task_id)

    def delete_task(self, task_id: int) -> None:
        self.get_task(task_id)
        self.conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        self.conn.commit()

    # ---------- notes ----------
    def add_note(self, title: str, content: str, tags: str = "") -> Dict[str, Any]:
        if not (title or "").strip() or not (content or "").strip():
            raise ValueError("笔记标题和内容不能为空")
        cur = self.conn.execute(
            "INSERT INTO notes (title, content, tags, created_at) VALUES (?, ?, ?, ?)",
            (title.strip(), content.strip(), tags or "", now_iso()),
        )
        self.conn.commit()
        return dict(self.conn.execute("SELECT * FROM notes WHERE id = ?", (cur.lastrowid,)).fetchone())

    def search_notes(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        like = "%{}%".format((query or "").strip())
        return self._rows(
            self.conn.execute(
                "SELECT * FROM notes WHERE title LIKE ? OR content LIKE ? OR tags LIKE ?"
                " ORDER BY id DESC LIMIT ?",
                (like, like, like, limit),
            )
        )

    def delete_note(self, note_id: int) -> None:
        cur = self.conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        self.conn.commit()
        if cur.rowcount == 0:
            raise ValueError("笔记 #{} 不存在".format(note_id))

    def delete_note(self, note_id: int) -> None:
        cur = self.conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        self.conn.commit()
        if cur.rowcount == 0:
            raise ValueError("笔记 #{} 不存在".format(note_id))

    def delete_note(self, note_id: int) -> None:
        cur = self.conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        self.conn.commit()
        if cur.rowcount == 0:
            raise ValueError("笔记 #{} 不存在".format(note_id))

    # ---------- worklogs ----------
    def add_worklog(
        self,
        content: str,
        minutes: Optional[int] = None,
        project: str = "",
        log_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not (content or "").strip():
            raise ValueError("日志内容不能为空")
        if minutes is not None and minutes < 0:
            raise ValueError("minutes 不能为负数")
        log_date = validate_date(log_date) if log_date else today_str()
        cur = self.conn.execute(
            "INSERT INTO worklogs (content, minutes, project, log_date, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (content.strip(), minutes, project or "", log_date, now_iso()),
        )
        self.conn.commit()
        return dict(self.conn.execute("SELECT * FROM worklogs WHERE id = ?", (cur.lastrowid,)).fetchone())

    def delete_worklog(self, log_id: int) -> None:
        cur = self.conn.execute("DELETE FROM worklogs WHERE id = ?", (log_id,))
        self.conn.commit()
        if cur.rowcount == 0:
            raise ValueError("日志 #{} 不存在".format(log_id))

    def list_worklogs(self, log_date: str) -> List[Dict[str, Any]]:
        return self._rows(
            self.conn.execute(
                "SELECT * FROM worklogs WHERE log_date = ? ORDER BY id", (validate_date(log_date),)
            )
        )

    def delete_worklog(self, log_id: int) -> None:
        cur = self.conn.execute("DELETE FROM worklogs WHERE id = ?", (log_id,))
        self.conn.commit()
        if cur.rowcount == 0:
            raise ValueError("日志 #{} 不存在".format(log_id))

    def delete_worklog(self, log_id: int) -> None:
        cur = self.conn.execute("DELETE FROM worklogs WHERE id = ?", (log_id,))
        self.conn.commit()
        if cur.rowcount == 0:
            raise ValueError("日志 #{} 不存在".format(log_id))

    # ---------- memories ----------
    def add_memory(self, content: str) -> Dict[str, Any]:
        if not (content or "").strip():
            raise ValueError("记忆内容不能为空")
        cur = self.conn.execute(
            "INSERT INTO memories (content, created_at) VALUES (?, ?)", (content.strip(), now_iso())
        )
        self.conn.commit()
        return {"id": cur.lastrowid, "content": content.strip()}

    def list_memories(self) -> List[Dict[str, Any]]:
        return self._rows(self.conn.execute("SELECT * FROM memories ORDER BY id"))

    def delete_memory(self, memory_id: int) -> None:
        cur = self.conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        self.conn.commit()
        if cur.rowcount == 0:
            raise ValueError("记忆 #{} 不存在".format(memory_id))

    # ---------- reminders ----------
    def add_reminder(self, content: str, remind_at: str) -> Dict[str, Any]:
        content = (content or "").strip()
        if not content:
            raise ValueError("提醒内容不能为空")
        remind_at = validate_datetime(remind_at)
        if remind_at < now_minute():
            raise ValueError("提醒时间 {} 已经过去了".format(remind_at))
        cur = self.conn.execute(
            "INSERT INTO reminders (content, remind_at, created_at) VALUES (?, ?, ?)",
            (content, remind_at, now_iso()),
        )
        self.conn.commit()
        return self._reminder(cur.lastrowid)

    def _reminder(self, rid: int) -> Dict[str, Any]:
        row = self.conn.execute("SELECT * FROM reminders WHERE id = ?", (rid,)).fetchone()
        if row is None:
            raise ValueError("提醒 #{} 不存在".format(rid))
        return dict(row)

    def list_reminders(self, status: str = "pending") -> List[Dict[str, Any]]:
        if status == "pending":
            sql = "SELECT * FROM reminders WHERE status = 'pending' ORDER BY remind_at, id"
        elif status == "all":
            sql = "SELECT * FROM reminders ORDER BY remind_at DESC, id DESC LIMIT 100"
        else:
            raise ValueError("status 必须是 pending/all")
        return self._rows(self.conn.execute(sql))

    def cancel_reminder(self, rid: int) -> Dict[str, Any]:
        cur = self.conn.execute(
            "UPDATE reminders SET status = 'cancelled' WHERE id = ? AND status = 'pending'", (rid,))
        self.conn.commit()
        if cur.rowcount == 0:
            raise ValueError("提醒 #{} 不存在或已经提醒过".format(rid))
        return self._reminder(rid)

    def due_reminders(self) -> List[Dict[str, Any]]:
        return self._rows(self.conn.execute(
            "SELECT * FROM reminders WHERE status = 'pending' AND remind_at <= ? ORDER BY remind_at",
            (now_minute(),)))

    def mark_reminder_sent(self, rid: int) -> None:
        self.conn.execute(
            "UPDATE reminders SET status = 'sent', sent_at = ? WHERE id = ?", (now_iso(), rid))
        self.conn.commit()

    def pop_unseen_reminders(self) -> List[Dict[str, Any]]:
        """返回已触发但网页还没显示过的提醒，并标记为已显示。"""
        rows = self._rows(self.conn.execute(
            "SELECT * FROM reminders WHERE status = 'sent' AND seen = 0 ORDER BY remind_at"))
        if rows:
            self.conn.executemany("UPDATE reminders SET seen = 1 WHERE id = ?",
                                  [(r["id"],) for r in rows])
            self.conn.commit()
        return rows

    # ---------- email drafts ----------
    def add_draft(self, to_addr: str, subject: str, body: str) -> Dict[str, Any]:
        if not (to_addr or "").strip():
            raise ValueError("收件人不能为空")
        if not (subject or "").strip():
            raise ValueError("邮件主题不能为空")
        if "\r" in subject or "\n" in subject:
            raise ValueError("主题不能包含换行")
        if not (body or "").strip():
            raise ValueError("邮件正文不能为空")
        cur = self.conn.execute(
            "INSERT INTO email_drafts (to_addr, subject, body, created_at) VALUES (?, ?, ?, ?)",
            (to_addr.strip(), subject.strip(), body, now_iso()))
        self.conn.commit()
        return self.get_draft(cur.lastrowid)

    def get_draft(self, did: int) -> Dict[str, Any]:
        row = self.conn.execute("SELECT * FROM email_drafts WHERE id = ?", (did,)).fetchone()
        if row is None:
            raise ValueError("草稿 #{} 不存在".format(did))
        return dict(row)

    def list_drafts(self) -> List[Dict[str, Any]]:
        return self._rows(self.conn.execute(
            "SELECT * FROM email_drafts WHERE status = 'pending' ORDER BY id DESC"))

    def set_draft_status(self, did: int, status: str) -> Dict[str, Any]:
        cur = self.conn.execute(
            "UPDATE email_drafts SET status = ?, sent_at = ? WHERE id = ? AND status = 'pending'",
            (status, now_iso() if status == "sent" else None, did))
        self.conn.commit()
        if cur.rowcount == 0:
            raise ValueError("草稿 #{} 不存在或已处理".format(did))
        return self.get_draft(did)

    # ---------- agents（自定义智能体）----------
    def _agent_row(self, row: sqlite3.Row) -> Dict[str, Any]:
        d = dict(row)
        d["tool_groups"] = json.loads(d["tool_groups"] or "[]")
        d["enabled"] = bool(d["enabled"])
        return d

    def _validate_agent(self, name: str, role: str, instructions: str,
                        tool_groups: List[str]) -> None:
        if not name or len(name) > 20 or re.search(r"[\s@,，]", name):
            raise ValueError("名字需要 1-20 个字，不能包含空格、@ 或逗号")
        if name in (MAIN_AGENT_NAME, "用户"):
            raise ValueError("「{}」是保留名字".format(name))
        if not role or len(role) > 100:
            raise ValueError("职责需要 1-100 个字")
        if len(instructions) > 2000:
            raise ValueError("工作要求不能超过 2000 字")
        bad = [g for g in tool_groups if g not in AGENT_TOOL_GROUPS]
        if bad:
            raise ValueError("未知的工具类别：" + "、".join(bad))

    def add_agent(self, name: str, role: str, instructions: str = "",
                  tool_groups: Optional[List[str]] = None) -> Dict[str, Any]:
        name, role, instructions = (name or "").strip(), (role or "").strip(), (instructions or "").strip()
        groups = list(dict.fromkeys(tool_groups or []))
        self._validate_agent(name, role, instructions, groups)
        count = self.conn.execute("SELECT COUNT(*) FROM agents").fetchone()[0]
        if count >= MAX_AGENTS:
            raise ValueError("最多创建 {} 个智能体".format(MAX_AGENTS))
        try:
            cur = self.conn.execute(
                "INSERT INTO agents (name, role, instructions, tool_groups, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (name, role, instructions, json.dumps(groups), now_iso()))
        except sqlite3.IntegrityError:
            raise ValueError("已经有叫「{}」的智能体了".format(name))
        self.conn.commit()
        return self.get_agent(cur.lastrowid)

    def get_agent(self, agent_id: int) -> Dict[str, Any]:
        row = self.conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
        if row is None:
            raise ValueError("智能体 #{} 不存在".format(agent_id))
        return self._agent_row(row)

    def find_agent(self, name: str) -> Optional[Dict[str, Any]]:
        row = self.conn.execute(
            "SELECT * FROM agents WHERE name = ? AND enabled = 1", ((name or "").strip(),)).fetchone()
        return self._agent_row(row) if row else None

    def list_agents(self, enabled_only: bool = False) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM agents {} ORDER BY id".format("WHERE enabled = 1" if enabled_only else "")
        return [self._agent_row(r) for r in self.conn.execute(sql).fetchall()]

    def update_agent(self, agent_id: int, **fields: Any) -> Dict[str, Any]:
        cur = self.get_agent(agent_id)
        merged = {k: fields[k] if fields.get(k) is not None else cur[k]
                  for k in ("name", "role", "instructions", "tool_groups", "enabled")}
        name = str(merged["name"]).strip()
        role = str(merged["role"]).strip()
        instructions = str(merged["instructions"]).strip()
        groups = list(dict.fromkeys(merged["tool_groups"] or []))
        self._validate_agent(name, role, instructions, groups)
        try:
            self.conn.execute(
                "UPDATE agents SET name = ?, role = ?, instructions = ?, tool_groups = ?, enabled = ?"
                " WHERE id = ?",
                (name, role, instructions, json.dumps(groups), 1 if merged["enabled"] else 0, agent_id))
        except sqlite3.IntegrityError:
            raise ValueError("已经有叫「{}」的智能体了".format(name))
        self.conn.commit()
        return self.get_agent(agent_id)

    def delete_agent(self, agent_id: int) -> None:
        self.get_agent(agent_id)
        self.conn.execute("DELETE FROM agents WHERE id = ?", (agent_id,))
        self.conn.commit()

    # ---------- chat history ----------
    def add_chat_message(self, msg: Dict[str, Any]) -> None:
        tool_calls = msg.get("tool_calls")
        self.conn.execute(
            "INSERT INTO chat_messages (role, content, tool_calls, tool_call_id, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (msg["role"], msg.get("content") or "",
             json.dumps(tool_calls, ensure_ascii=False) if tool_calls else None,
             msg.get("tool_call_id"), now_iso()))
        self.conn.commit()

    def load_chat_messages(self, limit: int) -> List[Dict[str, Any]]:
        """返回最近 limit 条消息（按时间正序），格式与发给模型的一致。"""
        rows = self._rows(self.conn.execute(
            "SELECT * FROM (SELECT * FROM chat_messages ORDER BY id DESC LIMIT ?) ORDER BY id",
            (limit,)))
        out = []
        for r in rows:
            m: Dict[str, Any] = {"role": r["role"], "content": r["content"]}
            if r["tool_calls"]:
                m["tool_calls"] = json.loads(r["tool_calls"])
            if r["tool_call_id"]:
                m["tool_call_id"] = r["tool_call_id"]
            m["created_at"] = r["created_at"]
            out.append(m)
        return out

    def clear_chat(self) -> None:
        self.conn.execute("DELETE FROM chat_messages")
        self.conn.commit()

    # ---------- backup ----------
    def backup_to(self, dest: Path) -> None:
        """用 SQLite 在线备份接口复制整个数据库（写入中也能安全备份）。"""
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".tmp")
        target = sqlite3.connect(str(tmp))
        try:
            self.conn.backup(target)
        finally:
            target.close()
        tmp.replace(dest)

    # ---------- summary ----------
    def daily_summary(self, day: Optional[str] = None) -> Dict[str, Any]:
        day = validate_date(day) if day else today_str()
        completed = self._rows(
            self.conn.execute(
                "SELECT * FROM tasks WHERE status = 'done' AND substr(completed_at, 1, 10) = ? ORDER BY id",
                (day,),
            )
        )
        open_tasks = self.list_tasks("open")
        overdue = [t for t in open_tasks if t["due_date"] and t["due_date"] < day]
        due_today = [t for t in open_tasks if t["due_date"] == day]
        logs = self.list_worklogs(day)
        return {
            "date": day,
            "completed_tasks": completed,
            "worklogs": logs,
            "total_logged_minutes": sum(l["minutes"] or 0 for l in logs),
            "due_today": due_today,
            "overdue": overdue,
            "open_task_count": len(open_tasks),
        }


def _locked(fn):
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return fn(self, *args, **kwargs)
    return wrapper


# 给 Store 的所有实例方法加锁（多线程网页服务共用一个连接）
for _name, _attr in list(vars(Store).items()):
    if _name != "__init__" and not isinstance(_attr, (staticmethod, classmethod)) and callable(_attr):
        setattr(Store, _name, _locked(_attr))
