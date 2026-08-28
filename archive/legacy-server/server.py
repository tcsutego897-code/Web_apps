import http.server
import socketserver
import socket
import sqlite3
import os
import urllib.parse

PORT = 8000
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'data.sqlite')

CREATE_TABLE_SQL = '''
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    created_at TEXT NOT NULL
)
'''


def ensure_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(CREATE_TABLE_SQL)
    conn.commit()
    conn.close()


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == '/register.py' or parsed.path == '/register':
            self.handle_register()
        else:
            super().do_POST()

    def handle_register(self):
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length).decode('utf-8')
        params = urllib.parse.parse_qs(body)
        name = params.get('name', [''])[0].strip()
        email = params.get('email', [''])[0].strip()

        if not name or not email:
            self.send_response(303)
            self.send_header('Location', '/index.html?error=1')
            self.end_headers()
            return

        conn = sqlite3.connect(DB_PATH)
        conn.execute(CREATE_TABLE_SQL)
        conn.execute(
            'INSERT INTO users (name, email, created_at) VALUES (?, ?, datetime("now"))',
            (name, email),
        )
        conn.commit()
        conn.close()

        self.send_response(303)
        self.send_header('Location', '/list.html')
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == '/list.html':
            self.serve_list()
        else:
            super().do_GET()

    def serve_list(self):
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('SELECT id, name, email FROM users ORDER BY id')
        users = cursor.fetchall()
        conn.close()

        rows = ''
        if users:
            for user in users:
                rows += f'<tr>\n'
                rows += f'    <td>{user["id"]}</td>\n'
                rows += f'    <td>{html_escape(user["name"])}</td>\n'
                rows += f'    <td>{html_escape(user["email"])}</td>\n'
                rows += '</tr>\n'
        else:
            rows = '<tr><td colspan="3">まだ登録データがありません。</td></tr>\n'

        html = f'''<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <title>登録データ一覧</title>
    <style>
        table {{ border-collapse: collapse; width: 80%; margin: 20px 0; }}
        th, td {{ border: 1px solid #ccc; padding: 10px; text-align: left; }}
        th {{ background-color: #f4f4f4; }}
        .nav {{ margin-top: 20px; }}
    </style>
</head>
<body>
    <h1>登録ユーザー一覧</h1>
    <div class="nav">
        <a href="/index.html">新しいデータを追加する</a>
    </div>
    <table>
        <tr>
            <th>ID</th>
            <th>名前</th>
            <th>メールアドレス</th>
        </tr>
        {rows}
    </table>
</body>
</html>'''

        encoded = html.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def html_escape(value):
    return (value.replace('&', '&amp;')
                 .replace('<', '&lt;')
                 .replace('>', '&gt;')
                 .replace('"', '&quot;')
                 .replace("'", '&#39;'))


def get_local_ips():
    ips = set()
    try:
        # try primary outbound IP
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ips.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    try:
        # hostname resolution fallback
        hostname = socket.gethostname()
        for ip in socket.gethostbyname_ex(hostname)[2]:
            if not ip.startswith('127.'):
                ips.add(ip)
    except Exception:
        pass
    if not ips:
        ips.add('127.0.0.1')
    return sorted(ips)


if __name__ == '__main__':
    ensure_db()
    bind_addr = '0.0.0.0'
    with socketserver.TCPServer((bind_addr, PORT), Handler) as httpd:
        local_ips = get_local_ips()
        print(f'Listening on {bind_addr}:{PORT} (all interfaces)')
        for ip in local_ips:
            print(f'Access from LAN via: http://{ip}:{PORT}')
        print('Press Ctrl+C to stop.')
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print('\nServer stopped.')
