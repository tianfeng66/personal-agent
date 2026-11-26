"""外部集成：通知/微信推送、macOS 日历、邮件、网页搜索。"""

import html
import re


class IntegrationError(Exception):
    """外部服务未配置或调用失败。"""


_DROP = re.compile(r"<(script|style|noscript|svg|template)\b[^>]*>.*?</\1\s*>", re.I | re.S)
_BREAK = re.compile(r"<(br|/p|/div|/li|/h[1-6]|/tr|/section|/article|/header|/footer)\b[^>]*>", re.I)
_TAG = re.compile(r"<[^>]+>")


def html_to_text(raw: str) -> str:
    """把 HTML 粗略转成纯文本。"""
    text = _DROP.sub(" ", raw)
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    text = _BREAK.sub("\n", text)
    text = _TAG.sub(" ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v\u00a0]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text)
    return text.strip()


def truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "…（已截断）"
