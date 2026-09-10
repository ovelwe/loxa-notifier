"""Формат сообщений для Telegram: Markdown -> Telegram HTML/MarkdownV2,
шаблоны постов, безопасная разбивка на части (лимит 4096 символов).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .config import Config
from .models import Article

MDV2_SPECIALS = r"_*[]()~`>#+-=|{}.!"

DEFAULT_BLOG_TEMPLATE = (
    "📝 <b>Новая статья в блоге</b>\n"
    "\n"
    "<b>{title}</b>\n"
    "{meta}\n"
    "\n"
    "{body}\n"
    "\n"
    "🔗 <a href=\"{url}\">Открыть на сайте</a>"
)

DEFAULT_BOOK_TEMPLATE = (
    "📖 <b>Вышла новая глава книги!</b>\n"
    "\n"
    "<b>{heading}</b>\n"
    "{meta}\n"
    "{summary}"
    "\n"
    "🔗 <a href=\"{url}\">Читать главу</a>"
)


# --------------------------------------------------------------------- #
# Markdown -> HTML для Telegram
# --------------------------------------------------------------------- #
def _escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _inline_html(text: str) -> str:
    text = _escape_html(text)
    # картинки: ![alt](url) -> ссылка (Telegram сам подтянет превью)
    text = re.sub(
        r"!\[([^\]]*)\]\(([^)\s]+)\)",
        lambda m: f'🖼 <a href="{m.group(2)}">{m.group(1) or "изображение"}</a>',
        text,
    )
    # ссылки
    text = re.sub(
        r"\[([^\]]+)\]\((https?://[^\s)]+)\)",
        lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>',
        text,
    )
    # инлайн-код
    text = re.sub(r"`([^`\n]+)`", lambda m: f"<code>{m.group(1)}</code>", text)
    # жирный / курсив / зачёркнутый
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text, flags=re.S)
    text = re.sub(r"(?<![*\w])\*([^*\n]+)\*(?!\*)", r"<i>\1</i>", text)
    text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text, flags=re.S)
    text = re.sub(r"(?<![_\w])_([^_\n]+)_(?![_\w])", r"<i>\1</i>", text)
    return text


def md_to_tg_html(md: str) -> str:
    """Markdown -> Telegram HTML (parse_mode=HTML)."""
    out: list[str] = []
    in_code = False
    code_buf: list[str] = []
    quote_buf: list[str] = []

    def flush_quote() -> None:
        if quote_buf:
            inner = "\n".join(quote_buf)
            out.append(f"<blockquote>{inner}</blockquote>")
            quote_buf.clear()

    for raw_line in md.replace("\r\n", "\n").split("\n"):
        line = raw_line.rstrip()

        if line.strip().startswith("```"):
            if in_code:
                out.append("<pre>" + _escape_html("\n".join(code_buf)) + "</pre>")
                code_buf.clear()
                in_code = False
            else:
                flush_quote()
                in_code = True
            continue
        if in_code:
            code_buf.append(line)
            continue

        if line.startswith(">"):
            quote_buf.append(_inline_html(line.lstrip("> ").strip()))
            continue
        flush_quote()

        stripped = line.strip()
        if not stripped:
            out.append("")
            continue

        if stripped in {"---", "***", "___"}:
            out.append("──────────")
            continue

        m = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if m:
            level = len(m.group(1))
            body = _inline_html(m.group(2))
            if level <= 2:
                out.append(f"<b>{body}</b>")
            else:
                out.append(f"<b>▸ {body}</b>")
            continue

        m = re.match(r"^([-*+])\s+(.*)$", stripped)
        if m:
            out.append("• " + _inline_html(m.group(2)))
            continue

        m = re.match(r"^(\d+)[.)]\s+(.*)$", stripped)
        if m:
            out.append(f"{m.group(1)}. " + _inline_html(m.group(2)))
            continue

        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                continue
            out.append(" | ".join(_inline_html(c) for c in cells))
            continue

        out.append(_inline_html(stripped))

    if in_code and code_buf:
        out.append("<pre>" + _escape_html("\n".join(code_buf)) + "</pre>")
    flush_quote()

    text = "\n".join(out)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# --------------------------------------------------------------------- #
# Markdown -> MarkdownV2 (альтернативный режим)
# --------------------------------------------------------------------- #
def _escape_mdv2(text: str) -> str:
    return re.sub(rf"([{re.escape(MDV2_SPECIALS)}])", r"\\\1", text)


def md_to_tg_mdv2(md: str) -> str:
    text = _escape_mdv2(md)
    text = re.sub(r"^\\#+ (.*)$", lambda m: f"*{m.group(1)}*", text, flags=re.M)
    text = re.sub(r"\\\*\\\*(.+?)\\\*\\\*", r"*\1*", text, flags=re.S)
    return text


def md_to_tg(md: str, parse_mode: str) -> str:
    if parse_mode.upper().startswith("MARKDOWN"):
        return md_to_tg_mdv2(md)
    return md_to_tg_html(md)


# --------------------------------------------------------------------- #
# Шаблоны
# --------------------------------------------------------------------- #
def _fill(template: str, ctx: dict[str, str]) -> str:
    out = template
    for key, value in ctx.items():
        out = out.replace("{" + key + "}", value)
    return re.sub(r"[ \t]+\n", "\n", out).strip()


def render_blog(article: Article, cfg: Config, include_body: bool = True,
                body: str | None = None, template: str | None = None) -> str:
    meta_bits = [b for b in (article.date_raw, article.reading_time) if b]
    ctx = {
        "title": _inline_html(article.title) if cfg.parse_mode.upper() == "HTML" else article.title,
        "date": article.date_raw,
        "reading_time": article.reading_time,
        "meta": " · ".join(meta_bits),
        "url": article.url,
        "summary": _inline_html(article.item.summary) if article.item else "",
        "heading": article.title,
        "slug": article.slug,
        "site": cfg.site_base_url,
    }
    if include_body:
        body_md = article.markdown if body is None else body
        ctx["body"] = md_to_tg(body_md, cfg.parse_mode) if body_md else ""
    else:
        ctx["body"] = ""
        template = template or (
            "📝 <b>Новая статья в блоге</b>\n\n<b>{title}</b>\n{meta}\n"
            "\n🔗 <a href=\"{url}\">Открыть на сайте</a>"
        )
    tpl = template or DEFAULT_BLOG_TEMPLATE
    return _fill(tpl, ctx)


def render_book(article: Article, cfg: Config, template: str | None = None) -> str:
    heading_parts = [p for p in (article.subtitle, article.title) if p]
    heading = " — ".join(heading_parts) if len(heading_parts) > 1 else (heading_parts[0] if heading_parts else article.title)
    meta_bits = [b for b in (article.date_raw, article.reading_time) if b]
    summary = ""
    if article.item and article.item.summary:
        raw = article.item.summary
        if cfg.parse_mode.upper() == "HTML":
            summary = f"<i>{_escape_html(raw)}</i>\n"
        else:
            summary = f"_{raw}_\n"
    ctx = {
        "heading": heading,
        "title": article.title,
        "chapter": article.subtitle,
        "date": article.date_raw,
        "reading_time": article.reading_time,
        "meta": " · ".join(meta_bits),
        "summary": summary,
        "url": article.url,
        "site": cfg.site_base_url,
    }
    tpl = template or DEFAULT_BOOK_TEMPLATE
    return _fill(tpl, ctx)


# --------------------------------------------------------------------- #
# Безопасная разбивка (не режем теги, переносим открытые на следующую часть)
# --------------------------------------------------------------------- #
AVAILABLE_TAGS = {
    "b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "code", "pre",
    "a", "span", "tg-spoiler", "blockquote",
}
TOKEN_RE = re.compile(r"(</?[a-zA-Z][^>]*>)")


@dataclass
class _OpenTag:
    opening: str
    name: str


def split_html(text: str, limit: int = 3800) -> list[str]:
    """Режет HTML-сообщение на части, не ломая теги и не разрезая слова."""
    tokens = TOKEN_RE.split(text)
    parts: list[str] = []
    buf: list[str] = []
    buf_len = 0
    open_tags: list[_OpenTag] = []

    def closing_suffix(tags: list[_OpenTag]) -> str:
        return "".join(f"</{t.name}>" for t in reversed(tags))

    def opening_prefix(tags: list[_OpenTag]) -> str:
        return "".join(t.opening for t in tags)

    def flush() -> None:
        nonlocal buf, buf_len, open_tags
        parts.append("".join(buf) + closing_suffix(open_tags))
        buf = [opening_prefix(open_tags)]
        buf_len = len(buf[0])

    for token in tokens:
        if token == "":
            continue
        is_tag = bool(TOKEN_RE.fullmatch(token))
        if is_tag:
            name_match = re.match(r"</?([a-zA-Z0-9-]+)", token)
            name = (name_match.group(1).lower() if name_match else "")
            is_close = token.startswith("</")
            is_self_closing = token.endswith("/>")
            if name not in AVAILABLE_TAGS:
                # незнакомый/опасный тег — вырезаем
                continue
            if is_close:
                for i in range(len(open_tags) - 1, -1, -1):
                    if open_tags[i].name == name:
                        del open_tags[i]
                        break
                buf.append(token)
                buf_len += len(token)
            else:
                buf.append(token)
                buf_len += len(token)
                if not is_self_closing:
                    open_tags.append(_OpenTag(token, name))
            continue

        # текстовый кусок — при необходимости режем по границам слов
        chunk = token
        while chunk:
            budget = limit - buf_len - len(closing_suffix(open_tags))
            if budget <= 0:
                flush()
                budget = limit - buf_len - len(closing_suffix(open_tags))
            if len(chunk) <= budget:
                buf.append(chunk)
                buf_len += len(chunk)
                chunk = ""
            else:
                cut = chunk.rfind("\n", 0, budget)
                if cut < budget // 2:
                    cut = chunk.rfind(" ", 0, budget)
                if cut < budget // 2:
                    cut = budget
                buf.append(chunk[:cut])
                buf_len += cut
                chunk = chunk[cut:]
                flush()

    if "".join(buf).strip() or not parts:
        parts.append("".join(buf) + closing_suffix(open_tags))

    return [p for p in parts if p.strip()] or [""]


def paginate(text: str, limit: int = 3800, max_parts: int = 4) -> tuple[list[str], str]:
    """Возвращает (части для отправки, остаток). Если частей больше max_parts —
    лишнее возвращается как остаток (уйдёт файлом)."""
    reserve = 14  # место под префикс "(1/3) "
    parts = split_html(text, max(500, limit - reserve))
    total = min(len(parts), max_parts)
    if total > 1:
        shown = [f"<i>({i}/{total})</i>\n{p}" for i, p in enumerate(parts[:total], start=1)]
    else:
        shown = parts[:1]
    leftover = "\n\n".join(parts[max_parts:])
    return shown, leftover
