"""Notion API とのやり取り。Todoデータベースのページを NotionTask として読み書きする。"""

import json
import time
from dataclasses import dataclass
from datetime import datetime

import requests

import config


class NotionError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def headers() -> dict:
    return {
        "Authorization": f"Bearer {config.NOTION_TOKEN}",
        "Notion-Version": config.NOTION_VERSION,
        "Content-Type": "application/json",
    }


def request(method: str, path: str, payload: dict | None = None) -> dict:
    for attempt in range(4):
        res = requests.request(method, f"{config.NOTION_API}{path}", headers=headers(), json=payload, timeout=30)
        # 速度制限（429）や一時的なエラーは、少し待って再試行する
        if (res.status_code == 429 or res.status_code >= 500) and attempt < 3:
            time.sleep(float(res.headers.get("Retry-After", 1 + attempt)))
            continue
        break
    if not res.ok:
        detail = res.json().get("message", res.text) if res.content else res.reason
        hint = ""
        if res.status_code == 404:
            hint = "\n  → データベースIDが正しいか、「コネクト」に TodoSync を追加したか確認してください。"
        elif res.status_code == 401:
            hint = "\n  → NOTION_TOKEN が正しいか確認してください。"
        elif res.status_code == 400:
            hint = "\n  → プロパティ名・種類がデータベースと一致しているか確認してください（check_setup.py）。"
        raise NotionError(res.status_code, f"Notion API エラー ({res.status_code}): {detail}{hint}")
    return res.json()


# ---------- プロパティの変換 ----------

# NotionTask の項目名 → (Notionのプロパティ名, 種類)
FIELD_PROPS = {
    "title": (config.PROP_TITLE, "title"),
    "importance": (config.PROP_IMPORTANCE, "select"),
    "due": (config.PROP_DUE, "date"),
    "done": (config.PROP_DONE, "checkbox"),
    "done_at": (config.PROP_DONE_AT, "date"),
    "source": (config.PROP_SOURCE, "rich_text"),
    "gtask_id": (config.PROP_GTASK_ID, "rich_text"),
    "snapshot": (config.PROP_SNAPSHOT, "rich_text"),
}


def rich_text(text: str) -> list:
    return [{"type": "text", "text": {"content": text[:2000]}}]


def plain(items: list) -> str:
    return "".join(t.get("plain_text", "") for t in items)


def to_props(fields: dict) -> dict:
    """{"title": "…", "due": "2026-10-30", …} → Notion API の properties の形"""
    props = {}
    for key, value in fields.items():
        name, kind = FIELD_PROPS[key]
        if key == "snapshot":
            value = json.dumps(value, ensure_ascii=False) if value else ""
        if kind == "title":
            props[name] = {"title": rich_text(value)}
        elif kind == "select":
            props[name] = {"select": {"name": value} if value else None}
        elif kind == "date":
            props[name] = {"date": {"start": value} if value else None}
        elif kind == "checkbox":
            props[name] = {"checkbox": bool(value)}
        else:
            props[name] = {"rich_text": rich_text(value) if value else []}
    return props


@dataclass
class NotionTask:
    page_id: str
    url: str
    title: str
    importance: str | None
    due: str | None  # "YYYY-MM-DD"
    done: bool
    done_at: str | None
    gtask_id: str
    snapshot: dict | None  # 前回同期したときの値
    last_edited: datetime


def parse_date(prop: dict) -> str | None:
    date = prop.get("date")
    return date["start"] if date else None


def from_page(page: dict) -> NotionTask:
    p = page["properties"]
    select = p.get(config.PROP_IMPORTANCE, {}).get("select")
    due = parse_date(p.get(config.PROP_DUE, {}))
    try:
        snapshot = json.loads(plain(p.get(config.PROP_SNAPSHOT, {}).get("rich_text", [])) or "null")
    except json.JSONDecodeError:
        snapshot = None  # 手で書き換えられて壊れている → 同期記録なしとして扱う
    return NotionTask(
        page_id=page["id"],
        url=page.get("url", ""),
        title=plain(p.get(config.PROP_TITLE, {}).get("title", [])),
        importance=select["name"] if select else None,
        due=due[:10] if due else None,  # 時刻つきで入力されていても日付だけ使う
        done=p.get(config.PROP_DONE, {}).get("checkbox", False),
        done_at=parse_date(p.get(config.PROP_DONE_AT, {})),
        gtask_id=plain(p.get(config.PROP_GTASK_ID, {}).get("rich_text", [])).strip(),
        snapshot=snapshot if isinstance(snapshot, dict) else None,
        last_edited=datetime.fromisoformat(page["last_edited_time"]),
    )


# ---------- 読み書き ----------

def query_all() -> list[NotionTask]:
    """ゴミ箱に入っていないページをすべて取得する。"""
    tasks, cursor = [], None
    while True:
        payload = {"page_size": 100}
        if cursor:
            payload["start_cursor"] = cursor
        data = request("POST", f"/databases/{config.NOTION_DATABASE_ID}/query", payload)
        tasks += [from_page(page) for page in data.get("results", [])]
        if not data.get("has_more"):
            return tasks
        cursor = data["next_cursor"]


def create(fields: dict) -> NotionTask:
    page = request("POST", "/pages", {
        "parent": {"database_id": config.NOTION_DATABASE_ID},
        "properties": to_props(fields),
    })
    return from_page(page)


def update(page_id: str, fields: dict) -> None:
    request("PATCH", f"/pages/{page_id}", {"properties": to_props(fields)})


def archive(page_id: str) -> None:
    """ゴミ箱へ移動する（Notionのゴミ箱から30日間は戻せる）。"""
    request("PATCH", f"/pages/{page_id}", {"archived": True})


def is_gone(page_id: str) -> bool:
    """ページがゴミ箱に入っているか、完全に削除されていれば True。"""
    try:
        page = request("GET", f"/pages/{page_id}")
    except NotionError as e:
        if e.status == 404:
            return True
        raise
    return bool(page.get("archived") or page.get("in_trash"))


def page_link(page_id: str) -> str:
    return f"https://www.notion.so/{page_id.replace('-', '')}"
