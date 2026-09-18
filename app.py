from __future__ import annotations

import html
import json
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


HOST = "127.0.0.1"
PORT = 8000
DATA_FILE = Path(__file__).with_name("data") / "users.json"


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


class AppHandler(BaseHTTPRequestHandler):
    def send_html(self, content: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        encoded = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        pages = {"/": input_page, "/list": list_page, "/tax": tax_page, "/memo": memo_page}
        if path == "/style.css":
            try:
                content = Path(__file__).with_name("style.css").read_bytes()
            except OSError:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/css; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return
        if path in pages:
            self.send_html(pages[path]())
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/register":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        length = int(self.headers.get("Content-Length", "0"))
        fields = parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True)
        name = fields.get("name", [""])[0].strip()
        email = fields.get("email", [""])[0].strip()
        if not name or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            self.send_html(page("入力エラー", '<h1>入力エラー</h1><p>名前と正しいメールアドレスを入力してください。</p>'), HTTPStatus.BAD_REQUEST)
            return
        save_user(name, email)
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", "/list")
        self.end_headers()


if __name__ == "__main__":
    server = ThreadingHTTPServer((HOST, PORT), AppHandler)
    print(f"http://{HOST}:{PORT}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()