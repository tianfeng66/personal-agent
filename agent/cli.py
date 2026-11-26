"""命令行入口。

  python3 -m agent              进入对话模式
  python3 -m agent tasks        查看未完成任务（无需大模型）
  python3 -m agent report       生成今日日报并保存为 Markdown
  python3 -m agent web          打开网页界面（http://127.0.0.1:8765）
  python3 -m agent install      设为开机自动在后台运行
  python3 -m agent uninstall    取消开机自动运行
  python3 -m agent status       查看后台运行状态
"""

import argparse
import sys

from .config import load_config
from .core import Agent
from .llm import LLMClient, LLMError
from .storage import Store, today_str

HELP = """命令：/tasks 查看任务  /report 生成日报  /reset 清空对话  /quit 退出
直接说话即可，例如：「明天下午3点前把周报发给老板，优先级高」"""

PRIORITY_CN = {"high": "高", "medium": "中", "low": "低"}
STATUS_CN = {"todo": "待办", "doing": "进行中", "done": "已完成"}


def print_tasks(store: Store) -> None:
    tasks = store.list_tasks("open")
    if not tasks:
        print("没有未完成的任务。")
        return
    today = today_str()
    for t in tasks:
        due = t["due_date"] or "—"
        flag = " ⚠逾期" if t["due_date"] and t["due_date"] < today else ""
        proj = " [{}]".format(t["project"]) if t["project"] else ""
        print("#{:<4} {} 优先级:{} 截止:{}{}  {}{}".format(
            t["id"], STATUS_CN[t["status"]], PRIORITY_CN[t["priority"]], due, flag, t["title"], proj))


def make_agent(cfg, store: Store) -> Agent:
    if not cfg.api_key and not cfg.is_local_llm:
        print("未配置 LLM_API_KEY。请复制 .env.example 为 .env 并填写。", file=sys.stderr)
        sys.exit(1)
    llm = LLMClient(cfg.api_key, cfg.base_url, cfg.model)
    return Agent(llm, store, cfg=cfg,
                 on_tool=lambda name, args: print("  · 调用 {} {}".format(name, args)))


def generate_report(agent: Agent, cfg) -> None:
    text, path = agent.write_daily_report(cfg.reports_dir)
    print(text)
    print("\n已保存到 {}".format(path))


def chat_loop(agent: Agent, store: Store, cfg) -> None:
    print("私人工作助理已启动（模型: {}）".format(cfg.model))
    print(HELP)
    while True:
        try:
            text = input("\n你> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见。")
            return
        if not text:
            continue
        if text in ("/quit", "/exit", "/q"):
            print("再见。")
            return
        if text == "/help":
            print(HELP)
            continue
        if text == "/tasks":
            print_tasks(store)
            continue
        if text == "/reset":
            agent.reset()
            print("对话已清空（任务、笔记、记忆仍保留）。")
            continue
        try:
            if text == "/report":
                generate_report(agent, cfg)
            else:
                print("\n助理> " + agent.ask(text))
        except LLMError as e:
            print("调用大模型失败：{}".format(e))


def main() -> None:
    parser = argparse.ArgumentParser(prog="agent", description="私人 AI 工作助理")
    parser.add_argument("command", nargs="?", default="chat",
                        choices=["chat", "tasks", "report", "web", "install", "uninstall", "status"])
    parser.add_argument("--port", type=int, default=8765, help="网页界面端口")
    args = parser.parse_args()

    cfg = load_config()
    if args.command == "web":
        from .server import serve
        serve(cfg, args.port)
        return
    if args.command in ("install", "uninstall", "status"):
        if sys.platform == "win32":
            from . import service_windows as service
        else:
            from . import service
        if args.command == "install":
            service.install(cfg, args.port)
        elif args.command == "uninstall":
            service.uninstall()
        else:
            service.status(args.port)
        return
    store = Store(cfg.db_path)
    try:
        if args.command == "tasks":
            print_tasks(store)
            return
        agent = make_agent(cfg, store)
        if args.command == "report":
            try:
                generate_report(agent, cfg)
            except LLMError as e:
                print("调用大模型失败：{}".format(e), file=sys.stderr)
                sys.exit(1)
        else:
            from .reminders import ReminderScheduler
            scheduler = ReminderScheduler(cfg)
            scheduler.start()  # 对话期间也会按时提醒
            chat_loop(agent, store, cfg)
            scheduler.stop()
    finally:
        store.close()


if __name__ == "__main__":
    main()
