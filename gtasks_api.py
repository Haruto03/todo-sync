"""Google ToDoリスト（Tasks API）とのやり取り。

Google側では重要度をタイトル先頭の印（🔴🟡🟢）で表し、メモ欄にNotionのページへのリンクを入れておく。
（リンクは、Notion側で削除されたタスクを見分けるのに使う）
"""

import re
from dataclasses import dataclass
from datetime import datetime

from googleapiclient.errors import HttpError

import config
import google_auth

MARK_TO_IMPORTANCE = {mark: name for name, mark in config.IMPORTANCE_MARKS.items()}
NOTION_LINK_RE = re.compile(r"notion\.so/([0-9a-f]{32})")
RETRIES = 3


def parse_title(raw: str) -> tuple[str | None, str]:
    """"🔴 レポート提出" → ("高", "レポート提出")。印がなければ重要度は None。"""
    raw = raw.strip()
    for mark, importance in MARK_TO_IMPORTANCE.items():
        if raw.startswith(mark):
            return importance, raw[len(mark):].strip()
    return None, raw


def format_title(title: str, importance: str) -> str:
    return f"{config.IMPORTANCE_MARKS[importance]} {title}"


def due_to_api(due: str | None) -> str | None:
    # Tasks API は日付しか保持しないので、時刻は 00:00 固定で送る
    return f"{due}T00:00:00.000Z" if due else None


def to_body(title: str, importance: str, due: str | None, done: bool) -> dict:
    body = {
        "title": format_title(title, importance),
        "due": due_to_api(due),
        "status": "completed" if done else "needsAction",
    }
    if not done:
        body["completed"] = None  # 未完了に戻すときは完了日時を消す（完了にしたときはGoogleが入れる）
    return body


@dataclass
class GoogleTask:
    task_id: str
    raw_title: str
    title: str
    importance: str | None  # タイトルに印がなければ None
    due: str | None  # "YYYY-MM-DD"
    done: bool
    completed_at: str | None
    notes: str
    notion_page_id: str | None  # メモ欄のリンクから取り出したNotionのページID
    updated: datetime
    deleted: bool


def from_item(item: dict) -> GoogleTask:
    importance, title = parse_title(item.get("title", ""))
    notes = item.get("notes", "")
    link = NOTION_LINK_RE.search(notes)
    return GoogleTask(
        task_id=item["id"],
        raw_title=item.get("title", ""),
        title=title,
        importance=importance,
        due=item["due"][:10] if item.get("due") else None,
        done=item.get("status") == "completed",
        completed_at=item.get("completed"),
        notes=notes,
        notion_page_id=link.group(1) if link else None,
        updated=datetime.fromisoformat(item["updated"]),
        deleted=bool(item.get("deleted")),
    )


class Client:
    def __init__(self, interactive: bool = False):
        self.service = google_auth.tasks_service(interactive)

    def find_list(self, name: str) -> str | None:
        token = None
        while True:
            data = self.service.tasklists().list(maxResults=100, pageToken=token).execute(num_retries=RETRIES)
            for item in data.get("items", []):
                if item["title"] == name:
                    return item["id"]
            token = data.get("nextPageToken")
            if not token:
                return None

    def create_list(self, name: str) -> str:
        return self.service.tasklists().insert(body={"title": name}).execute(num_retries=RETRIES)["id"]

    def list_tasks(self, list_id: str) -> list[GoogleTask]:
        """完了済み・非表示（完了を消去したもの）・削除済みも含めてすべて取得する。"""
        tasks, token = [], None
        while True:
            data = self.service.tasks().list(
                tasklist=list_id, maxResults=100, pageToken=token,
                showCompleted=True, showHidden=True, showDeleted=True,
            ).execute(num_retries=RETRIES)
            tasks += [from_item(item) for item in data.get("items", [])]
            token = data.get("nextPageToken")
            if not token:
                return tasks

    def insert(self, list_id: str, body: dict) -> GoogleTask:
        body = {k: v for k, v in body.items() if v is not None}
        return from_item(self.service.tasks().insert(tasklist=list_id, body=body).execute(num_retries=RETRIES))

    def patch(self, list_id: str, task_id: str, body: dict) -> GoogleTask:
        return from_item(
            self.service.tasks().patch(tasklist=list_id, task=task_id, body=body).execute(num_retries=RETRIES)
        )

    def delete(self, list_id: str, task_id: str) -> None:
        try:
            self.service.tasks().delete(tasklist=list_id, task=task_id).execute(num_retries=RETRIES)
        except HttpError as e:
            if e.resp.status != 404:  # すでに消えていれば問題なし
                raise
