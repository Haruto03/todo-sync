"""NotionのTodoデータベースと Google ToDoリストを同期する。

項目（タイトル・重要度・期限・完了）ごとに、前回同期したときの値（Notionの「同期スナップショット」）と比べ、
- 片方だけ変わっていたら、その値をもう片方に反映する
- 両方変わっていたら、更新時刻が新しい方を採用する
削除は、Googleで消したらNotionではゴミ箱へ、Notionでゴミ箱に入れたらGoogleからも削除する。

実行: .venv\\Scripts\\python.exe sync.py            1回だけ同期する
      .venv\\Scripts\\python.exe sync.py --dry-run  何をするかを表示するだけ（書き込まない）
"""

import argparse
import sys
import threading
from collections import Counter
from datetime import datetime
from typing import Callable

import config
import gtasks_api
import notion_api
from gtasks_api import GoogleTask
from notion_api import NotionTask

FIELDS = ("title", "importance", "due", "done")
FIELD_LABELS = {"title": "タイトル", "importance": "重要度", "due": "期限", "done": "完了"}
# 1回の同期でこの件数以上、かつ全体の半分以上を削除しようとしたら、設定ミスなどを疑って止める
MASS_DELETE_LIMIT = 5

# 進行状況の表示先。CLIでは print、アプリでは画面表示などに差し替える
log: Callable[[str], None] = print
_lock = threading.Lock()  # 定期同期と「登録直後の同期」が同時に走らないようにする


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def notion_values(p: NotionTask) -> dict:
    return {"title": p.title, "importance": p.importance or config.DEFAULT_IMPORTANCE, "due": p.due, "done": p.done}


def google_values(t: GoogleTask, fallback_importance: str) -> dict:
    # タイトルの印が消されていたら、重要度は変わっていないものとして扱う
    return {"title": t.title, "importance": t.importance or fallback_importance, "due": t.due, "done": t.done}


def done_at_fields(p: NotionTask, done: bool, completed_at: str | None = None) -> dict:
    """完了にしたら完了日時を入れ、未完了に戻したら消す。"""
    if done and not p.done_at:
        return {"done_at": completed_at or now_iso()}
    if not done and p.done_at:
        return {"done_at": None}
    return {}


def labels(fields) -> str:
    return "・".join(FIELD_LABELS[f] for f in fields)


