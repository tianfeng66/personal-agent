"""配置加载：从环境变量或项目根目录的 .env 文件读取。"""

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path) -> None:
    """极简 .env 解析器（KEY=VALUE），不覆盖已存在的环境变量。"""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


@dataclass
class Config:
    api_key: str
    base_url: str
    model: str
    data_dir: Path
    # 邮箱
    email_address: str = ""
    email_password: str = ""
    imap_host: str = ""
    imap_port: int = 993
    smtp_host: str = ""
    smtp_port: int = 465
    # 微信推送
    serverchan_key: str = ""
    wecom_webhook: str = ""
    # 搜索
    tavily_key: str = ""

    @property
    def db_path(self) -> Path:
        return self.data_dir / "agent.db"

    @property
    def reports_dir(self) -> Path:
        return self.data_dir / "reports"

    @property
    def is_local_llm(self) -> bool:
        return any(h in self.base_url for h in ("localhost", "127.0.0.1"))

    @property
    def email_ready(self) -> bool:
        return all((self.email_address, self.email_password, self.imap_host, self.smtp_host))

    @property
    def wechat_ready(self) -> bool:
        return bool(self.serverchan_key or self.wecom_webhook)


def load_config() -> Config:
    load_dotenv(Path.cwd() / ".env")
    load_dotenv(PROJECT_ROOT / ".env")
    env = os.environ.get
    data_dir = Path(env("AGENT_DATA_DIR", str(PROJECT_ROOT / "data"))).expanduser()
    return Config(
        api_key=env("LLM_API_KEY", ""),
        base_url=env("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
        model=env("LLM_MODEL", "gpt-4o-mini"),
        data_dir=data_dir,
        email_address=env("EMAIL_ADDRESS", ""),
        email_password=env("EMAIL_PASSWORD", ""),
        imap_host=env("EMAIL_IMAP_HOST", ""),
        imap_port=_int("EMAIL_IMAP_PORT", 993),
        smtp_host=env("EMAIL_SMTP_HOST", ""),
        smtp_port=_int("EMAIL_SMTP_PORT", 465),
        serverchan_key=env("SERVERCHAN_KEY", ""),
        wecom_webhook=env("WECOM_WEBHOOK_URL", ""),
        tavily_key=env("TAVILY_API_KEY", ""),
    )
