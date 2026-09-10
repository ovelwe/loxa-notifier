"""Основной сценарий: проверить сайт -> найти новое -> отправить в Telegram."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import requests

from . import scraper
from .config import Config
from .formatter import paginate, render_blog, render_book
from .models import Article, ListedItem
from .state import State
from .telegram import TelegramClient, TelegramError

log = logging.getLogger("loxa.notifier")
SLUG_SAFE = re.compile(r"[^a-z0-9\-_]+")


@dataclass
class Report:
    started_at: str = ""
    new_blog: list[str] = field(default_factory=list)
    new_book: list[str] = field(default_factory=list)
    sent_messages: int = 0
    sent_files: int = 0
    errors: list[str] = field(default_factory=list)
    dry_run: bool = False
    baselined: int = 0

    def to_dict(self) -> dict:
        return {
            "started_at": self.started_at,
            "dry_run": self.dry_run,
            "new_blog": self.new_blog,
            "new_book": self.new_book,
            "sent_messages": self.sent_messages,
            "sent_files": self.sent_files,
            "baselined": self.baselined,
            "errors": self.errors,
        }

    def summary_line(self) -> str:
        if self.errors and not (self.new_blog or self.new_book):
            return "⚠️ Ошибки: " + "; ".join(self.errors)
        if not self.new_blog and not self.new_book:
            return "Новых материалов нет."
        parts = []
        if self.new_blog:
            parts.append(f"статей: {len(self.new_blog)}")
        if self.new_book:
            parts.append(f"глав: {len(self.new_book)}")
        return "Отправлено — " + ", ".join(parts) + f" (сообщений: {self.sent_messages})"


def _safe_slug(text: str) -> str:
    slug = SLUG_SAFE.sub("-", text.lower()).strip("-")
    return slug[:60] or "material"


# --------------------------------------------------------------------- #
def collect(cfg: Config, session: requests.Session | None = None) -> dict[str, list[ListedItem]]:
    """Возвращает найденные материалы по разделам, от новых к старым."""
    result: dict[str, list[ListedItem]] = {"blog": [], "book": []}
    wanted = []
    if cfg.watch_blog:
        wanted.append("blog")
    if cfg.watch_book:
        wanted.append("book")

    for kind in wanted:
        try:
            items = scraper.load_listing(kind, cfg, session)
        except Exception as exc:  # noqa: BLE001
            log.error("Листинг %s недоступен: %s", kind, exc)
            items = []
        result[kind] = items

    # Добираем из sitemap то, чего нет в листинге (напр. сняли с главной).
    sm_blog, sm_book = scraper.load_sitemap(cfg, session)
    for kind, items in (("blog", sm_blog), ("book", sm_book)):
        if kind not in wanted:
            continue
        known = {i.path for i in result[kind]}
        for item in items:
            if item.path not in known:
                result[kind].append(item)
    return result


def _fill_from_page(item: ListedItem, cfg: Config, session) -> Article:
    """Догружает страницу, если данных из листинга мало."""
    art = scraper.load_article(item, cfg, session)
    if not art.title:
        art.title = item.title
    if not art.subtitle:
        art.subtitle = item.chapter_no
    return art


def build_messages(items: list[ListedItem], cfg: Config, session,
                   kind: str, force_body_for_all: bool = False) -> list[tuple[ListedItem, Article, str, dict]]:
    """Готовит [(item, article, текст сообщения, опции)] для новых материалов."""
    prepared = []
    for index, item in enumerate(items):
        try:
            if kind == "blog":
                art = _fill_from_page(item, cfg, session)
                include_body = force_body_for_all or index == 0
                body = art.markdown if include_body else None
                if include_body and not art.markdown.strip():
                    log.warning("У %s пустое тело — отправляю только заголовок", item.path)
                text = render_blog(art, cfg, include_body=include_body, body=body,
                                   template=cfg.post_template_blog or None)
            else:
                # Глава книги: нужны название, номер и (по желанию) дата.
                # Страница главы лёгкая, поэтому берём данные оттуда;
                # если запрос упал — обходимся данными из листинга.
                try:
                    art = _fill_from_page(item, cfg, session)
                except Exception as exc:  # noqa: BLE001
                    log.warning("Страница главы %s недоступна (%s) — беру данные из листинга",
                                item.url, exc)
                    art = Article(kind="book", url=item.url, title=item.title,
                                  subtitle=item.chapter_no, item=item)
                text = render_book(art, cfg, template=cfg.post_template_book or None)
            preview = not (cfg.disable_link_preview_blog if kind == "blog" else cfg.disable_link_preview_book)
            prepared.append((item, art, text, {"disable_preview": preview, "kind": kind}))
        except Exception as exc:  # noqa: BLE001
            log.error("Не удалось подготовить %s: %s", item.url, exc)
    return prepared


# --------------------------------------------------------------------- #
def run(cfg: Config, force: bool = False, announce_baseline: bool = False,
        only: str | None = None, limit: int | None = None) -> Report:
    report = Report(started_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                    dry_run=cfg.dry_run)
    session = requests.Session()
    state = State.load(cfg.state_file)

    kinds = ["blog", "book"] if not only else [only]
    found = collect(cfg, session)

    first_run = not any(state.known_paths(k) for k in ("blog", "book"))

    # --- 1) первый запуск: запоминаем всё, что уже есть, чтобы не спамить ---
    if first_run and not force:
        for kind in ("blog", "book"):
            if kind not in kinds:
                continue
            added = state.baseline(kind, found[kind], notified=announce_baseline)
            report.baselined += added
        state.set_last_run()
        state.save(cfg.dry_run)
        log.info("Первый запуск: базовый список из %s материалов сохранён (уведомления %s)",
                 report.baselined, "включены" if announce_baseline else "пропущены")
        if not announce_baseline:
            return report

    # --- 2) ищем новое ---
    planned: list[tuple[str, ListedItem]] = []
    for kind in kinds:
        new_items = [i for i in found[kind] if not state.is_known(kind, i.path)]
        if limit:
            new_items = new_items[:limit]
        for item in new_items:
            planned.append((kind, item))
        if kind == "blog":
            report.new_blog = [i.path for i in new_items]
        else:
            report.new_book = [i.path for i in new_items]

    if not planned:
        log.info("Новых материалов нет")
        state.set_last_run()
        state.save(cfg.dry_run)
        return report

    # При первом запуске с --announce-baseline в state уже есть пути,
    # но пометка notified=False — отправляем только неотправленные.
    planned = [(k, i) for k, i in planned if not state.is_notified(k, i.path)]

    # --- 3) отправка ---
    client: TelegramClient | None = None
    problems = cfg.validate_for_sending()
    if not cfg.dry_run:
        if problems:
            report.errors.extend(problems)
            raise RuntimeError("Не могу отправлять: " + "; ".join(problems))
        client = TelegramClient(cfg.telegram_bot_token, timeout=cfg.http_timeout)

    out_dir = Path(cfg.out_dir) if cfg.out_dir else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)

    for kind in ("blog", "book"):
        kind_items = [i for k, i in planned if k == kind]
        if not kind_items:
            continue
        for item, art, text, opts in build_messages(kind_items, cfg, session, kind):
            parts, leftover = paginate(text, limit=cfg.message_max_chars, max_parts=cfg.max_message_parts)
            for body in parts:
                if cfg.dry_run:
                    log.info("DRY-RUN %s: %s\n%s", kind, item.path, body[:1500])
                    report.sent_messages += 1
                else:
                    assert client is not None
                    for chat_id in cfg.telegram_chat_ids:
                        res = client.send_rich(chat_id, body, parse_mode=cfg.parse_mode,
                                               disable_preview=opts["disable_preview"])
                        if res.ok:
                            report.sent_messages += 1
                        else:
                            report.errors.append(f"{chat_id}: {res.error}")
                            log.error("Не отправлено в %s: %s", chat_id, res.error)

            if out_dir:
                (out_dir / f"{kind}-{_safe_slug(item.path.rsplit('/', 1)[-1])}.html").write_text(text, encoding="utf-8")

            if leftover and cfg.send_markdown_file and not cfg.dry_run and client is not None:
                fname = f"{kind}-{_safe_slug(art.title)}.md"
                content = f"# {art.title}\n\n{art.url}\n\n{art.markdown}\n".encode("utf-8")
                caption = f"<b>{art.title}</b>\nПолный текст во вложении (сообщение не влезло в Telegram)."
                for chat_id in cfg.telegram_chat_ids:
                    try:
                        client.send_document(chat_id, fname, content, caption=caption)
                        report.sent_files += 1
                    except TelegramError as exc:
                        report.errors.append(f"файл {fname}: {exc}")
                        log.error("Файл не отправлен: %s", exc)

            state.mark(kind, item, notified=True, dry_run=cfg.dry_run)

    state.set_last_run()
    state.save(cfg.dry_run)
    log.info(report.summary_line())
    return report


# --------------------------------------------------------------------- #
def export_all(cfg: Config, out_dir: str = "export") -> int:
    """Выгружает весь контент сайта в Markdown-файлы (бэкап/просмотр)."""
    session = requests.Session()
    found = collect(cfg, session)
    base = Path(out_dir)
    count = 0
    for kind in ("blog", "book"):
        for item in found[kind]:
            try:
                art = scraper.load_article(item, cfg, session)
            except Exception as exc:  # noqa: BLE001
                log.error("Пропуск %s: %s", item.url, exc)
                continue
            folder = base / kind
            folder.mkdir(parents=True, exist_ok=True)
            name = f"{count:02d}-{_safe_slug(item.path.rsplit('/', 1)[-1])}.md"
            front = [f"title: {art.title}", f"url: {art.url}", f"date: {art.date_raw}"]
            if art.subtitle:
                front.insert(1, f"subtitle: {art.subtitle}")
            (folder / name).write_text(
                "---\n" + "\n".join(front) + "\n---\n\n" + art.markdown + "\n", encoding="utf-8"
            )
            count += 1
            log.info("Выгружено: %s/%s", kind, name)
    return count
