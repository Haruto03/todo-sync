"""設定値と、Notionのプロパティ名などの定数。

登録ポップアップ・一覧ポップアップ・同期処理のすべてから使う。
"""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

# Googleのログイン情報（どちらも .gitignore 済み）
GOOGLE_CREDENTIALS_FILE = BASE_DIR / "credentials.json"  # Google Cloudからダウンロードしたもの
GOOGLE_TOKEN_FILE = BASE_DIR / "token.json"  # 初回ログイン時に自動で作られる
GOOGLE_SCOPES = ["https://www.googleapis.com/auth/tasks"]
GOOGLE_TASKLIST_NAME = os.getenv("GOOGLE_TASKLIST_NAME", "Todo")

ADD_HOTKEY = os.getenv("TODO_ADD_HOTKEY", "ctrl+alt+t")
LIST_HOTKEY = os.getenv("TODO_LIST_HOTKEY", "ctrl+alt+l")
SYNC_INTERVAL_SEC = int(os.getenv("TODO_SYNC_INTERVAL_SEC", "180"))

# Notionのプロパティ名（データベース側と完全に一致させる）
PROP_TITLE = "タイトル"
PROP_IMPORTANCE = "重要度"
PROP_DUE = "期限"
PROP_DONE = "完了"
PROP_DONE_AT = "完了日時"
PROP_CREATED = "登録日時"
PROP_SOURCE = "取得元"
PROP_GTASK_ID = "GTask ID"
PROP_SNAPSHOT = "同期スナップショット"

# プロパティ名 → Notion APIでの種類
EXPECTED_PROPERTIES = {
    PROP_TITLE: "title",
    PROP_IMPORTANCE: "select",
    PROP_DUE: "date",
    PROP_DONE: "checkbox",
    PROP_DONE_AT: "date",
    PROP_CREATED: "created_time",
    PROP_SOURCE: "rich_text",
    PROP_GTASK_ID: "rich_text",
    PROP_SNAPSHOT: "rich_text",
}

# 重要度 → Google ToDoリストのタイトル先頭に付ける印
IMPORTANCE_MARKS = {"高": "🔴", "中": "🟡", "低": "🟢"}
DEFAULT_IMPORTANCE = "中"


def missing_env() -> list[str]:
    """.env に設定されていない必須項目の名前を返す。"""
    return [
        name
        for name, value in [
            ("NOTION_TOKEN", NOTION_TOKEN),
            ("NOTION_DATABASE_ID", NOTION_DATABASE_ID),
        ]
        if not value
    ]
