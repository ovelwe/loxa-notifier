#!/usr/bin/env python3
"""Обновляет HTML-фикстуры в tests/fixtures с живого сайта.

Нужен, когда сайт немного поменял разметку: сначала обновляем фикстуры,
потом правим парсер и смотрим, что тесты снова зелёные.

    python tools/refresh_fixtures.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import scraper  # noqa: E402
from src.config import Config  # noqa: E402

FIX = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
TARGETS = {
    "blog_listing.html": lambda cfg: cfg.blog_url,
    "book_listing.html": lambda cfg: cfg.book_url,
    "sitemap.xml": lambda cfg: cfg.sitemap_url,
}


def main() -> int:
    cfg = Config.load()
    FIX.mkdir(parents=True, exist_ok=True)

    for name, url_fn in TARGETS.items():
        url = url_fn(cfg)
        html = scraper.fetch(url, cfg)
        (FIX / name).write_text(html, encoding="utf-8")
        print(f"обновлено {name} <- {url}")

    # по одной свежей статье и главе
    for kind, name in (("blog", "blog_article.html"), ("book", "book_article.html")):
        items = scraper.load_listing(kind, cfg)
        if not items:
            print(f"! {kind}: в листинге пусто, статью не обновил")
            continue
        html = scraper.fetch(items[0].url, cfg)
        (FIX / name).write_text(html, encoding="utf-8")
        print(f"обновлено {name} <- {items[0].url}")

    print("\nТеперь запустите: python -m unittest discover -s tests -v")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
