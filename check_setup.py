"""NotionとGoogleの準備ができているかを確認する。

実行: .venv\\Scripts\\python.exe check_setup.py
"""

import requests

import config

OK, NG = "✓", "✗"


def check_notion() -> bool:
    print("■ Notion")
    missing = config.missing_env()
    if missing:
        print(f"  {NG} .env に次の値が設定されていません: {', '.join(missing)}")
        return False

    res = requests.get(
        f"{config.NOTION_API}/databases/{config.NOTION_DATABASE_ID}",
        headers={"Authorization": f"Bearer {config.NOTION_TOKEN}", "Notion-Version": config.NOTION_VERSION},
        timeout=30,
    )
    if res.status_code == 401:
        print(f"  {NG} トークンが正しくありません（401）。.env の NOTION_TOKEN を確認してください。")
        return False
    if res.status_code == 404:
        print(f"  {NG} データベースが見つかりません（404）。")
        print("    → DBの「•••」→「コネクト」に TodoSync を追加したか、IDが正しいか確認してください。")
        return False
    if not res.ok:
        print(f"  {NG} Notion API エラー（{res.status_code}）: {res.text}")
        return False

    db = res.json()
    title = "".join(t.get("plain_text", "") for t in db.get("title", [])) or "(無題)"
    print(f"  {OK} データベース「{title}」に接続できました")

    props = db.get("properties", {})
    all_ok = True
    for name, expected in config.EXPECTED_PROPERTIES.items():
        actual = props.get(name, {}).get("type")
        if actual == expected:
            print(f"  {OK} {name}（{expected}）")
        elif actual is None:
            print(f"  {NG} {name}: プロパティがありません（種類: {expected}）")
            all_ok = False
        else:
            print(f"  {NG} {name}: 種類が {actual} になっています（{expected} にしてください）")
            all_ok = False

    options = {o["name"] for o in props.get(config.PROP_IMPORTANCE, {}).get("select", {}).get("options", [])}
    lacking = [o for o in config.IMPORTANCE_MARKS if o not in options]
    if lacking:
        print(f"  {NG} {config.PROP_IMPORTANCE} の選択肢が足りません: {', '.join(lacking)}")
        all_ok = False
    elif config.PROP_IMPORTANCE in props:
        print(f"  {OK} {config.PROP_IMPORTANCE} の選択肢: 高・中・低")

    extra = [n for n in props if n not in config.EXPECTED_PROPERTIES]
    if extra:
        print(f"  ・使わないプロパティ（あっても問題ありません）: {', '.join(extra)}")
    return all_ok


def check_google() -> bool:
    print("■ Google ToDoリスト")
    try:
        import google_auth

        service = google_auth.tasks_service()
        lists = service.tasklists().list(maxResults=100).execute().get("items", [])
    except Exception as e:
        print(f"  {NG} {e}")
        return False

    print(f"  {OK} ログインできました（ToDoリスト: {', '.join(l['title'] for l in lists) or 'なし'}）")
    if any(l["title"] == config.GOOGLE_TASKLIST_NAME for l in lists):
        print(f"  {OK} 同期先のリスト「{config.GOOGLE_TASKLIST_NAME}」があります")
    else:
        print(f"  ・同期先のリスト「{config.GOOGLE_TASKLIST_NAME}」は、同期を始めるときに自動で作ります")
    return True


def main() -> None:
    results = [check_notion(), check_google()]
    print()
    print("準備OKです。" if all(results) else f"{NG} の項目を直してから、もう一度実行してください。")


if __name__ == "__main__":
    main()
