"""macOS「日历」App 读写（通过 AppleScript）。

所有在「日历」App 里登录的账户（iCloud / Google / Exchange 等）都能读到。
首次使用时 macOS 会弹窗询问是否允许控制「日历」，需要点「好」。
限制：重复事件只能读到它的第一次发生。
"""

import subprocess
import sys
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from ..storage import validate_date, validate_datetime
from . import IntegrationError

_HELPERS = '''
on mkdt(y, m, d, h, mi)
	set dt to current date
	set day of dt to 1
	set year of dt to (y as integer)
	set month of dt to (m as integer)
	set day of dt to (d as integer)
	set time of dt to ((h as integer) * hours + (mi as integer) * minutes)
	return dt
end mkdt

on iso(dt)
	return (dt as «class isot» as string)
end iso
'''

_LIST = '''
on run argv
	set US to character id 31
	set RS to character id 30
	set d1 to my mkdt(item 1 of argv, item 2 of argv, item 3 of argv, 0, 0)
	set d2 to d1 + (item 4 of argv as integer) * days
	set out to ""
	tell application "Calendar"
		repeat with c in calendars
			set cname to name of c
			set evs to (every event of c whose start date ≥ d1 and start date < d2)
			repeat with e in evs
				set s to summary of e
				if s is missing value then set s to ""
				set loc to location of e
				if loc is missing value then set loc to ""
				set out to out & cname & US & s & US & my iso(start date of e) & US & my iso(end date of e) & US & loc & US & ((allday event of e) as string) & RS
			end repeat
		end repeat
	end tell
	return out
end run
''' + _HELPERS

_ADD = '''
on run argv
	set t to item 1 of argv
	set d1 to my mkdt(item 2 of argv, item 3 of argv, item 4 of argv, item 5 of argv, item 6 of argv)
	set d2 to my mkdt(item 7 of argv, item 8 of argv, item 9 of argv, item 10 of argv, item 11 of argv)
	set loc to item 12 of argv
	set calName to item 13 of argv
	tell application "Calendar"
		if calName is "" then
			set cal to first calendar whose writable is true
		else
			set cal to first calendar whose name is calName
		end if
		make new event at end of events of cal with properties {summary:t, start date:d1, end date:d2, location:loc}
		return name of cal
	end tell
end run
''' + _HELPERS


def available() -> bool:
    return sys.platform == "darwin"


def _run(script: str, args: List[Any], timeout: int = 90) -> str:
    if not available():
        raise IntegrationError("日历功能仅支持 macOS")
    try:
        r = subprocess.run(["osascript", "-"] + [str(a) for a in args], input=script,
                           capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise IntegrationError("日历操作超时（可能在等待你授权，或日历事件太多）")
    except OSError as e:
        raise IntegrationError("无法调用 osascript：{}".format(e))
    if r.returncode != 0:
        err = r.stderr.strip()
        if "-1743" in err or "not allowed" in err.lower() or "不允许" in err:
            raise IntegrationError("没有日历权限：请到「系统设置 → 隐私与安全性 → 自动化」，"
                                   "允许终端（或 Kiro）控制「日历」")
        raise IntegrationError("日历操作失败：" + err[-300:])
    return r.stdout


def list_events(start_date: Optional[str] = None, days: int = 1) -> List[Dict[str, Any]]:
    d = date.fromisoformat(validate_date(start_date)) if start_date else date.today()
    days = max(1, min(int(days), 31))
    out = _run(_LIST, [d.year, d.month, d.day, days])
    events = []
    for rec in out.strip("\n").split("\x1e"):
        parts = rec.split("\x1f")
        if len(parts) < 6:
            continue
        cal, title, start, end, loc, allday = parts[:6]
        events.append({"calendar": cal, "title": title, "start": start, "end": end,
                       "location": loc, "all_day": allday.strip() == "true"})
    events.sort(key=lambda e: e["start"])
    return events


def add_event(title: str, start: str, end: Optional[str] = None,
              location: str = "", calendar: str = "") -> Dict[str, Any]:
    title = (title or "").strip()
    if not title:
        raise ValueError("日程标题不能为空")
    s = datetime.strptime(validate_datetime(start), "%Y-%m-%d %H:%M")
    e = datetime.strptime(validate_datetime(end), "%Y-%m-%d %H:%M") if end else s + timedelta(hours=1)
    if e <= s:
        raise ValueError("结束时间必须晚于开始时间")
    cal_name = _run(_ADD, [title, s.year, s.month, s.day, s.hour, s.minute,
                           e.year, e.month, e.day, e.hour, e.minute,
                           location or "", calendar or ""]).strip()
    return {"title": title, "start": s.strftime("%Y-%m-%d %H:%M"),
            "end": e.strftime("%Y-%m-%d %H:%M"), "calendar": cal_name}
