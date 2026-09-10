"""Конвертация HTML-содержимого страниц LOXA RPG в Markdown.

Разметка сайта генерируется кастомным markdown-рендерером (классы zmd-*),
поэтому набор тегов предсказуем: заголовки, абзацы, strong/em, списки,
цитаты, код, ссылки, картинки, hr, table.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup, NavigableString, Tag

BLOCK_TAGS = {
    "p", "div", "section", "article", "header", "footer", "ul", "ol", "li",
    "blockquote", "pre", "table", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "hr",
}


def _clean_inline(text: str) -> str:
    """Схлопываем пробелы/переносы внутри инлайнового текста."""
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]*\n[ \t]*", " ", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text


class HtmlToMarkdown:
    def __init__(self, base_url: str = ""):
        self.base_url = base_url

    # ------------------------------------------------------------------ #
    def convert(self, html: str | Tag) -> str:
        soup = BeautifulSoup(html, "html.parser") if isinstance(html, str) else html
        out = self._children(soup)
        out = re.sub(r"\n{3,}", "\n\n", out)
        out = "\n".join(line.rstrip() for line in out.split("\n"))
        return out.strip()

    # ------------------------------------------------------------------ #
    def _children(self, node: Tag | BeautifulSoup) -> str:
        return "".join(self._node(child) for child in node.children)

    def _node(self, node) -> str:
        if isinstance(node, NavigableString):
            return _clean_inline(str(node))
        if not isinstance(node, Tag):
            return ""

        name = node.name.lower()
        if name in {"script", "style", "noscript", "svg", "button", "nav", "footer"}:
            return ""
        if node.has_attr("hidden") and name == "div":
            return ""

        if name in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            level = int(name[1])
            text = self._inline(node).strip()
            return f"\n\n{'#' * level} {text}\n\n" if text else ""

        if name == "p":
            text = self._inline(node).strip()
            return f"\n\n{text}\n\n" if text else ""

        if name == "br":
            return "\n"

        if name == "hr":
            return "\n\n---\n\n"

        if name in {"strong", "b"}:
            text = self._inline(node).strip()
            return f"**{text}**" if text else ""

        if name in {"em", "i"}:
            text = self._inline(node).strip()
            return f"*{text}*" if text else ""

        if name in {"del", "s", "strike"}:
            text = self._inline(node).strip()
            return f"~~{text}~~" if text else ""

        if name == "code":
            if node.find_parent("pre") is not None:
                return self._children(node)
            text = node.get_text()
            fence = "`" if "`" not in text else "``"
            return f"{fence}{text}{fence}"

        if name == "pre":
            code_el = node.find("code")
            text = (code_el or node).get_text()
            text = text.replace("\r\n", "\n").strip("\n")
            return f"\n\n```\n{text}\n```\n\n"

        if name == "a":
            href = (node.get("href") or "").strip()
            text = self._inline(node).strip()
            if not text:
                return ""
            if not href or href.startswith(("#", "javascript:")):
                return text
            href = urljoin(self.base_url, href)
            return f"[{text}]({href})"

        if name == "img":
            src = (node.get("src") or "").strip()
            if not src:
                return ""
            alt = (node.get("alt") or "").strip()
            return f"![{alt}]({urljoin(self.base_url, src)})"

        if name in {"ul", "ol"}:
            return f"\n\n{self._list(node, ordered=name == 'ol')}\n\n"

        if name == "li":
            return self._inline(node).strip()

        if name == "blockquote":
            inner = self.convert(node)
            quoted = "\n".join(
                f"> {line}" if line.strip() else ">" for line in inner.split("\n")
            )
            return f"\n\n{quoted}\n\n"

        if name == "table":
            return f"\n\n{self._table(node)}\n\n"

        if name == "figure":
            return f"\n\n{self._children(node)}\n\n"

        if name == "figcaption":
            text = self._inline(node).strip()
            return f"\n*{text}*\n" if text else ""

        if name in {"div", "section", "article", "main", "header", "footer", "span", "time"}:
            sep = "\n\n" if name in {"div", "section", "article", "main"} else ""
            inner = self._children(node)
            if name in {"div", "section", "article", "main"}:
                return f"\n\n{inner}\n\n" if inner.strip() else ""
            return inner

        # Неизвестный тег — просто recurse
        return self._children(node)

    # ------------------------------------------------------------------ #
    def _inline(self, node: Tag) -> str:
        return self._children(node)

    def _list(self, node: Tag, ordered: bool, depth: int = 0) -> str:
        lines: list[str] = []
        index = 1
        for li in node.find_all("li", recursive=False):
            marker = f"{index}." if ordered else "-"
            nested = [c for c in li.find_all(["ul", "ol"], recursive=False)]
            for n in nested:
                n.extract()
            text = self._inline(li).strip()
            lines.append(f"{'  ' * depth}{marker} {text}".rstrip())
            for n in nested:
                lines.append(self._list(n, ordered=n.name == "ol", depth=depth + 1))
            index += 1
        return "\n".join(lines)

    def _table(self, node: Tag) -> str:
        rows: list[list[str]] = []
        for tr in node.find_all("tr"):
            cells = [self._inline(c).strip() for c in tr.find_all(["th", "td"])]
            if cells:
                rows.append(cells)
        if not rows:
            return ""
        width = max(len(r) for r in rows)
        rows = [r + [""] * (width - len(r)) for r in rows]
        out = ["| " + " | ".join(rows[0]) + " |"]
        out.append("| " + " | ".join(["---"] * width) + " |")
        for r in rows[1:]:
            out.append("| " + " | ".join(r) + " |")
        return "\n".join(out)


def html_to_markdown(html: str, base_url: str = "") -> str:
    return HtmlToMarkdown(base_url).convert(html)
