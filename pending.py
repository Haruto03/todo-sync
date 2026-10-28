"""Notionへの登録に失敗したタスクを一時保存し、次の同期のときに再送する。"""

import json
import threading

import config
import notion_api

FILE = config.BASE_DIR / "pending.json"
_lock = threading.Lock()


def load() -> list[dict]:
    try:
        return json.loads(FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save(items: list[dict]) -> None:
    if items:
        FILE.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        FILE.unlink(missing_ok=True)


def add(fields: dict) -> None:
    with _lock:
        save(load() + [fields])


def flush() -> int:
    """再送して、送れた件数を返す。1件でも失敗したら（通信できないなど）、残りは次回に回す。"""
    with _lock:
        items = load()
        for i, fields in enumerate(items):
            try:
                notion_api.create(fields)
            except Exception:
                save(items[i:])
                return i
        save([])
        return len(items)
