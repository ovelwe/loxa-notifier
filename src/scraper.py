"""Разбор loxa-rpg.vercel.app: листинги блога/книги, страницы материалов, sitemap.

Никаких сторонних API и RSS сайт не отдаёт, поэтому используется
аккуратный HTML-парсинг стабильных структурных признаков:
  * ссылки-карточки в /{locale}/blog и /{locale}/book
  * контент материала — контейнер .zmd (кастомный markdown-рендерер сайта)
  * sitemap.xml — как резервный источник списка URL
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup, Tag

from .config import Config
from .htmlmd import html_to_markdown
from .models import Article, ListedItem

log = logging.getLogger("loxa.scraper")

READ_TIME_RE = re.compile(r"(\d+)\s*мин", re.IGNORECASE)
CHAPTER_RE = re.compile(r"^Глава\s*([\d]+|[IVXLC]+)", re.IGNORECASE)


class FetchError(RuntimeError):
    pass


def fetch(url: str, cfg: Config, session: requests.Session | None = None) -> str:
    """GET с ретраями. Возвращает HTML."""
    sess = session or requests.Session()
    last_err: Exception | None = None
    for attempt in range(1, cfg.http_retries + 1):
        try:
            resp = sess.get(
                url,
                timeout=cfg.http_timeout,
                headers={
                    "User-Agent": cfg.user_agent,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": f"{cfg.locale},en;q=0.8",
                },
            )
            if resp.status_code == 404:
                raise FetchError(f"404 Not Found: {url}")
            resp.raise_for_status()
            return resp.text
        except FetchError:
            raise
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            log.warning("Попытка %s/%s не удалась для %s: %s", attempt, cfg.http_retries, url, exc)
    raise FetchError(f"Не удалось загрузить {url}: {last_err}")


# --------------------------------------------------------------------- #
# Листинги
# --------------------------------------------------------------------- #
def _text(node: Tag | None) -> str:
    return node.get_text(" ", strip=True) if node else ""


def _parse_card(anchor: Tag, kind: str, cfg: Config, order: int) -> ListedItem:
    path = anchor.get("href", "")
    url = cfg.abs_url(path)
    item = ListedItem(kind=kind, url=url, path=path if path.startswith("/") else "/" + path, order=order)

    time_el = anchor.find("time")
    if time_el is not None:
        item.date_raw = time_el.get_text(" ", strip=True)

    spans = [s for s in anchor.find_all("span")]
    summary, title, chapter = "", "", ""

    for span in spans:
        text = _text(span)
        if not text:
            continue
        classes = " ".join(span.get("class") or [])
        if CHAPTER_RE.match(text) and len(text) < 24:
            chapter = text
            continue
        if "text-xl" in classes:           # описание в карточке
            summary = text if not summary else f"{summary} {text}"
            continue
        if not title:
            title = text
        elif not summary:
            summary = text

    # Иногда <span> может отсутствовать — берём весь текст ссылки.
    if not title:
        title = _text(anchor)

    item.title = _clean_title(title)
    item.summary = _clean_summary(summary)
    item.chapter_no = chapter
    return item


def _clean_title(title: str) -> str:
    title = re.sub(r"\s+", " ", title or "").strip()
    # В листинге книг к заголовку иногда прилипает номер главы: "Глава03Братья по оружию"
    title = re.sub(r"^Глава\s*\d+\s*", "", title).strip()
    return title


def _clean_summary(summary: str) -> str:
    summary = re.sub(r"\s+", " ", summary or "").strip()
    # Убираем прилипшее "67" (счётчик просмотров/лайков) в начале и конце.
    summary = re.sub(r"\s*\d{1,4}\s*$", "", summary).strip()
    return summary


def parse_listing(html: str, kind: str, cfg: Config) -> list[ListedItem]:
    """Возвращает элементы листинга в порядке убывания новизны."""
    soup = BeautifulSoup(html, "html.parser")
    main = soup.find("main") or soup
    items: list[ListedItem] = []
    seen: set[str] = set()

    for anchor in main.find_all("a", href=True):
        href: str = anchor["href"]
        if kind not in href:
            continue
        # ссылка на сам листинг — пропускаем, нужны только материалы
        tail = href.rstrip("/").rsplit("/", 1)[-1]
        if tail in {"blog", "book"} or href.count("/") < 3:
            continue
        if href in seen:
            continue
        seen.add(href)
        items.append(_parse_card(anchor, kind, cfg, order=len(items)))

    log.info("Листинг %s: найдено %s материалов", kind, len(items))
    return items


def load_listing(kind: str, cfg: Config, session: requests.Session | None = None) -> list[ListedItem]:
    url = cfg.blog_url if kind == "blog" else cfg.book_url
    html = fetch(url, cfg, session)
    return parse_listing(html, kind, cfg)


# --------------------------------------------------------------------- #
# sitemap.xml — резервный источник
# --------------------------------------------------------------------- #
def parse_sitemap(xml_text: str, cfg: Config) -> tuple[list[ListedItem], list[ListedItem]]:
    blog: list[ListedItem] = []
    book: list[ListedItem] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        log.warning("sitemap.xml не разобран: %s", exc)
        return blog, book

    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    for loc_el in root.findall(".//sm:url", ns) or root.findall(".//url"):
        loc = loc_el.findtext("sm:loc", default="", namespaces=ns) or loc_el.findtext("loc", default="")
        if not loc:
            continue
        lastmod = loc_el.findtext("sm:lastmod", default="", namespaces=ns) or loc_el.findtext("lastmod", default="")
        parts = loc.rstrip("/").split("/")
        if len(parts) < 2:
            continue
        kind = parts[-2]
        if kind not in {"blog", "book"}:
            continue
        if not re.search(rf"/{cfg.locale}/", loc):
            continue
        item = ListedItem(kind=kind, url=loc, path="/" + loc.split("/", 3)[-1], date_raw=lastmod, source="sitemap")
        (blog if kind == "blog" else book).append(item)
    return blog, book


def load_sitemap(cfg: Config, session: requests.Session | None = None) -> tuple[list[ListedItem], list[ListedItem]]:
    try:
        return parse_sitemap(fetch(cfg.sitemap_url, cfg, session), cfg)
    except Exception as exc:  # noqa: BLE001
        log.warning("Не удалось прочитать sitemap: %s", exc)
        return [], []


# --------------------------------------------------------------------- #
# Страница материала
# --------------------------------------------------------------------- #
def extract_content_root(soup: BeautifulSoup) -> Tag | None:
    """Контейнер с телом материала."""
    root = soup.select_one(".zmd")
    if root:
        return root
    article = soup.find("article")
    if article:
        # отбрасываем шапку статьи (заголовок/дата уже разобраны отдельно)
        header = article.find("header")
        if header:
            header.extract()
        return article
    return soup.find("main")


def parse_article(html: str, kind: str, cfg: Config, url: str = "", item: ListedItem | None = None) -> Article:
    soup = BeautifulSoup(html, "html.parser")
    art = Article(kind=kind, url=url or (item.url if item else ""), item=item)

    article_tag = soup.find("article")
    header = article_tag.find("header") if article_tag else None

    h1 = (header.find("h1") if header else None) or soup.find("h1")
    art.title = _text(h1).strip()
    if not art.title and soup.title:
        art.title = re.split(r"\s+—\s+", soup.title.get_text(strip=True))[0].strip()

    if header:
        for span in header.find_all("span"):
            text = _text(span)
            if not text:
                continue
            if CHAPTER_RE.match(text):
                art.subtitle = text
                break
        time_el = header.find("time")
        if time_el is not None:
            art.date_raw = time_el.get_text(" ", strip=True)
        for span in header.find_all("span"):
            m = READ_TIME_RE.search(_text(span))
            if m:
                art.reading_time = _text(span)
                break
        # "Обновлено: <дата>" — сохраняем как дату, если даты не было
        if not art.date_raw:
            for span in header.find_all("span"):
                text = _text(span)
                if text.lower().startswith("обновлено"):
                    art.date_raw = text.split(":", 1)[-1].strip()
                    break

    if not art.subtitle and item:
        art.subtitle = item.chapter_no

    content = extract_content_root(soup)
    art.markdown = html_to_markdown(str(content), base_url=cfg.abs_url("/")) if content else ""

    if not art.title and item:
        art.title = item.title
    return art


def load_article(item: ListedItem, cfg: Config, session: requests.Session | None = None) -> Article:
    html = fetch(item.url, cfg, session)
    return parse_article(html, item.kind, cfg, url=item.url, item=item)