class Syncer:
    def __init__(self, dry_run: bool, interactive: bool, allow_mass_delete: bool):
        self.dry_run = dry_run
        self.allow_mass_delete = allow_mass_delete
        self.google = gtasks_api.Client(interactive)
        self.list_id = None
        self.counts: Counter = Counter()

    def act(self, kind: str, message: str) -> bool:
        """操作を記録して表示する。実際に書き込んでよければ True。"""
        self.counts[kind] += 1
        log(("（予定）" if self.dry_run else "") + message)
        return not self.dry_run

    def check_mass_delete(self, items: list, total: int, what: str) -> None:
        if not self.allow_mass_delete and len(items) >= MASS_DELETE_LIMIT and len(items) * 2 >= total:
            raise RuntimeError(
                f"{what}を一度に{len(items)}件削除しようとしたため、安全のため同期を止めました。\n"
                "  → 意図した削除なら sync.py --allow-mass-delete で実行してください。"
            )

    def run(self) -> Counter:
        name = config.GOOGLE_TASKLIST_NAME
        pages = notion_api.query_all()
        self.list_id = self.google.find_list(name)
        list_is_new = self.list_id is None
        if list_is_new and self.act("リスト作成", f"Google ToDoリストに「{name}」を作成"):
            self.list_id = self.google.create_list(name)
        gtasks = [] if list_is_new else self.google.list_tasks(self.list_id)

        live = {t.task_id: t for t in gtasks if not t.deleted}
        linked = {p.gtask_id for p in pages if p.gtask_id}
        orphans = [t for t in live.values() if t.task_id not in linked]
        orphan_by_page = {t.notion_page_id: t for t in orphans if t.notion_page_id}

        new_pages, pairs, deleted_on_google = [], [], []
        for p in pages:
            if p.gtask_id and p.gtask_id in live:
                pairs.append((p, live[p.gtask_id]))
            elif p.page_id.replace("-", "") in orphan_by_page:
                # Googleに追加した直後にNotionへの書き込みが失敗していた → 作り直さずにつなぎ直す
                pairs.append((p, orphan_by_page.pop(p.page_id.replace("-", ""))))
            elif p.gtask_id and not list_is_new:
                deleted_on_google.append(p)
            elif p.title.strip():  # タイトルが空のページ（入力途中など）はまだ送らない
                new_pages.append(p)

        relinked = {t.task_id for _, t in pairs}
        orphans = [t for t in orphans if t.task_id not in relinked]
        new_google = [t for t in orphans if not t.notion_page_id]
        # リンクがあるのにNotion側で見当たらないものは、本当に消されたか確認してから削除する
        deleted_on_notion = [t for t in orphans if t.notion_page_id and notion_api.is_gone(t.notion_page_id)]

        self.check_mass_delete(deleted_on_google, len(pages), "Notionのタスク")
        self.check_mass_delete(deleted_on_notion, len(live), "Googleのタスク")

        for p in new_pages:
            self.push_new(p)
        for p, t in pairs:
            self.sync_pair(p, t)
        for t in new_google:
            self.import_google(t)
        for p in deleted_on_google:
            if self.act("Notion削除", f"Notionでゴミ箱へ「{p.title}」（Googleで削除されたため）"):
                notion_api.archive(p.page_id)
        for t in deleted_on_notion:
            if self.act("Google削除", f"Googleから削除「{t.title}」（Notionで削除されたため）"):
                self.google.delete(self.list_id, t.task_id)
        return self.counts

    # --- Notion → Google（新規） ---

    def push_new(self, p: NotionTask) -> None:
        values = notion_values(p)
        if not self.act("Google追加", f"Googleに追加「{p.title}」"):
            return
        body = gtasks_api.to_body(**values)
        body["notes"] = f"Notionで開く: {notion_api.page_link(p.page_id)}"
        t = self.google.insert(self.list_id, body)

        fields = {"gtask_id": t.task_id, "snapshot": values, **done_at_fields(p, values["done"])}
        if p.importance is None:
            fields["importance"] = values["importance"]
        notion_api.update(p.page_id, fields)

    # --- Google → Notion（新規） ---

    def import_google(self, t: GoogleTask) -> None:
        values = google_values(t, config.DEFAULT_IMPORTANCE)
        if not values["title"]:
            return
        if not self.act("Notion追加", f"Notionに追加「{t.title}」（Googleで追加されたため）"):
            return
        fields = {**values, "source": "Google ToDoリスト", "gtask_id": t.task_id, "snapshot": values}
        if values["done"]:
            fields["done_at"] = t.completed_at or now_iso()
        page = notion_api.create(fields)

        link = f"Notionで開く: {notion_api.page_link(page.page_id)}"
        patch = {"notes": f"{t.notes}\n{link}" if t.notes else link}
        title = gtasks_api.format_title(values["title"], values["importance"])
        if title != t.raw_title:
            patch["title"] = title  # 重要度の印を付ける
        self.google.patch(self.list_id, t.task_id, patch)

    # --- 両方にあるもの ---

    def sync_pair(self, p: NotionTask, t: GoogleTask) -> None:
        n = notion_values(p)
        s = p.snapshot or google_values(t, n["importance"])  # 同期記録がなければNotionを優先する
        g = google_values(t, s.get("importance") or n["importance"])

        merged = {}
        for f in FIELDS:
            if n[f] == g[f] or g[f] == s.get(f):
                merged[f] = n[f]  # 同じ、またはNotionだけ変わった
            elif n[f] == s.get(f):
                merged[f] = g[f]  # Googleだけ変わった
            else:
                merged[f] = n[f] if p.last_edited >= t.updated else g[f]  # 両方変わった → 新しい方

        # 先にGoogleを更新し、成功してから同期記録を書く（失敗しても次回やり直せる）
        patch = {}
        body = gtasks_api.to_body(**merged)
        if body["title"] != t.raw_title:
            patch["title"] = body["title"]
        if merged["due"] != t.due:
            patch["due"] = body["due"]
        if merged["done"] != t.done:
            patch["status"] = body["status"]
            if "completed" in body:
                patch["completed"] = body["completed"]
        if patch:
            changed = [f for f in FIELDS if merged[f] != g[f]]
            if not self.act("Google更新", f"Googleを更新「{merged['title']}」: {labels(changed) or '重要度の印'}"):
                return
            t = self.google.patch(self.list_id, t.task_id, patch)

        raw = {"title": p.title, "importance": p.importance, "due": p.due, "done": p.done}
        changed = [f for f in FIELDS if raw[f] != merged[f]]
        fields = {f: merged[f] for f in changed}
        fields.update(done_at_fields(p, merged["done"], t.completed_at))
        if changed and not self.act("Notion更新", f"Notionを更新「{merged['title']}」: {labels(changed)}"):
            return
        if fields or p.snapshot != merged:
            if self.dry_run:
                return
            fields["snapshot"] = merged
            notion_api.update(p.page_id, fields)


def sync_once(dry_run: bool = False, interactive: bool = False, allow_mass_delete: bool = False) -> Counter:
    with _lock:
        return Syncer(dry_run, interactive, allow_mass_delete).run()


def main() -> None:
    parser = argparse.ArgumentParser(description="NotionのTodoとGoogle ToDoリストを同期する")
    parser.add_argument("--dry-run", action="store_true", help="何をするかを表示するだけで、書き込まない")
    parser.add_argument("--allow-mass-delete", action="store_true", help="一度に多くのタスクを削除するのを許可する")
    args = parser.parse_args()

    missing = config.missing_env()
    if missing:
        print(f".env に次の値が設定されていません: {', '.join(missing)}")
        sys.exit(1)
    try:
        counts = sync_once(args.dry_run, interactive=True, allow_mass_delete=args.allow_mass_delete)
    except Exception as e:
        print(f"✗ 同期に失敗しました: {e}")
        sys.exit(1)
    summary = "、".join(f"{kind} {n}件" for kind, n in counts.items())
    print(f"✓ 同期しました（{summary or '変更なし'}）" + ("　※dry-runのため書き込んでいません" if args.dry_run else ""))


if __name__ == "__main__":
    main()
