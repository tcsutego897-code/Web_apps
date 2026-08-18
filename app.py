import os
import sqlite3
from datetime import datetime

from flask import Flask, abort, flash, redirect, render_template, request, url_for

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'data.sqlite')

app = Flask(__name__)
app.config['SECRET_KEY'] = 'dev-secret-key'
app.config['DATABASE'] = DB_PATH


def get_db():
    db_path = app.config.get('DATABASE', DB_PATH)
    if db_path == ':memory:':
        db_path = 'file:board_shared?mode=memory&cache=shared'
        conn = sqlite3.connect(db_path, uri=True)
    else:
        conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            category TEXT NOT NULL,
            author TEXT NOT NULL,
            content TEXT NOT NULL,
            cause TEXT,
            reflection TEXT,
            improvement_habit TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        '''
    )
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL,
            author TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(post_id) REFERENCES posts(id) ON DELETE CASCADE
        )
        '''
    )
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS likes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL UNIQUE,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        '''
    )
    conn.commit()
    conn.close()
    return True


@app.route('/')
def index():
    conn = get_db()
    posts = conn.execute(
        '''
        SELECT p.*, 
               (SELECT COUNT(*) FROM comments c WHERE c.post_id = p.id) AS comment_count,
               (SELECT COUNT(*) FROM likes l WHERE l.post_id = p.id) AS like_count
        FROM posts p
        ORDER BY p.id DESC
        '''
    ).fetchall()
    conn.close()
    return render_template('index.html', posts=posts)


@app.route('/posts/new')
def new_post():
    return render_template('post_form.html')


@app.route('/posts', methods=['POST'])
def create_post():
    title = (request.form.get('title') or '').strip()
    category = (request.form.get('category') or '').strip()
    author = (request.form.get('author') or '').strip()
    content = (request.form.get('content') or '').strip()
    cause = (request.form.get('cause') or '').strip()
    reflection = (request.form.get('reflection') or '').strip()
    improvement_habit = (request.form.get('improvement_habit') or '').strip()

    if not all([title, category, author, content]):
        flash('タイトル、カテゴリ、投稿者名、本文は必須です。')
        return redirect(url_for('new_post'))

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        '''
        INSERT INTO posts (title, category, author, content, cause, reflection, improvement_habit, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (title, category, author, content, cause, reflection, improvement_habit, datetime.now().isoformat()),
    )
    conn.commit()
    post_id = cursor.lastrowid
    conn.close()

    flash('投稿を保存しました。')
    return redirect(url_for('show_post', post_id=post_id))


@app.route('/posts/<int:post_id>')
def show_post(post_id):
    conn = get_db()
    post = conn.execute('SELECT * FROM posts WHERE id = ?', (post_id,)).fetchone()
    comments = conn.execute(
        'SELECT * FROM comments WHERE post_id = ? ORDER BY id DESC',
        (post_id,),
    ).fetchall()
    like_count = conn.execute('SELECT COUNT(*) FROM likes WHERE post_id = ?', (post_id,)).fetchone()[0]
    conn.close()

    if post is None:
        abort(404)

    return render_template('post_detail.html', post=post, comments=comments, like_count=like_count)


@app.route('/posts/<int:post_id>/comments', methods=['POST'])
def create_comment(post_id):
    author = (request.form.get('author') or '').strip()
    content = (request.form.get('content') or '').strip()

    if not author or not content:
        flash('コメントの名前と本文は必須です。')
        return redirect(url_for('show_post', post_id=post_id))

    conn = get_db()
    conn.execute(
        'INSERT INTO comments (post_id, author, content, created_at) VALUES (?, ?, ?, ?)',
        (post_id, author, content, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()

    flash('コメントを投稿しました。')
    return redirect(url_for('show_post', post_id=post_id))


@app.route('/posts/<int:post_id>/like', methods=['POST'])
def toggle_like(post_id):
    conn = get_db()
    existing = conn.execute('SELECT id FROM likes WHERE post_id = ?', (post_id,)).fetchone()
    if existing:
        conn.execute('DELETE FROM likes WHERE post_id = ?', (post_id,))
    else:
        conn.execute(
            'INSERT INTO likes (post_id, created_at) VALUES (?, ?)',
            (post_id, datetime.now().isoformat()),
        )
    conn.commit()
    conn.close()
    return redirect(url_for('show_post', post_id=post_id))


with app.app_context():
    init_db()


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000, debug=True)
