"""邮箱：IMAP 读取（只读，不会标记已读）+ SMTP 发送。

发送只在用户于网页上点击「确认发送」后执行，AI 只能创建草稿。
"""

import email
import imaplib
import re
import smtplib
import ssl
from datetime import date, timedelta
from email import policy
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List

from . import IntegrationError, html_to_text, truncate

_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_EMAIL_RE = re.compile(r"^[^@\s,;<>\"']+@[^@\s,;<>\"']+\.[^@\s,;<>\"']+$")


def validate_recipients(to: str) -> List[str]:
    addrs = [a.strip() for a in re.split(r"[,;，；]", to or "") if a.strip()]
    if not addrs:
        raise ValueError("收件人不能为空")
    bad = [a for a in addrs if not _EMAIL_RE.match(a)]
    if bad:
        raise ValueError("邮箱地址格式不正确：" + "、".join(bad))
    return addrs


def _require(cfg) -> None:
    if cfg is None or not cfg.email_ready:
        raise IntegrationError("未配置邮箱：请在 .env 填写 EMAIL_ADDRESS、EMAIL_PASSWORD、"
                               "EMAIL_IMAP_HOST、EMAIL_SMTP_HOST")


def _imap(cfg) -> imaplib.IMAP4_SSL:
    _require(cfg)
    try:
        conn = imaplib.IMAP4_SSL(cfg.imap_host, cfg.imap_port,
                                 ssl_context=ssl.create_default_context(), timeout=30)
        conn.login(cfg.email_address, cfg.email_password)
        if re.search(r"(163|126|yeah)\.(com|net)$", cfg.imap_host):
            # 网易邮箱要求先发送 ID 命令，否则 SELECT 会被拒绝
            imaplib.Commands["ID"] = ("AUTH", "SELECTED")
            conn._simple_command("ID", '("name" "personal-agent" "version" "0.1")')
        typ, _ = conn.select("INBOX", readonly=True)
        if typ != "OK":
            raise IntegrationError("无法打开收件箱")
        return conn
    except imaplib.IMAP4.error as e:
        raise IntegrationError("邮箱登录失败（请确认用的是授权码/应用专用密码）：{}".format(e))
    except (OSError, ssl.SSLError) as e:
        raise IntegrationError("连接邮箱服务器失败：{}".format(e))


def _close(conn) -> None:
    try:
        conn.logout()
    except Exception:  # 退出失败不影响结果
        pass


def _fmt_date(value) -> str:
    try:
        return parsedate_to_datetime(str(value)).astimezone().strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, IndexError):
        return str(value or "")


def _uid_of(meta: bytes) -> str:
    m = re.search(rb"UID (\d+)", meta)
    return m.group(1).decode() if m else ""


def list_emails(cfg, days: int = 3, query: str = "", limit: int = 10) -> List[Dict[str, Any]]:
    """列出最近几天的收件箱邮件；query 按主题和发件人筛选。"""
    days = max(1, min(int(days), 30))
    limit = max(1, min(int(limit), 30))
    d = date.today() - timedelta(days=days - 1)
    since = "{:02d}-{}-{}".format(d.day, _MONTHS[d.month - 1], d.year)
    conn = _imap(cfg)
    items = []
    try:
        typ, data = conn.uid("SEARCH", "SINCE", since)
        if typ != "OK":
            raise IntegrationError("搜索邮件失败")
        uids = data[0].split()
        uids = uids[-200:] if query else uids[-limit:]
        if not uids:
            return []
        typ, data = conn.uid("FETCH", b",".join(uids).decode(),
                             "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM DATE)])")
        for part in data or []:
            if not isinstance(part, tuple):
                continue
            try:
                msg = email.message_from_bytes(part[1], policy=policy.default)
                items.append({"uid": _uid_of(part[0]), "from": str(msg.get("From", "")),
                              "subject": str(msg.get("Subject", "")) or "（无主题）",
                              "date": _fmt_date(msg.get("Date"))})
            except Exception:  # 个别邮件头损坏时跳过
                continue
    except imaplib.IMAP4.error as e:
        raise IntegrationError("读取邮件失败：{}".format(e))
    finally:
        _close(conn)
    if query:
        q = query.lower()
        items = [i for i in items if q in i["subject"].lower() or q in i["from"].lower()]
    items.sort(key=lambda i: int(i["uid"] or 0), reverse=True)
    return items[:limit]


def read_email(cfg, uid) -> Dict[str, Any]:
    uid = str(int(uid))
    conn = _imap(cfg)
    try:
        typ, data = conn.uid("FETCH", uid, "(BODY.PEEK[])")
    except imaplib.IMAP4.error as e:
        raise IntegrationError("读取邮件失败：{}".format(e))
    finally:
        _close(conn)
    raw = next((p[1] for p in (data or []) if isinstance(p, tuple)), None)
    if raw is None:
        raise ValueError("邮件 {} 不存在".format(uid))
    msg = email.message_from_bytes(raw, policy=policy.default)
    text = ""
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is not None:
        try:
            text = part.get_content()
        except (LookupError, ValueError):
            text = (part.get_payload(decode=True) or b"").decode("utf-8", "replace")
        if part.get_content_subtype() == "html":
            text = html_to_text(text)
    attachments = [p.get_filename() for p in msg.iter_attachments() if p.get_filename()]
    return {"uid": uid, "from": str(msg.get("From", "")), "to": str(msg.get("To", "")),
            "subject": str(msg.get("Subject", "")), "date": _fmt_date(msg.get("Date")),
            "body": truncate(text.strip(), 6000), "attachments": attachments}


def send_email(cfg, to: str, subject: str, body: str) -> None:
    _require(cfg)
    addrs = validate_recipients(to)
    if "\r" in subject or "\n" in subject:
        raise ValueError("主题不能包含换行")
    msg = EmailMessage()
    msg["From"] = cfg.email_address
    msg["To"] = ", ".join(addrs)
    msg["Subject"] = subject
    msg.set_content(body)
    ctx = ssl.create_default_context()
    try:
        if cfg.smtp_port == 465:
            server = smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port, context=ctx, timeout=30)
        else:
            server = smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=30)
            server.starttls(context=ctx)
        with server:
            server.login(cfg.email_address, cfg.email_password)
            server.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        raise IntegrationError("发信登录失败，请检查授权码/应用专用密码")
    except (smtplib.SMTPException, OSError) as e:
        raise IntegrationError("发送失败：{}".format(e))
