// todo-sync: 今日のTodoを iPhone のロック画面に表示する Scriptable 用ウィジェット
//
// - 今日が期限（期限切れ・持ち越し分も含む）で未完了のタスクを、重要度の高い順に表示する
// - ロック画面: 時計の下（長方形）・時計の上（1行）・丸形 に対応。ホーム画面のウィジェットにも使える
// - タップすると Notion の Todo を開く
//
// 設定: Scriptable アプリでこのスクリプトを1回実行し、Notion の「読み取り専用」のトークンと
//       データベースの ID を入力する（キーチェーンに保存され、スクリプトには書かない）

const KEY_TOKEN = "todo-sync.notionToken";
const KEY_DB = "todo-sync.databaseId";
const NOTION_VERSION = "2022-06-28";
const REFRESH_MINUTES = 15; // 更新を頼む間隔（実際にいつ更新するかは iOS が決める）

// Notion のプロパティ名（データベース側と完全に一致させる）
const PROP = { title: "タイトル", importance: "重要度", due: "期限", done: "完了", original: "元の期限" };
const ORDER = { "高": 0, "中": 1, "低": 2 };
const COLORS = { "高": "#cf222e", "中": "#bf8700", "低": "#1a7f37" };

// ---------- 設定 ----------

async function setup() {
  const alert = new Alert();
  alert.title = "todo-sync の設定";
  alert.message = "Notion の読み取り専用トークン（ntn_...）と、Todo データベースの ID（32文字）を入力してください。";
  alert.addSecureTextField("トークン（ntn_...）", Keychain.contains(KEY_TOKEN) ? Keychain.get(KEY_TOKEN) : "");
  alert.addTextField("データベースの ID", Keychain.contains(KEY_DB) ? Keychain.get(KEY_DB) : "");
  alert.addAction("保存");
  alert.addCancelAction("キャンセル");
  if ((await alert.presentAlert()) === -1) return false;

  const token = alert.textFieldValue(0).trim();
  // URL をそのまま貼っても、32文字の ID だけを取り出す
  const db = (alert.textFieldValue(1).replace(/-/g, "").match(/[0-9a-f]{32}/i) || [""])[0];
  if (!token || !db) {
    await message("入力が足りません", "トークンとデータベースの ID の両方を入力してください。");
    return false;
  }
  Keychain.set(KEY_TOKEN, token);
  Keychain.set(KEY_DB, db);
  return true;
}

async function message(title, text) {
  const alert = new Alert();
  alert.title = title;
  alert.message = text;
  alert.addAction("OK");
  await alert.presentAlert();
}

// ---------- Notion から今日のタスクを取る ----------

function localDate(d = new Date()) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function plain(items) {
  return (items || []).map((t) => t.plain_text || "").join("");
}

async function fetchToday(token, db) {
  const today = localDate();
  const tasks = [];
  let cursor = undefined;
  do {
    const req = new Request(`https://api.notion.com/v1/databases/${db}/query`);
    req.method = "POST";
    req.headers = {
      Authorization: `Bearer ${token}`,
      "Notion-Version": NOTION_VERSION,
      "Content-Type": "application/json",
    };
    req.body = JSON.stringify({
      filter: {
        and: [
          { property: PROP.done, checkbox: { equals: false } },
          { property: PROP.due, date: { on_or_before: today } }, // 期限切れ（持ち越し前）も含める
        ],
      },
      page_size: 100,
      ...(cursor ? { start_cursor: cursor } : {}),
    });
    const data = await req.loadJSON();
    const status = req.response.statusCode;
    if (status !== 200) {
      const hint = { 401: "トークンが正しくありません", 404: "DBが見つかりません（コネクトを追加したか確認）" }[status];
      throw new Error(hint || data.message || `HTTP ${status}`);
    }
    for (const page of data.results) {
      const p = page.properties;
      const due = p[PROP.due] && p[PROP.due].date ? p[PROP.due].date.start.slice(0, 10) : today;
      const original = p[PROP.original] && p[PROP.original].date ? p[PROP.original].date.start.slice(0, 10) : null;
      tasks.push({
        title: plain(p[PROP.title] && p[PROP.title].title) || "（無題）",
        importance: (p[PROP.importance] && p[PROP.importance].select && p[PROP.importance].select.name) || "中",
        // 期限を過ぎてからの日数（持ち越し中なら元の期限から数える）
        late: daysBetween(original || due, today),
      });
    }
    cursor = data.has_more ? data.next_cursor : undefined;
  } while (cursor);

  // 重要度の高い順。同じなら遅れている日数が多い順
  return tasks.sort((a, b) => (ORDER[a.importance] ?? 1) - (ORDER[b.importance] ?? 1) || b.late - a.late);
}

function daysBetween(from, to) {
  return Math.round((new Date(`${to}T00:00:00`) - new Date(`${from}T00:00:00`)) / 86400000);
}

// ---------- ウィジェット ----------

function line(t) {
  return `${t.importance} ${t.title}`;
}

