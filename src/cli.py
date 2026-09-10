"""CLI: python -m src <команда>"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from . import notifier, scraper
from .config import Config
from .formatter import render_blog, render_book
from .state import State
from .telegram import TelegramClient, TelegramError

log = logging.getLogger("loxa")


def setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )


def set_output(name: str, value) -> None:
    """Записывает значение в $GITHUB_OUTPUT (для GitHub Actions)."""
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"{name}={value}\n")
    except OSError:
        pass


# --------------------------------------------------------------------- #
def cmd_check(args, cfg: Config) -> int:
    cfg.dry_run = args.dry_run
    cfg.out_dir = args.out_dir
    cfg.verbose = args.verbose

    report = notifier.run(
        cfg,
        force=args.force,
        announce_baseline=args.announce_baseline,
        only=args.only,
        limit=args.limit,
    )
    print("\n=== ИТОГ ===")
    print(report.summary_line())
    if report.baselined and not report.new_blog and not report.new_book:
        print(f"(первый запуск: запомнено {report.baselined} существующих материалов)")
    if report.errors:
        for err in report.errors:
            print("Ошибка:", err)

    set_output("new_blog", json.dumps(report.new_blog, ensure_ascii=False))
    set_output("new_book", json.dumps(report.new_book, ensure_ascii=False))
    set_output("sent", report.sent_messages)
    set_output("errors", json.dumps(report.errors, ensure_ascii=False))
    return 1 if report.errors and not (report.sent_messages or cfg.dry_run) else 0


def cmd_test(args, cfg: Config) -> int:
    print(f"Сайт: {cfg.site_base_url}")
    if not cfg.telegram_bot_token:
        print("❌ TELEGRAM_BOT_TOKEN не задан (проверьте .env)")
        return 1
    client = TelegramClient(cfg.telegram_bot_token, timeout=cfg.http_timeout)
    try:
        me = client.get_me()
    except TelegramError as exc:
        print(f"❌ Неверный токен или нет связи: {exc}")
        return 1
    print(f"✅ Бот: @{me.get('username')} ({me.get('first_name')})")

    if not cfg.telegram_chat_ids:
        print("❌ TELEGRAM_CHAT_ID не задан — покажу список чатов не могу, добавьте бота в канал")
        return 1

    text = ("✅ <b>LOXA Notifier подключён</b>\n\n"
            "Бот будет присылать сюда новые статьи блога и главы книги.\n"
            f"Источник: {cfg.blog_url}")
    ok = 0
    for chat_id in cfg.telegram_chat_ids:
        res = client.send_rich(chat_id, text, parse_mode=cfg.parse_mode, disable_preview=True)
        if res.ok:
            print(f"✅ Тестовое сообщение отправлено в {chat_id}")
            ok += 1
        else:
            print(f"❌ {chat_id}: {res.error}")
            print("   Подсказка: бот должен быть админом канала (право «Публикация сообщений»).")
    return 0 if ok else 1


def cmd_preview(args, cfg: Config) -> int:
    """Показывает, как будет выглядеть сообщение (без отправки)."""
    target = args.target or ""
    session = None
    found = notifier.collect(cfg, session)

    kinds = [args.kind] if args.kind else ["blog", "book"]
    chosen, kind = None, kinds[0]
    for k in kinds:
        for item in found[k]:
            if target and (target == item.path or target in item.url or item.path.endswith("/" + target)):
                chosen, kind = item, k
                break
        if chosen:
            break
    if chosen is None and target:
        print(f"Не нашёл материал «{target}». Доступные: " +
              ", ".join(i.path.rsplit('/', 1)[-1] for k in kinds for i in found[k]))
        return 1
    if chosen is None:
        kind = kinds[0]
        chosen = found[kind][0] if found[kind] else None
    if chosen is None:
        print(f"Не нашёл материалов в разделе {kind}")
        return 1

    art = scraper.load_article(chosen, cfg, session)
    if kind == "blog":
        if args.no_body:
            text = render_blog(art, cfg, include_body=False)
        else:
            text = render_blog(art, cfg, body=art.markdown, template=cfg.post_template_blog or None)
    else:
        text = render_book(art, cfg, template=cfg.post_template_book or None)

    print(f"--- {chosen.path} ({len(text)} символов) ---\n")
    print(text)
    print("\n--- markdown статьи (первые 400 символов) ---\n")
    print(art.markdown[:400])
    return 0


def cmd_seed(args, cfg: Config) -> int:
    """Запоминает всё существующее как «уже известное» (без отправки)."""
    state = State.load(cfg.state_file)
    found = notifier.collect(cfg)
    total = 0
    for kind in ("blog", "book"):
        total += state.baseline(kind, found[kind], notified=False)
    state.save(dry_run=False)
    print(f"✅ В state.json добавлено {total} материалов — они не будут отправлены.")
    print("Теперь будущие публикации придут в канал.")
    return 0


def cmd_export(args, cfg: Config) -> int:
    count = notifier.export_all(cfg, out_dir=args.out_dir or "export")
    print(f"✅ Выгружено {count} материалов в {args.out_dir or 'export'}/")
    return 0


def cmd_discover(args, cfg: Config) -> int:
    found = notifier.collect(cfg)
    for kind in ("blog", "book"):
        print(f"\n=== {kind} ({len(found[kind])}) ===")
        for item in found[kind]:
            print(f"  {item.order:>2}. {item.path}\n      title={item.title!r} chapter={item.chapter_no!r} "
                  f"date={item.date_raw!r} src={item.source}")
    return 0


# --------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="loxa-notifier",
        description="Отправка новых статей блога и глав книги LOXA RPG в Telegram-канал.",
    )
    p.add_argument("--env-file", default=".env", help="путь к .env (по умолчанию .env)")
    p.add_argument("--state-file", default=None, help="путь к state.json")
    sub = p.add_subparsers(dest="command")

    # те же ключи доступны и после подкоманды (python bot.py check --state-file x.json)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--env-file", default=argparse.SUPPRESS)
    common.add_argument("--state-file", default=argparse.SUPPRESS)

    c = sub.add_parser("check", parents=[common], help="проверить сайт и отправить новое (основная команда)")
    c.add_argument("--dry-run", action="store_true", help="ничего не отправлять, только показать")
    c.add_argument("--force", action="store_true", help="игнорировать state.json (переслать всё найденное)")
    c.add_argument("--announce-baseline", action="store_true",
                   help="при первом запуске отправить и уже существующие материалы")
    c.add_argument("--only", choices=["blog", "book"], help="проверить только один раздел")
    c.add_argument("--limit", type=int, help="максимум материалов за запуск")
    c.add_argument("--out-dir", help="сохранить готовые сообщения в папку")

    t = sub.add_parser("test", help="проверить токен и отправить тестовое сообщение в канал", parents=[common])

    pr = sub.add_parser("preview", help="показать сообщение без отправки", parents=[common])
    pr.add_argument("target", nargs="?", help="slug или URL материала (по умолчанию — самый свежий)")
    pr.add_argument("--kind", choices=["blog", "book"])
    pr.add_argument("--no-body", action="store_true", help="для блога — только заголовок и ссылка")

    sub.add_parser("seed", help="запомнить текущее содержимое сайта, не отправляя его", parents=[common])
    e = sub.add_parser("export", help="выгрузить весь контент сайта в Markdown", parents=[common])
    e.add_argument("--out-dir", default="export")
    sub.add_parser("discover", help="показать, что бот видит на сайте", parents=[common])
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command or "check"

    if command == "check":
        # значения по умолчанию для check, если запущено без подкоманды
        for attr, default in (("dry_run", False), ("force", False), ("announce_baseline", False),
                              ("only", None), ("limit", None), ("out_dir", None), ("verbose", False)):
            if not hasattr(args, attr):
                setattr(args, attr, default)

    cfg = Config.load(getattr(args, "env_file", ".env") or ".env")
    if getattr(args, "state_file", None):
        cfg.state_file = args.state_file
    cfg.verbose = bool(getattr(args, "verbose", False))
    setup_logging(cfg.verbose or os.environ.get("VERBOSE") == "1")

    handlers = {
        "check": cmd_check,
        "test": cmd_test,
        "preview": cmd_preview,
        "seed": cmd_seed,
        "export": cmd_export,
        "discover": cmd_discover,
    }
    try:
        return handlers[command](args, cfg)
    except KeyboardInterrupt:
        print("Прервано пользователем")
        return 130
    except Exception as exc:  # noqa: BLE001
        log.error("Фатальная ошибка: %s", exc, exc_info=cfg.verbose)
        print(f"\n❌ Ошибка: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
