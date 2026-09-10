"""Тесты на фикстурах (без сети) + проверка клиента Telegram на локальном фейке.

Запуск:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import scraper, telegram  # noqa: E402
from src.config import Config  # noqa: E402
from src.formatter import md_to_tg_html, paginate, render_blog, render_book, split_html  # noqa: E402
from src.state import State  # noqa: E402

FIX = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def test_cfg(**kw) -> Config:
    cfg = Config.load("/nonexistent.env")
    cfg.parse_mode = kw.get("parse_mode", "HTML")
    for key, value in kw.items():
        setattr(cfg, key, value)
    return cfg


class TestListing(unittest.TestCase):
    def test_blog_listing(self):
        items = scraper.parse_listing(fixture("blog_listing.html"), "blog", test_cfg())
        self.assertEqual(len(items), 3)
        self.assertEqual(items[0].path, "/ru/blog/innovations-1")
        self.assertEqual(items[0].title, "Нововведения. Реально.")
        self.assertIn("Кратко про то что нового", items[0].summary)
        self.assertEqual(items[1].title, "Об состоянии проекта")
        self.assertTrue(items[0].url.startswith("https://loxa-rpg.vercel.app/"))

    def test_book_listing(self):
        items = scraper.parse_listing(fixture("book_listing.html"), "book", test_cfg())
        self.assertEqual(len(items), 3)
        self.assertEqual(items[0].chapter_no, "Глава 01")
        self.assertEqual(items[0].title, "Братья по оружию")
        self.assertEqual(items[2].chapter_no, "Глава 03")
        self.assertNotIn("Глава", items[2].title)

    def test_sitemap(self):
        blog, book = scraper.parse_sitemap(fixture("sitemap.xml"), test_cfg())
        paths = {i.path for i in blog} | {i.path for i in book}
        self.assertIn("/ru/blog/pidot", paths)
        self.assertIn("/ru/book/panther-history", paths)
        # англоязычная локаль не должна попадать в выборку
        self.assertFalse(any(p.startswith("/en/") for p in paths))


class TestArticle(unittest.TestCase):
    def test_blog_article(self):
        art = scraper.parse_article(fixture("blog_article.html"), "blog", test_cfg(),
                                    url="https://loxa-rpg.vercel.app/ru/blog/innovations-1")
        self.assertEqual(art.title, "Нововведения. Реально.")
        self.assertIn("10 сентября 2026", art.date_raw)
        self.assertIn("мин чтения", art.reading_time)
        self.assertIn("# На каком этапе сейчас проект?", art.markdown)
        self.assertIn("**почти**", art.markdown)
        self.assertNotIn("<p", art.markdown)
        self.assertNotIn("zmd", art.markdown)

    def test_book_article(self):
        art = scraper.parse_article(fixture("book_article.html"), "book", test_cfg(),
                                    url="https://loxa-rpg.vercel.app/ru/book/panther-history")
        self.assertEqual(art.title, "Тень Пантеры")
        self.assertEqual(art.subtitle, "Глава 02")
        self.assertTrue(art.markdown.startswith("## Глава 1.1"))
        self.assertNotIn("Ко всем главам", art.markdown)

    def test_slug(self):
        art = scraper.parse_article(fixture("book_article.html"), "book", test_cfg(),
                                    url="https://loxa-rpg.vercel.app/ru/book/panther-history")
        self.assertEqual(art.slug, "panther-history")


class TestHtmlToMarkdown(unittest.TestCase):
    def test_structures(self):
        from src.htmlmd import html_to_markdown
        html = (
            "<div class='zmd'>"
            "<h2>Заголовок</h2><p>Текст с <strong>жирным</strong> и <em>курсивом</em>.</p>"
            "<ul><li>Первый</li><li>Второй<ul><li>Вложенный</li></ul></li></ul>"
            "<ol><li>Раз</li><li>Два</li></ol>"
            "<blockquote><p>Цитата</p></blockquote>"
            "<pre><code>print(1)</code></pre>"
            "<p><a href='/ru/blog'>ссылка</a></p>"
            "<p><img src='/img/a.png' alt='картинка'></p>"
            "<hr/>"
            "</div>"
        )
        md = html_to_markdown(html, base_url="https://loxa-rpg.vercel.app")
        self.assertIn("## Заголовок", md)
        self.assertIn("**жирным**", md)
        self.assertIn("*курсивом*", md)
        self.assertIn("- Первый", md)
        self.assertIn("  - Вложенный", md)
        self.assertIn("1. Раз", md)
        self.assertIn("> Цитата", md)
        self.assertIn("```\nprint(1)\n```", md)
        self.assertIn("[ссылка](https://loxa-rpg.vercel.app/ru/blog)", md)
        self.assertIn("![картинка](https://loxa-rpg.vercel.app/img/a.png)", md)
        self.assertIn("---", md)


class TestFormatter(unittest.TestCase):
    def test_md_to_html(self):
        md = "# Заголовок\n\nТекст **жирный** и `код` и [ссылка](https://ya.ru)\n\n- пункт\n\n> цитата\n"
        html = md_to_tg_html(md)
        self.assertIn("<b>Заголовок</b>", html)
        self.assertIn("<b>жирный</b>", html)
        self.assertIn("<code>код</code>", html)
        self.assertIn('<a href="https://ya.ru">ссылка</a>', html)
        self.assertIn("• пункт", html)
        self.assertIn("<blockquote>цитата</blockquote>", html)

    def test_escaping(self):
        html = md_to_tg_html("5 < 6 & 7 > 4 <script>alert(1)</script>")
        self.assertIn("5 &lt; 6 &amp; 7 &gt; 4", html)
        self.assertNotIn("<script>", html)

    def test_split_keeps_tags_balanced(self):
        text = "<b>Жирный</b> " + ("слово " * 900)
        parts = split_html(text, limit=600)
        self.assertGreater(len(parts), 3)
        for part in parts:
            self.assertLessEqual(len(part), 600)
            opens = len(re.findall(r"<b>", part))
            closes = len(re.findall(r"</b>", part))
            self.assertEqual(opens, closes, "теги <b> должны быть закрыты в каждой части")

    def test_split_no_broken_link(self):
        text = '<a href="https://loxa-rpg.vercel.app/ru/blog/very-long-slug">' + "т" * 200 + "</a>"
        parts = split_html(text, limit=120)
        joined = "".join(parts)
        self.assertEqual(joined.count('<a href='), joined.count("</a>"))
        for part in parts:
            self.assertNotIn("<a", part.replace("<a href", "", 1)) if False else None
            if "<a href" in part:
                self.assertIn("</a>", part)

    def test_paginate_leftover(self):
        text = "<b>Заголовок</b>\n\n" + ("Абзац текста. " * 300)
        parts, leftover = paginate(text, limit=900, max_parts=2)
        self.assertEqual(len(parts), 2)
        self.assertTrue(parts[0].startswith("<i>(1/2)</i>"))
        self.assertTrue(parts[1].startswith("<i>(2/2)</i>"))
        self.assertNotEqual(leftover, "")

    def test_render_blog_and_book(self):
        cfg = test_cfg()
        art = scraper.parse_article(fixture("blog_article.html"), "blog", cfg,
                                    url="https://loxa-rpg.vercel.app/ru/blog/innovations-1")
        art.item = scraper.parse_listing(fixture("blog_listing.html"), "blog", cfg)[0]
        msg = render_blog(art, cfg)
        self.assertIn("Новая статья в блоге", msg)
        self.assertIn("<b>Нововведения. Реально.</b>", msg)
        self.assertIn("Открыть на сайте", msg)

        ch = scraper.parse_article(fixture("book_article.html"), "book", cfg,
                                   url="https://loxa-rpg.vercel.app/ru/book/panther-history")
        ch.item = scraper.parse_listing(fixture("book_listing.html"), "book", cfg)[1]
        book_msg = render_book(ch, cfg)
        self.assertIn("Вышла новая глава книги!", book_msg)
        self.assertIn("<b>Глава 02 — Тень Пантеры</b>", book_msg)
        self.assertIn("<i>История жизни Пантеры.</i>", book_msg)
        self.assertNotIn("<i>_", book_msg)
        self.assertIn("Читать главу", book_msg)


class TestState(unittest.TestCase):
    def test_baseline_and_new(self):
        cfg = test_cfg()
        items = scraper.parse_listing(fixture("blog_listing.html"), "blog", cfg)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "state.json")
            state = State.load(path)
            self.assertEqual(state.baseline("blog", items), 3)
            state.save()
            reloaded = State.load(path)
            self.assertTrue(reloaded.is_known("blog", items[0].path))
            self.assertEqual(reloaded.baseline("blog", items), 0)

            new_items = items[1:]
            new_items.insert(0, items[0])  # имитируем заново появившуюся запись
            fresh = [i for i in new_items if not reloaded.is_known("blog", i.path)]
            self.assertEqual(fresh, [])

    def test_corrupted_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "state.json")
            Path(path).write_text("{это не json", encoding="utf-8")
            state = State.load(path)
            self.assertEqual(state.known_paths("blog"), set())


class _FakeTelegram(BaseHTTPRequestHandler):
    calls: list[dict] = []
    fail_first = 0

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8", "replace")
        _FakeTelegram.calls.append({"path": self.path, "body": body})
        if _FakeTelegram.fail_first > 0:
            _FakeTelegram.fail_first -= 1
            payload = json.dumps({"ok": False, "description": "can't parse entities: bad"}).encode()
            self.send_response(400)
        else:
            payload = json.dumps({"ok": True, "result": {"message_id": 1}}).encode()
            self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):  # заглушаем шум в тестах
        return


class TestTelegramClient(unittest.TestCase):
    def setUp(self):
        _FakeTelegram.calls = []
        _FakeTelegram.fail_first = 0
        self.server = HTTPServer(("127.0.0.1", 0), _FakeTelegram)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.old_root = telegram.API_ROOT
        telegram.API_ROOT = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        telegram.API_ROOT = self.old_root
        self.server.shutdown()
        self.server.server_close()

    def test_send_message(self):
        client = telegram.TelegramClient("123:ABC", max_retries=1)
        res = client.send_rich("@loxa_channel", "<b>Привет</b>", parse_mode="HTML")
        self.assertTrue(res.ok)
        self.assertEqual(res.messages, 1)
        body = _FakeTelegram.calls[-1]["body"]
        self.assertIn("chat_id=%40loxa_channel", body)
        self.assertIn("parse_mode=HTML", body)

    def test_fallback_to_plain_text(self):
        _FakeTelegram.fail_first = 1
        client = telegram.TelegramClient("123:ABC", max_retries=1)
        res = client.send_rich("@loxa_channel", "<b>Привет</b>", parse_mode="HTML")
        self.assertTrue(res.ok)
        self.assertTrue(res.fallback_plain)
        self.assertNotIn("parse_mode=HTML", _FakeTelegram.calls[-1]["body"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