function buildRectangular(widget, tasks) {
  // 時計の下（3行ほど）。ロック画面は白黒なので、重要度は「高・中・低」の文字で表す
  widget.addAccessoryWidgetBackground = true;
  const head = widget.addText(tasks.length ? `今日のTodo ${tasks.length}件` : "今日のTodo");
  head.font = Font.boldSystemFont(13);
  head.lineLimit = 1;
  if (!tasks.length) {
    widget.addText("すべて完了").font = Font.systemFont(13);
    return;
  }
  const shown = tasks.slice(0, 2);
  shown.forEach((t, i) => {
    const rest = i === shown.length - 1 && tasks.length > shown.length ? ` 他${tasks.length - shown.length}` : "";
    const text = widget.addText(line(t) + rest);
    text.font = Font.systemFont(12);
    text.lineLimit = 1;
    text.minimumScaleFactor = 0.8;
  });
}

function buildInline(widget, tasks) {
  // 時計の上の1行
  widget.addText(tasks.length ? `Todo ${tasks.length}件 ▸ ${tasks[0].title}` : "Todo すべて完了");
}

function buildCircular(widget, tasks) {
  widget.addAccessoryWidgetBackground = true;
  widget.addSpacer();
  const count = widget.addText(String(tasks.length));
  count.font = Font.boldSystemFont(20);
  count.centerAlignText();
  const label = widget.addText("Todo");
  label.font = Font.systemFont(9);
  label.centerAlignText();
  widget.addSpacer();
}

function buildHome(widget, tasks, max) {
  // ホーム画面（小・中）。こちらは色が出るので、重要度を色付きの●で表す
  const head = widget.addText(`今日のTodo ${tasks.length}件`);
  head.font = Font.boldSystemFont(14);
  widget.addSpacer(6);
  if (!tasks.length) {
    widget.addText("すべて完了しました").font = Font.systemFont(13);
  }
  for (const t of tasks.slice(0, max)) {
    const row = widget.addStack();
    row.centerAlignContent();
    const dot = row.addText("● ");
    dot.font = Font.systemFont(11);
    dot.textColor = new Color(COLORS[t.importance] || COLORS["中"]);
    const title = row.addText(t.title);
    title.font = Font.systemFont(13);
    title.lineLimit = 1;
    if (t.late > 0) {
      row.addSpacer(4);
      const late = row.addText(`${t.late}日遅れ`);
      late.font = Font.systemFont(10);
      late.textColor = new Color(COLORS["高"]);
    }
    widget.addSpacer(3);
  }
  if (tasks.length > max) {
    const more = widget.addText(`ほか${tasks.length - max}件`);
    more.font = Font.systemFont(11);
    more.textColor = Color.gray();
  }
  widget.addSpacer();
}

function buildError(widget, family, error) {
  const text = family === "accessoryInline" ? "Todo: 読み込めません" : `Todoを読み込めません\n${error.message}`;
  const t = widget.addText(text);
  t.font = Font.systemFont(11);
  t.lineLimit = 3;
}

async function buildWidget(family) {
  const widget = new ListWidget();
  widget.refreshAfterDate = new Date(Date.now() + REFRESH_MINUTES * 60 * 1000);
  if (!Keychain.contains(KEY_TOKEN) || !Keychain.contains(KEY_DB)) {
    widget.addText(family === "accessoryInline" ? "Todo: 未設定" : "Scriptableでこのスクリプトを実行して設定してください").font =
      Font.systemFont(11);
    return widget;
  }
  const db = Keychain.get(KEY_DB);
  widget.url = `https://www.notion.so/${db}`;
  try {
    const tasks = await fetchToday(Keychain.get(KEY_TOKEN), db);
    if (family === "accessoryRectangular") buildRectangular(widget, tasks);
    else if (family === "accessoryInline") buildInline(widget, tasks);
    else if (family === "accessoryCircular") buildCircular(widget, tasks);
    else buildHome(widget, tasks, family === "small" ? 3 : 4);
  } catch (e) {
    buildError(widget, family, e);
  }
  return widget;
}

// ---------- 実行 ----------

if (config.runsInWidget || config.runsInAccessoryWidget) {
  Script.setWidget(await buildWidget(config.widgetFamily));
} else {
  // アプリ内で実行したとき: 未設定なら設定、設定済みなら設定し直すかプレビューを選ぶ
  let ready = Keychain.contains(KEY_TOKEN) && Keychain.contains(KEY_DB);
  if (!ready) {
    ready = await setup();
  } else {
    const menu = new Alert();
    menu.title = "todo-sync";
    menu.addAction("プレビュー");
    menu.addAction("トークン・DBを設定し直す");
    menu.addCancelAction("閉じる");
    const choice = await menu.presentAlert();
    if (choice === 1) ready = await setup();
    else if (choice === -1) ready = false;
  }
  if (ready) {
    await (await buildWidget("medium")).presentMedium();
  }
}
Script.complete();
