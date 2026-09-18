from __future__ import annotations

import html
import json
import os
import re
from pathlib import Path

from flask import Flask, redirect, request, send_file


HOST = "127.0.0.1"
PORT = 8000
DATA_FILE = Path(__file__).with_name("data") / "users.json"
app = Flask(__name__)


def page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <link rel="stylesheet" href="/style.css">
</head>
<body>
  <nav><a href="/">登録</a> | <a href="/list">一覧</a> | <a href="/tax">税計算</a> | <a href="/memo">メモ</a></nav>
  <main>{body}</main>
</body>
</html>"""


def load_users() -> list[dict[str, str | int]]:
    if not DATA_FILE.exists():
        return []
    try:
        with DATA_FILE.open(encoding="utf-8") as file:
            users = json.load(file)
        return users if isinstance(users, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def save_user(name: str, email: str) -> None:
    DATA_FILE.parent.mkdir(exist_ok=True)
    users = load_users()
    users.append({"id": len(users) + 1, "name": name, "email": email})
    with DATA_FILE.open("w", encoding="utf-8") as file:
        json.dump(users, file, ensure_ascii=False, indent=2)


def input_page() -> str:
    return page("データベース登録アプリ", """<h1>新しいデータの追加</h1>
<form action="/register" method="post">
  <label for="user_name">名前:</label>
  <input type="text" id="user_name" name="name" required>
  <label for="user_email">メール:</label>
  <input type="email" id="user_email" name="email" required>
  <button type="submit">登録する</button>
</form>""")


def list_page() -> str:
    rows = "".join(
        f"<tr><td>{user['id']}</td><td>{html.escape(str(user['name']))}</td>"
        f"<td>{html.escape(str(user['email']))}</td></tr>"
        for user in load_users()
    )
    if not rows:
        rows = '<tr><td colspan="3">登録データはありません。</td></tr>'
    return page("登録データ一覧", f"""<h1>登録ユーザー一覧</h1>
<table><thead><tr><th>ID</th><th>名前</th><th>メールアドレス</th></tr></thead>
<tbody>{rows}</tbody></table>""")


def tax_page() -> str:
    return page("Tax Calculator", """<h1>Tax Calculator</h1>
<form id="tax-form">
  <label for="amount">金額</label>
  <input type="number" min="0" step="any" id="amount" required>
  <label for="tax-rate">税率 (%)</label>
  <input type="number" min="0" step="any" id="tax-rate" required>
  <button type="submit">calculate</button>
</form>
<p id="tax-result" aria-live="polite"></p>
<script>
document.getElementById('tax-form').addEventListener('submit', function(event) {
  event.preventDefault();
  const amount = Number(document.getElementById('amount').value);
  const rate = Number(document.getElementById('tax-rate').value);
  document.getElementById('tax-result').textContent =
    '税込金額: ' + (amount * (1 + rate / 100)).toLocaleString('ja-JP');
});
</script>""")


def memo_page() -> str:
    return page("メモメモ", """<h1>メモメモ</h1>
<label for="memo-input">メモ</label>
<input id="memo-input" type="text">
<button type="button" id="memo-button">表示</button>
<div id="memo-output"></div>
<script>
document.getElementById('memo-button').addEventListener('click', function() {
  document.getElementById('memo-output').textContent =
    document.getElementById('memo-input').value;
});
</script>""")


@app.get("/")
def index() -> str:
    return input_page()


@app.get("/list")
def users() -> str:
    return list_page()


@app.get("/tax")
def tax() -> str:
    return tax_page()


@app.get("/memo")
def memo() -> str:
    return memo_page()


@app.get("/style.css")
def stylesheet():
    return send_file(Path(__file__).with_name("style.css"), mimetype="text/css")


@app.post("/register")
def register():
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip()
    if not name or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return page("入力エラー", '<h1>入力エラー</h1><p>名前と正しいメールアドレスを入力してください。</p>'), 400
    save_user(name, email)
    return redirect("/list", code=303)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", PORT)))