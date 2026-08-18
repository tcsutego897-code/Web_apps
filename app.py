from flask import Flask, request, redirect, url_for, render_template, send_from_directory, flash, Response
import sqlite3
import os
from werkzeug.utils import secure_filename
from datetime import datetime
import csv
import io
try:
    import openpyxl
    from openpyxl import Workbook
except Exception:
    openpyxl = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'data.sqlite')
UPLOAD_DIR = os.path.join(BASE_DIR, 'uploads')
ALLOWED_EXTENSIONS = {'jpg', 'jpeg', 'png', 'gif', 'webp'}
MAX_CONTENT_LENGTH = 2 * 1024 * 1024  # 2MB

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = UPLOAD_DIR
app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH
app.secret_key = 'change-this-secret'

if not os.path.isdir(UPLOAD_DIR):
    os.makedirs(UPLOAD_DIR, exist_ok=True)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT NOT NULL,
        image_path TEXT,
        created_at TEXT NOT NULL
    )''')
    conn.commit()
    return conn


def allowed_file(filename):
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    return ext in ALLOWED_EXTENSIONS


@app.route('/')
def index():
    keyword = request.args.get('keyword', '')
    return render_template('index.html', keyword=keyword)


@app.route('/register', methods=['POST'])
def register():
    name = (request.form.get('name') or '').strip()
    email = (request.form.get('email') or '').strip()

    if not name or not email:
        flash('名前とメールは必須です。')
        return redirect(url_for('index'))

    image_path = None
    file = request.files.get('image')
    if file and file.filename:
        filename = secure_filename(file.filename)
        if not allowed_file(filename):
            flash('許可されていないファイル形式です。')
            return redirect(url_for('index'))
        # size is already limited by MAX_CONTENT_LENGTH
        base, ext = os.path.splitext(filename)
        safe_base = ''.join(c if c.isalnum() or c in '._-' else '_' for c in base)
        stored = f"img_{int(datetime.now().timestamp())}_{safe_base[:8]}{ext}"
        dest = os.path.join(app.config['UPLOAD_FOLDER'], stored)
        file.save(dest)
        image_path = os.path.join('uploads', stored)

    conn = get_db()
    cur = conn.cursor()
    cur.execute('INSERT INTO users (name, email, image_path, created_at) VALUES (?, ?, ?, ?)',
                (name, email, image_path, datetime.now().isoformat()))
    conn.commit()
    conn.close()
    return redirect(url_for('list_users'))


@app.route('/list')
def list_users():
    conn = get_db()
    cur = conn.cursor()
    cur.execute('SELECT id, name, email, image_path FROM users ORDER BY id')
    users = cur.fetchall()
    conn.close()
    return render_template('list.html', users=users)


@app.route('/search')
def search():
    keyword = (request.args.get('keyword') or '').strip()
    conn = get_db()
    cur = conn.cursor()
    if keyword:
        like = f"%{keyword}%"
        cur.execute('SELECT * FROM users WHERE name LIKE ? OR email LIKE ? ORDER BY id', (like, like))
    else:
        cur.execute('SELECT * FROM users ORDER BY id')
    users = cur.fetchall()
    conn.close()
    return render_template('search.html', users=users)


@app.route('/edit', methods=['GET', 'POST'])
def edit():
    if request.method == 'POST':
        _id = int(request.form.get('id') or 0)
        name = (request.form.get('name') or '').strip()
        email = (request.form.get('email') or '').strip()
        if _id > 0 and name and email:
            conn = get_db()
            cur = conn.cursor()
            cur.execute('UPDATE users SET name = ?, email = ? WHERE id = ?', (name, email, _id))
            conn.commit()
            conn.close()
        return redirect(url_for('list_users'))
    else:
        _id = int(request.args.get('id') or 0)
        conn = get_db()
        cur = conn.cursor()
        cur.execute('SELECT id, name, email, image_path FROM users WHERE id = ?', (_id,))
        user = cur.fetchone()
        conn.close()
        if not user:
            return '対象データが見つかりません。', 404
        return render_template('edit.html', user=user)


@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


@app.route('/export')
def export():
    fmt = request.args.get('format', 'csv')
    conn = get_db()
    cur = conn.cursor()
    cur.execute('SELECT id, name, email, image_path, created_at FROM users ORDER BY id')
    rows = cur.fetchall()
    conn.close()

    if fmt == 'excel':
        if openpyxl is None:
            return 'Excel export requires openpyxl. Install it via requirements.', 500
        wb = Workbook()
        ws = wb.active
        ws.append(['ID', 'Name', 'Email', 'Image Path', 'Created At'])
        for r in rows:
            ws.append([r['id'], r['name'], r['email'], r['image_path'] or '', r['created_at']])
        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        return Response(output.read(), mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', headers={
            'Content-Disposition': 'attachment; filename=export.xlsx'
        })
    else:
        # CSV
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['ID', 'Name', 'Email', 'Image Path', 'Created At'])
        for r in rows:
            writer.writerow([r['id'], r['name'], r['email'], r['image_path'] or '', r['created_at']])
        output.seek(0)
        return Response(output.getvalue(), mimetype='text/csv', headers={
            'Content-Disposition': 'attachment; filename=export.csv'
        })


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000, debug=True)
