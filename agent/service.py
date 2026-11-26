"""开机自动运行（macOS launchd）。

  python3 -m agent install     安装并立即在后台启动，以后登录 Mac 时自动运行
  python3 -m agent uninstall   停止并取消开机运行
  python3 -m agent status      查看后台运行状态

服务只在当前用户登录后运行（LaunchAgent），不需要管理员权限。
"""

import os
import plistlib
import socket
import subprocess
import sys
import time
from pathlib import Path

from .config import PROJECT_ROOT, Config

LABEL = "com.personal-agent.web"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / (LABEL + ".plist")


def _domain() -> str:
    return "gui/{}".format(os.getuid())


def _launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl"] + list(args), capture_output=True, text=True)


def _is_loaded() -> bool:
    return _launchctl("print", "{}/{}".format(_domain(), LABEL)).returncode == 0


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _require_mac() -> None:
    if sys.platform != "darwin":
        print("开机自动运行目前只支持 macOS。", file=sys.stderr)
        sys.exit(1)


def install(cfg: Config, port: int) -> None:
    _require_mac()
    if _is_loaded():
        _launchctl("bootout", "{}/{}".format(_domain(), LABEL))
        for _ in range(20):  # 等旧进程退出、释放端口，最多 10 秒
            if not _port_in_use(port):
                break
            time.sleep(0.5)
    if _port_in_use(port):
        print("端口 {} 正在被使用。请先关掉手动运行的网页服务（在那个终端窗口按 Ctrl+C），"
              "然后重新执行本命令。".format(port), file=sys.stderr)
        sys.exit(1)
    log_dir = cfg.data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    plist = {
        "Label": LABEL,
        "ProgramArguments": [sys.executable, "-m", "agent", "web", "--port", str(port)],
        "WorkingDirectory": str(PROJECT_ROOT),
        "RunAtLoad": True,
        "KeepAlive": True,          # 意外退出后自动重启
        "ThrottleInterval": 10,
        "ProcessType": "Interactive",
        "StandardOutPath": str(log_dir / "server.log"),
        "StandardErrorPath": str(log_dir / "server.log"),
        "EnvironmentVariables": {"PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"},
    }
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(str(PLIST_PATH), "wb") as f:
        plistlib.dump(plist, f)
    r = _launchctl("bootstrap", _domain(), str(PLIST_PATH))
    if r.returncode != 0:
        print("启动失败：{}".format((r.stderr or r.stdout).strip()), file=sys.stderr)
        sys.exit(1)
    print("已安装。现在已在后台运行，以后登录 Mac 时会自动启动。")
    print("网页地址：http://127.0.0.1:{}".format(port))
    print("运行日志：{}".format(log_dir / "server.log"))


def uninstall() -> None:
    _require_mac()
    if _is_loaded():
        _launchctl("bootout", "{}/{}".format(_domain(), LABEL))
    if PLIST_PATH.exists():
        PLIST_PATH.unlink()
        print("已停止后台运行，并取消开机自动启动。")
    else:
        print("没有安装开机自动运行。")


def status(port: int) -> None:
    _require_mac()
    print("开机自动运行：{}".format("已安装" if PLIST_PATH.exists() else "未安装"))
    print("后台服务：{}".format("运行中" if _is_loaded() else "未运行"))
    print("网页 http://127.0.0.1:{} ：{}".format(port, "可以访问" if _port_in_use(port) else "无法访问"))
