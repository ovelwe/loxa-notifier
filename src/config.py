"""Конфигурация: читается из переменных окружения и/или файла .env.

Ничего обязательного кроме TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID
(и то — только когда бот реально отправляет сообщения).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULTS = {
    # --- Что отслеживаем -------------------------------------------------
    "SITE_BASE_URL": "https://loxa-rpg.vercel.app",
    "LOCALE": "ru",                    # ru | en — из какой локали брать контент
    "WATCH_BLOG": "true",
    "WATCH_BOOK": "true",

    # --- Telegram --------------------------------------------------------
    "TELEGRAM_BOT_TOKEN": "",
    "TELEGRAM_CHAT_ID": "",            # "@my_channel" или "-1001234567890", можно списком через запятую
    "PARSE_MODE": "HTML",              # HTML (надёжно) | MarkdownV2 (экзотика)
    "MESSAGE_MAX_CHARS": "3800",       # с запасом от лимита Telegram в 4096
    "MAX_MESSAGE_PARTS": "4",          # больше — остаток уйдёт .md-файлом
    "SEND_MARKDOWN_FILE": "true",      # прикладывать .md-файл, если статья не влезла
    "DISABLE_LINK_PREVIEW_BLOG": "true",
    "DISABLE_LINK_PREVIEW_BOOK": "false",
    "POST_TEMPLATE_BLOG": "",          # можно переопределить шапку/подвал в env
    "POST_TEMPLATE_BOOK": "",

    # --- Прочее ----------------------------------------------------------
    "STATE_FILE": "state.json",
    "HTTP_TIMEOUT": "25",
    "HTTP_RETRIES": "3",
    "USER_AGENT": "loxa-notifier/1.0 (+https://loxa-rpg.vercel.app)",
    "TARGET_LOCALE_NOTES": "",
}

ENV_FILE = ".env"


def _load_dotenv(path: str = ENV_FILE) -> None:
    """Минимальный загрузчик .env — без внешних зависимостей."""
    p = Path(path)
    if not p.is_file():
        return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _as_bool(value: str | bool) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y", "да"}


def _as_int(value: str, fallback: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return fallback


@dataclass
class Config:
    site_base_url: str = DEFAULTS["SITE_BASE_URL"]
    locale: str = DEFAULTS["LOCALE"]
    watch_blog: bool = True
    watch_book: bool = True

    telegram_bot_token: str = ""
    telegram_chat_ids: list[str] = field(default_factory=list)
    parse_mode: str = "HTML"
    message_max_chars: int = 3800
    max_message_parts: int = 4
    send_markdown_file: bool = True
    disable_link_preview_blog: bool = True
    disable_link_preview_book: bool = False

    post_template_blog: str = ""
    post_template_book: str = ""

    state_file: str = "state.json"
    http_timeout: int = 25
    http_retries: int = 3
    user_agent: str = DEFAULTS["USER_AGENT"]

    # не из env
    dry_run: bool = False
    verbose: bool = False
    out_dir: str | None = None

    # ------------------------------------------------------------------ #
    @classmethod
    def load(cls, env_file: str = ENV_FILE) -> "Config":
        _load_dotenv(env_file)

        def get(key: str) -> str:
            return os.environ.get(key, DEFAULTS.get(key, ""))

        chat_ids = [c.strip() for c in get("TELEGRAM_CHAT_ID").split(",") if c.strip()]
        return cls(
            site_base_url=get("SITE_BASE_URL").rstrip("/"),
            locale=(get("LOCALE") or "ru").strip().lower(),
            watch_blog=_as_bool(get("WATCH_BLOG")),
            watch_book=_as_bool(get("WATCH_BOOK")),
            telegram_bot_token=get("TELEGRAM_BOT_TOKEN").strip(),
            telegram_chat_ids=chat_ids,
            parse_mode=(get("PARSE_MODE") or "HTML").strip(),
            message_max_chars=min(_as_int(get("MESSAGE_MAX_CHARS"), 3800), 4096),
            max_message_parts=max(1, _as_int(get("MAX_MESSAGE_PARTS"), 4)),
            send_markdown_file=_as_bool(get("SEND_MARKDOWN_FILE")),
            disable_link_preview_blog=_as_bool(get("DISABLE_LINK_PREVIEW_BLOG")),
            disable_link_preview_book=_as_bool(get("DISABLE_LINK_PREVIEW_BOOK")),
            post_template_blog=get("POST_TEMPLATE_BLOG").replace("\\n", "\n"),
            post_template_book=get("POST_TEMPLATE_BOOK").replace("\\n", "\n"),
            state_file=get("STATE_FILE") or "state.json",
            http_timeout=_as_int(get("HTTP_TIMEOUT"), 25),
            http_retries=max(1, _as_int(get("HTTP_RETRIES"), 3)),
            user_agent=get("USER_AGENT"),
        )

    # ------------------------------------------------------------------ #
    @property
    def blog_url(self) -> str:
        return f"{self.site_base_url}/{self.locale}/blog"

    @property
    def book_url(self) -> str:
        return f"{self.site_base_url}/{self.locale}/book"

    @property
    def sitemap_url(self) -> str:
        return f"{self.site_base_url}/sitemap.xml"

    def abs_url(self, path: str) -> str:
        if path.startswith("http"):
            return path
        if not path.startswith("/"):
            path = "/" + path
        return self.site_base_url + path

    def validate_for_sending(self) -> list[str]:
        problems: list[str] = []
        if not self.telegram_bot_token:
            problems.append("TELEGRAM_BOT_TOKEN не задан")
        if not self.telegram_chat_ids:
            problems.append("TELEGRAM_CHAT_ID не задан")
        return problems
