"""Состояние: какие материалы уже видели/отправляли.

Файл маленький и его удобно коммитить в git, чтобы GitHub Actions
не отправлял дубликаты между запусками.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from .models import ListedItem

log = logging.getLogger("loxa.state")
STATE_VERSION = 1


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class State:
    def __init__(self, path: str | Path = "state.json", data: dict | None = None):
        self.path = Path(path)
        self.data = data or {
            "version": STATE_VERSION,
            "updated_at": _now_iso(),
            "blog": {"known": {}, "last_notified": ""},
            "book": {"known": {}, "last_notified": ""},
            "last_run": "",
        }

    # ------------------------------------------------------------------ #
    @classmethod
    def load(cls, path: str | Path = "state.json") -> "State":
        p = Path(path)
        if not p.is_file():
            log.info("state.json не найден — стартуем с чистого состояния (%s)", p)
            return cls(p)
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            log.error("state.json повреждён (%s) — начинаю заново", exc)
            return cls(p)
        state = cls(p, data)
        for kind in ("blog", "book"):
            state.data.setdefault(kind, {"known": {}, "last_notified": ""})
            state.data[kind].setdefault("known", {})
        return state

    def save(self, dry_run: bool = False) -> None:
        self.data["updated_at"] = _now_iso()
        if dry_run:
            log.info("dry-run: state.json не перезаписываю")
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)
        log.info("Состояние сохранено: %s", self.path)

    # ------------------------------------------------------------------ #
    def is_known(self, kind: str, path: str) -> bool:
        return path in self.data.get(kind, {}).get("known", {})

    def mark(self, kind: str, item: ListedItem, notified: bool, dry_run: bool = False) -> None:
        self.data.setdefault(kind, {"known": {}, "last_notified": ""})["known"][item.path] = {
            "first_seen": _now_iso(),
            "title": item.title,
            "notified": bool(notified) or self.is_notified(kind, item.path),
        }
        if notified and not dry_run:
            self.data[kind]["last_notified"] = _now_iso()

    def is_notified(self, kind: str, path: str) -> bool:
        entry = self.data.get(kind, {}).get("known", {}).get(path)
        return bool(entry and entry.get("notified"))

    def known_paths(self, kind: str) -> set[str]:
        return set(self.data.get(kind, {}).get("known", {}).keys())

    def set_last_run(self) -> None:
        self.data["last_run"] = _now_iso()

    def baseline(self, kind: str, items: list[ListedItem], notified: bool = False) -> int:
        """Помечает всё найденное как известное (для первого запуска)."""
        added = 0
        for item in items:
            if not self.is_known(kind, item.path):
                self.mark(kind, item, notified=notified)
                added += 1
        return added
