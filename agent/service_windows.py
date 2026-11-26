"""Windows desktop launcher and per-user logon task."""
import hashlib
import json
import os
import subprocess
import sys
import time
import webbrowser
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

from .config import PROJECT_ROOT, load_config
from .service import _port_in_use

INSTALL_ID = hashlib.sha256(str(PROJECT_ROOT).casefold().encode()).hexdigest()[:16]


def running(port):
    url = "http://127.0.0.1:{}/api/status".format(port)
    try:
        with build_opener(ProxyHandler({})).open(url, timeout=1) as response:
            return json.load(response).get("installation") == INSTALL_ID
    except (OSError, ValueError, AttributeError):
        return False


def task(operation, port=8765):
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(PROJECT_ROOT / "agent" / "logon.ps1"), operation],
        env=dict(os.environ, PA_ROOT=str(PROJECT_ROOT), PA_EXE=str(pythonw), PA_PORT=str(port),
                 PA_TASK="PersonalAgent-" + INSTALL_ID))
    if result.returncode:
        raise SystemExit(result.returncode)


def install(cfg, port):
    if not 1 <= port <= 65535:
        raise SystemExit("端口需要在 1 到 65535 之间")
    task("install", port)
    for _ in range(30):
        if running(port):
            print("已启用登录自启：http://127.0.0.1:{}".format(port))
            return
        time.sleep(0.3)
    raise SystemExit("任务已注册，但服务未就绪。请检查端口以及 data/logs/server.log。")


def uninstall():
    task("uninstall")


def status(port):
    task("status")
    print("此副本网页服务：" + ("运行中" if running(port) else "未运行"))


def desktop(port):
    from .server import serve
    url = "http://127.0.0.1:{}".format(port)
    if running(port):
        webbrowser.open(url)
        return
    serve(load_config(), port, on_ready=lambda: webbrowser.open(url))


def background(port):
    from .server import serve
    cfg = load_config()
    logs = cfg.data_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    with (logs / "server.log").open("a", encoding="utf-8", buffering=1) as log:
        with redirect_stdout(log), redirect_stderr(log):
            serve(cfg, port)


if __name__ == "__main__":
    mode, port = sys.argv[1:]
    {"desktop": desktop, "background": background}[mode](int(port))
