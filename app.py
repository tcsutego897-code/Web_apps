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
    conn.execute('PRAGMA foreign_keys = ON')
    return conn


def init_db():
    conn = get_db()
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nickname TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT,
            university TEXT,
            department TEXT,
            grade TEXT,
            role TEXT NOT NULL DEFAULT 'user',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        '''
    )
    user_columns = {row['name'] for row in conn.execute('PRAGMA table_info(users)')}
    for column, definition in (
        ('nickname', "TEXT NOT NULL DEFAULT '匿名ユーザー'"),
        ('password_hash', 'TEXT'),
        ('university', 'TEXT'),
        ('department', 'TEXT'),
        ('grade', 'TEXT'),
        ('role', "TEXT NOT NULL DEFAULT 'user'"),
    ):
        if column not in user_columns:
            conn.execute(f'ALTER TABLE users ADD COLUMN {column} {definition}')

    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            title TEXT NOT NULL,
            category TEXT NOT NULL,
            author TEXT NOT NULL,
            content TEXT NOT NULL,
            school_year TEXT,
            department TEXT,
            failure_type TEXT,
            cause TEXT,
            reflection TEXT,
            improvement_habit TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            is_deleted INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        '''
    )
    post_columns = {row['name'] for row in conn.execute('PRAGMA table_info(posts)')}
    for column, definition in (
        ('user_id', 'INTEGER'),
        ('school_year', 'TEXT'),
        ('department', 'TEXT'),
        ('failure_type', 'TEXT'),
        ('updated_at', 'TEXT'),
        ('is_deleted', 'INTEGER NOT NULL DEFAULT 0'),
    ):
        if column not in post_columns:
            conn.execute(f'ALTER TABLE posts ADD COLUMN {column} {definition}')
    if 'updated_at' not in post_columns:
        conn.execute('UPDATE posts SET updated_at = created_at WHERE updated_at IS NULL')

    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL,
            user_id INTEGER,
            author TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(post_id) REFERENCES posts(id) ON DELETE CASCADE,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        '''
    )
    comment_columns = {row['name'] for row in conn.execute('PRAGMA table_info(comments)')}
    if 'user_id' not in comment_columns:
        conn.execute('ALTER TABLE comments ADD COLUMN user_id INTEGER')

    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS likes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL,
            user_id INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(post_id, user_id),
            FOREIGN KEY(post_id) REFERENCES posts(id) ON DELETE CASCADE,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        '''
    )
    like_columns = {row['name'] for row in conn.execute('PRAGMA table_info(likes)')}
    if 'user_id' not in like_columns:
        conn.execute('ALTER TABLE likes RENAME TO likes_legacy')
        conn.execute(
            '''
            CREATE TABLE likes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id INTEGER NOT NULL,
                user_id INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(post_id, user_id),
                FOREIGN KEY(post_id) REFERENCES posts(id) ON DELETE CASCADE,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
            '''
        )
        conn.execute(
            'INSERT INTO likes (id, post_id, created_at) SELECT id, post_id, created_at FROM likes_legacy'
        )
        conn.execute('DROP TABLE likes_legacy')

    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER,
            user_id INTEGER,
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            status TEXT NOT NULL DEFAULT 'pending',
            FOREIGN KEY(post_id) REFERENCES posts(id) ON DELETE CASCADE,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        '''
    )
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL
        )
        '''
    )
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS post_tags (
            post_id INTEGER NOT NULL,
            tag_id INTEGER NOT NULL,
            PRIMARY KEY(post_id, tag_id),
            FOREIGN KEY(post_id) REFERENCES posts(id) ON DELETE CASCADE,
            FOREIGN KEY(tag_id) REFERENCES tags(id) ON DELETE CASCADE
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
