"""通知：macOS 系统通知 + 微信推送（Server酱 / 企业微信群机器人）。

说明：微信没有面向个人号的官方接口，这里只做「推送消息给自己」，不读取聊天记录。
"""

import json
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List

from . import IntegrationError


def mac_notify(title: str, message: str) -> bool:
    """弹出 macOS 通知。参数通过 argv 传入，避免 AppleScript 注入。"""
    if sys.platform != "darwin":
        return False
    try:
        r = subprocess.run(
            ["osascript",
             "-e", "on run argv",
             "-e", 'display notification (item 2 of argv) with title (item 1 of argv) sound name "Glass"',
             "-e", "end run",
             title, message[:200]],
            capture_output=True, timeout=10,
        )
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _post(url: str, data: bytes, ctype: str) -> Dict[str, Any]:
    req = urllib.request.Request(url, data=data, headers={"Content-Type": ctype}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode("utf-8") or "{}")
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise IntegrationError("推送失败：{}".format(e))


def wechat_push(cfg, title: str, content: str) -> List[str]:
    """推送到已配置的微信渠道，返回成功的渠道名称。"""
    sent, errors = [], []
    if cfg.serverchan_key:
        try:
            if not re.fullmatch(r"[A-Za-z0-9_]+", cfg.serverchan_key):
                raise IntegrationError("SERVERCHAN_KEY 格式不正确")
            data = urllib.parse.urlencode({"title": title[:32], "desp": content}).encode("utf-8")
            res = _post("https://sctapi.ftqq.com/{}.send".format(cfg.serverchan_key), data,
                        "application/x-www-form-urlencoded")
            if res.get("code") != 0:
                raise IntegrationError("Server酱返回错误：{}".format(res.get("message")))
            sent.append("微信（Server酱）")
        except IntegrationError as e:
            errors.append(str(e))
    if cfg.wecom_webhook:
        try:
            if not cfg.wecom_webhook.startswith("https://qyapi.weixin.qq.com/"):
                raise IntegrationError("WECOM_WEBHOOK_URL 必须以 https://qyapi.weixin.qq.com/ 开头")
            body = {"msgtype": "text", "text": {"content": title + "\n" + content}}
            res = _post(cfg.wecom_webhook, json.dumps(body, ensure_ascii=False).encode("utf-8"),
                        "application/json")
            if res.get("errcode") != 0:
                raise IntegrationError("企业微信返回错误：{}".format(res.get("errmsg")))
            sent.append("企业微信")
        except IntegrationError as e:
            errors.append(str(e))
    if not sent:
        raise IntegrationError("；".join(errors) or "未配置微信推送（SERVERCHAN_KEY 或 WECOM_WEBHOOK_URL）")
    return sent
