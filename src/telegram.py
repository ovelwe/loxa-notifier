"""Минимальный клиент Telegram Bot API (без сторонних SDK)."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import requests

log = logging.getLogger("loxa.telegram")
API_ROOT = "https://api.telegram.org"


class TelegramError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, retry_after: int | None = None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


@dataclass
class SendResult:
    ok: bool
    chat_id: str
    messages: int = 0
    error: str = ""
    fallback_plain: bool = False


class TelegramClient:
    def __init__(self, token: str, timeout: int = 30, max_retries: int = 3):
        self.token = token
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = requests.Session()

    # ------------------------------------------------------------------ #
    def _call(self, method: str, payload: dict, files: dict | None = None) -> dict:
        url = f"{API_ROOT}/bot{self.token}/{method}"
        last: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self.session.post(
                    url,
                    data={k: v for k, v in payload.items() if v is not None},
                    files=files,
                    timeout=self.timeout,
                )
                body = resp.json() if resp.content else {}
                if resp.status_code == 429:
                    retry_after = int(body.get("parameters", {}).get("retry_after", 5))
                    log.warning("Telegram 429: ждём %s c", retry_after)
                    time.sleep(retry_after + 1)
                    continue
                if resp.status_code >= 500:
                    raise TelegramError(f"Telegram {resp.status_code}: {body}", resp.status_code)
                if not body.get("ok", False):
                    desc = body.get("description", resp.text[:300])
                    raise TelegramError(f"Telegram API: {desc}", resp.status_code)
                return body.get("result", {})
            except TelegramError as exc:
                if exc.status and exc.status >= 500:
                    last = exc
                    time.sleep(2 * attempt)
                    continue
                raise
            except Exception as exc:  # noqa: BLE001
                last = exc
                log.warning("Ошибка запроса к Telegram (%s/%s): %s", attempt, self.max_retries, exc)
                time.sleep(2 * attempt)
        raise TelegramError(f"Telegram недоступен: {last}")

    # ------------------------------------------------------------------ #
    def get_me(self) -> dict:
        return self._call("getMe", {})

    def send_message(self, chat_id: str, text: str, parse_mode: str | None = "HTML",
                    disable_preview: bool = True) -> dict:
        return self._call("sendMessage", {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode or "",
            "disable_web_page_preview": "true" if disable_preview else "false",
            "disable_notification": "false",
        })

    def send_document(self, chat_id: str, filename: str, content: bytes,
                      caption: str = "", parse_mode: str = "HTML") -> dict:
        files = {"document": (filename, content, "text/markdown; charset=utf-8")}
        return self._call("sendDocument", {
            "chat_id": chat_id,
            "caption": caption[:1024] if caption else None,
            "parse_mode": parse_mode if caption else None,
            "disable_notification": "false",
        }, files=files)

    # ------------------------------------------------------------------ #
    def send_rich(self, chat_id: str, text: str, parse_mode: str = "HTML",
                  disable_preview: bool = True) -> SendResult:
        """Отправляет одно сообщение, при ошибке разметки повторяет без parse_mode."""
        try:
            self.send_message(chat_id, text, parse_mode=parse_mode, disable_preview=disable_preview)
            return SendResult(True, chat_id, messages=1)
        except TelegramError as exc:
            msg = str(exc)
            entity_problem = any(k in msg for k in ("can't parse entities", "can't parse entity", "Unsupported start tag", "not enough rights"))
            if not entity_problem:
                return SendResult(False, chat_id, error=msg)
            log.warning("Разметка не принята (%s) — отправляю обычным текстом", msg)
            import re as _re
            plain = _re.sub(r"<[^>]+>", "", text)
            try:
                self.send_message(chat_id, plain, parse_mode=None, disable_preview=disable_preview)
                return SendResult(True, chat_id, messages=1, fallback_plain=True)
            except TelegramError as exc2:
                return SendResult(False, chat_id, error=str(exc2), fallback_plain=True)
