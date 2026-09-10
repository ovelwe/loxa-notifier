"""Модели данных проекта."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ListedItem:
    """Элемент, найденный в листинге /ru/blog или /ru/book."""

    kind: str                     # "blog" | "book"
    url: str                      # абсолютный URL страницы
    path: str                     # путь без домена, напр. /ru/blog/innovations-1
    title: str = ""               # заголовок из листинга
    summary: str = ""             # краткое описание из листинга
    date_raw: str = ""            # дата из листинга (если есть)
    order: int = 0                # порядок в листинге (0 = первый/самый новый)
    chapter_no: str = ""          # "03" для книг
    source: str = "listing"       # откуда узнали: listing | sitemap


@dataclass
class Article:
    """Полностью разобранная страница статьи/главы."""

    kind: str
    url: str
    title: str = ""
    subtitle: str = ""            # напр. "Глава 03"
    date_raw: str = ""
    reading_time: str = ""
    markdown: str = ""            # тело материала в Markdown
    item: ListedItem | None = field(default=None, repr=False)

    @property
    def slug(self) -> str:
        return self.url.rstrip("/").rsplit("/", 1)[-1]
