import os
import hmac
import io
import re
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta
from functools import wraps
from urllib.parse import urljoin, urlsplit

import click
from flask import Flask, abort, flash, redirect, render_template, request, send_from_directory, session, url_for
from PIL import Image, UnidentifiedImageError
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'data.sqlite')
configured_secret_key = os.environ.get('SECRET_KEY')
if os.environ.get('APP_ENV') == 'production' and not configured_secret_key:
    raise RuntimeError('本番環境ではSECRET_KEYを設定してください。')
MOOD_LABELS = {
    'frustrated': '悔しい',
    'anxious': '不安',
    'down': '落ち込んだ',
    'relieved': '安心',
    'hopeful': '前向き',
    'other': 'その他',
}

app = Flask(__name__)
app.config['SECRET_KEY'] = configured_secret_key or secrets.token_hex(32)
app.config['DATABASE'] = os.environ.get('DATABASE_PATH', DB_PATH)
app.config['UPLOAD_FOLDER'] = os.environ.get('UPLOAD_FOLDER', os.path.join(BASE_DIR, 'uploads'))
app.config['MAX_IMAGE_BYTES'] = 2 * 1024 * 1024
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'


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
        ('is_anonymous', 'INTEGER NOT NULL DEFAULT 1'),
        ('mood_at_event', 'TEXT'),
        ('mood_now', 'TEXT'),
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
        CREATE TABLE IF NOT EXISTS reactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            reaction_type TEXT NOT NULL CHECK (reaction_type IN ('empathy', 'useful', 'support')),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(post_id, user_id),
            FOREIGN KEY(post_id) REFERENCES posts(id) ON DELETE CASCADE,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        '''
    )
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS post_images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL UNIQUE,
            storage_key TEXT NOT NULL UNIQUE,
            image_format TEXT NOT NULL,
            byte_size INTEGER NOT NULL,
            alt_text TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(post_id) REFERENCES posts(id) ON DELETE CASCADE
        )
        '''
    )
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS login_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ip_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        '''
    )
    conn.execute('CREATE INDEX IF NOT EXISTS idx_login_attempts_ip_time ON login_attempts(ip_hash, created_at)')
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS badges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            badge_key TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            description TEXT NOT NULL,
            threshold INTEGER NOT NULL CHECK (threshold > 0)
        )
        '''
    )
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS user_badges (
            user_id INTEGER NOT NULL,
            badge_id INTEGER NOT NULL,
            awarded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(user_id, badge_id),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(badge_id) REFERENCES badges(id) ON DELETE CASCADE
        )
        '''
    )
    conn.executemany(
        'INSERT OR IGNORE INTO badges (badge_key, name, description, threshold) VALUES (?, ?, ?, ?)',
        (
            ('shared_step', '共有の一歩', '初めての経験共有が誰かの支えになりました。', 5),
            ('support_circle', '支え合いの輪', '多くの人に経験を届けました。', 20),
            ('experience_bridge', '経験をつなぐ', '経験を次の挑戦へつなぐ支援を続けています。', 50),
        ),
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


def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if session.get('user_id') is None:
            return redirect(url_for('login', next=request.path))
        return view(*args, **kwargs)

    return wrapped_view


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped_view(*args, **kwargs):
        conn = get_db()
        user = conn.execute('SELECT role FROM users WHERE id = ?', (session['user_id'],)).fetchone()
        conn.close()
        if user is None or user['role'] != 'admin':
            abort(403)
        return view(*args, **kwargs)

    return wrapped_view


@app.before_request
def protect_post_requests():
    if request.method != 'POST':
        return None

    expected = session.get('csrf_token')
    supplied = request.form.get('_csrf_token') or request.headers.get('X-CSRF-Token')
    if not expected or not supplied or not hmac.compare_digest(expected, supplied):
        abort(400)
    return None


@app.context_processor
def inject_request_context():
    csrf_token = session.setdefault('csrf_token', secrets.token_urlsafe(32))
    user = None
    user_id = session.get('user_id')
    if user_id is not None:
        conn = get_db()
        user = conn.execute(
            'SELECT id, nickname, email, role FROM users WHERE id = ?', (user_id,)
        ).fetchone()
        conn.close()
        if user is None:
            session.clear()
    return {'csrf_token': csrf_token, 'current_user': user, 'mood_labels': MOOD_LABELS}


def is_safe_redirect_target(target):
    if not target:
        return False
    ref_url = urlsplit(request.host_url)
    test_url = urlsplit(urljoin(request.host_url, target))
    return test_url.scheme in ('http', 'https') and ref_url.netloc == test_url.netloc


def validate_image_upload(upload, alt_text):
    if upload is None or not upload.filename:
        return None
    raw = upload.read(app.config['MAX_IMAGE_BYTES'] + 1)
    if len(raw) > app.config['MAX_IMAGE_BYTES']:
        abort(413)
    alt_text = (alt_text or '').strip()
    if not alt_text or len(alt_text) > 150:
        abort(400, '画像の説明は1〜150文字で入力してください。')

    try:
        with Image.open(io.BytesIO(raw)) as image:
            image_format = image.format
            if image_format not in ('JPEG', 'PNG', 'GIF'):
                abort(400, 'JPEG、PNG、GIF画像のみアップロードできます。')
            if image.width * image.height > 25_000_000:
                abort(400, '画像の解像度が上限を超えています。')
            image.seek(0)
            normalized = io.BytesIO()
            save_format = 'JPEG' if image_format == 'JPEG' else image_format
            if image_format == 'JPEG' and image.mode not in ('RGB', 'L'):
                image = image.convert('RGB')
            image.save(normalized, format=save_format)
    except (UnidentifiedImageError, OSError, ValueError):
        abort(400, '有効な画像ファイルを選択してください。')

    extension = {'JPEG': 'jpg', 'PNG': 'png', 'GIF': 'gif'}[image_format]
    return {
        'storage_key': f'{uuid.uuid4().hex}.{extension}',
        'image_format': image_format.lower(),
        'content': normalized.getvalue(),
        'alt_text': alt_text,
    }


def save_post_image(conn, post_id, image_data):
    if image_data is None:
        return None
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    image_path = os.path.join(app.config['UPLOAD_FOLDER'], image_data['storage_key'])
    try:
        with open(image_path, 'xb') as image_file:
            image_file.write(image_data['content'])
        conn.execute(
            '''INSERT INTO post_images
               (post_id, storage_key, image_format, byte_size, alt_text, created_at)
               VALUES (?, ?, ?, ?, ?, ?)''',
            (post_id, image_data['storage_key'], image_data['image_format'],
             len(image_data['content']), image_data['alt_text'], datetime.now().isoformat()),
        )
    except Exception:
        if os.path.exists(image_path):
            os.remove(image_path)
        raise
    return image_path


def refresh_user_badges(conn, user_id):
    supporter_count = conn.execute(
        '''SELECT COUNT(DISTINCT r.user_id)
           FROM reactions r JOIN posts p ON p.id = r.post_id
           WHERE p.user_id = ? AND p.is_deleted = 0 AND r.user_id != ?''',
        (user_id, user_id),
    ).fetchone()[0]
    badges = conn.execute('SELECT id, threshold FROM badges').fetchall()
    for badge in badges:
        if supporter_count >= badge['threshold']:
            conn.execute(
                'INSERT OR IGNORE INTO user_badges (user_id, badge_id) VALUES (?, ?)',
                (user_id, badge['id']),
            )
        else:
            conn.execute(
                'DELETE FROM user_badges WHERE user_id = ? AND badge_id = ?',
                (user_id, badge['id']),
            )


@app.cli.command('promote-admin')
@click.argument('email')
def promote_admin(email):
    """Grant administrator access to an existing account."""
    conn = get_db()
    cursor = conn.execute(
        "UPDATE users SET role = 'admin' WHERE email = ?", (email.strip().lower(),)
    )
    conn.commit()
    conn.close()
    if cursor.rowcount == 0:
        raise click.ClickException('指定されたメールアドレスのアカウントがありません。')
    click.echo('管理者権限を付与しました。')


@app.cli.command('set-legacy-password')
@click.argument('email')
def set_legacy_password(email):
    """Set a password for an existing account that has no password hash."""
    password = click.prompt('新しいパスワード（12文字以上）', hide_input=True, confirmation_prompt=True)
    if len(password) < 12:
        raise click.ClickException('パスワードは12文字以上にしてください。')
    conn = get_db()
    cursor = conn.execute(
        'UPDATE users SET password_hash = ? WHERE email = ? AND password_hash IS NULL',
        (generate_password_hash(password), email.strip().lower()),
    )
    conn.commit()
    conn.close()
    if cursor.rowcount == 0:
        raise click.ClickException('該当するパスワード未設定アカウントがありません。')
    click.echo('パスワードを設定しました。')


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'GET':
        return render_template('register.html')

    nickname = (request.form.get('nickname') or '').strip()
    email = (request.form.get('email') or '').strip().lower()
    password = request.form.get('password') or ''
    if not nickname or len(nickname) > 40:
        flash('ニックネームは1〜40文字で入力してください。')
        return render_template('register.html'), 400
    if not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', email):
        flash('有効なメールアドレスを入力してください。')
        return render_template('register.html'), 400
    if len(password) < 12:
        flash('パスワードは12文字以上にしてください。')
        return render_template('register.html'), 400

    conn = get_db()
    try:
        cursor = conn.execute(
            'INSERT INTO users (nickname, email, password_hash, role) VALUES (?, ?, ?, ?)',
            (nickname, email, generate_password_hash(password), 'user'),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        flash('このメールアドレスはすでに登録されています。')
        return render_template('register.html'), 400
    user_id = cursor.lastrowid
    conn.close()

    session.clear()
    session['user_id'] = user_id
    session['csrf_token'] = secrets.token_urlsafe(32)
    flash('アカウントを作成しました。')
    return redirect(url_for('index'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'GET':
        return render_template('login.html')

    email = (request.form.get('email') or '').strip().lower()
    password = request.form.get('password') or ''
    ip_hash = hmac.new(
        str(app.config['SECRET_KEY']).encode(),
        (request.remote_addr or 'unknown').encode(),
        'sha256',
    ).hexdigest()
    conn = get_db()
    cutoff = (datetime.now() - timedelta(minutes=15)).isoformat()
    conn.execute('DELETE FROM login_attempts WHERE created_at < ?', (cutoff,))
    attempt_count = conn.execute(
        'SELECT COUNT(*) FROM login_attempts WHERE ip_hash = ? AND created_at >= ?',
        (ip_hash, cutoff),
    ).fetchone()[0]
    if attempt_count >= 5:
        conn.close()
        abort(429)
    user = conn.execute(
        'SELECT id, password_hash FROM users WHERE email = ?', (email,)
    ).fetchone()
    if user is None or not user['password_hash'] or not check_password_hash(user['password_hash'], password):
        conn.execute(
            'INSERT INTO login_attempts (ip_hash, created_at) VALUES (?, ?)',
            (ip_hash, datetime.now().isoformat()),
        )
        conn.commit()
        conn.close()
        flash('メールアドレスまたはパスワードが正しくありません。')
        return render_template('login.html'), 400
    conn.execute('DELETE FROM login_attempts WHERE ip_hash = ?', (ip_hash,))
    conn.commit()
    conn.close()

    target = request.args.get('next')
    session.clear()
    session['user_id'] = user['id']
    session['csrf_token'] = secrets.token_urlsafe(32)
    return redirect(target if is_safe_redirect_target(target) else url_for('index'))


@app.route('/logout', methods=['POST'])
@login_required
def logout():
    session.clear()
    flash('ログアウトしました。')
    return redirect(url_for('index'))


@app.route('/')
def index():
    conn = get_db()
    posts = conn.execute(
        '''
        SELECT p.*, 
               (SELECT COUNT(*) FROM comments c WHERE c.post_id = p.id) AS comment_count,
               (SELECT COUNT(*) FROM likes l WHERE l.post_id = p.id) AS legacy_like_count,
               (SELECT COUNT(*) FROM reactions r WHERE r.post_id = p.id AND r.reaction_type = 'empathy') AS empathy_count,
               (SELECT COUNT(*) FROM reactions r WHERE r.post_id = p.id AND r.reaction_type = 'useful') AS useful_count,
               (SELECT COUNT(*) FROM reactions r WHERE r.post_id = p.id AND r.reaction_type = 'support') AS support_count
        FROM posts p
         WHERE p.is_deleted = 0
        ORDER BY p.id DESC
        '''
    ).fetchall()
    conn.close()
    return render_template('index.html', posts=posts)


@app.route('/mypage')
@login_required
def mypage():
    conn = get_db()
    posts = conn.execute(
        'SELECT id, title, created_at, is_anonymous FROM posts WHERE user_id = ? AND is_deleted = 0 ORDER BY id DESC',
        (session['user_id'],),
    ).fetchall()
    badges = conn.execute(
        '''SELECT b.name, b.description, ub.awarded_at FROM user_badges ub
           JOIN badges b ON b.id = ub.badge_id WHERE ub.user_id = ? ORDER BY b.threshold''',
        (session['user_id'],),
    ).fetchall()
    conn.close()
    return render_template('mypage.html', posts=posts, badges=badges)


@app.route('/posts/new')
@login_required
def new_post():
    return render_template('post_form.html')


@app.route('/posts', methods=['POST'])
@login_required
def create_post():
    title = (request.form.get('title') or '').strip()
    category = (request.form.get('category') or '').strip()
    anonymous = request.form.get('is_anonymous') == 'on'
    content = (request.form.get('content') or '').strip()
    cause = (request.form.get('cause') or '').strip()
    reflection = (request.form.get('reflection') or '').strip()
    improvement_habit = (request.form.get('improvement_habit') or '').strip()
    mood_at_event = request.form.get('mood_at_event') or None
    mood_now = request.form.get('mood_now') or None
    image_data = validate_image_upload(request.files.get('image'), request.form.get('image_alt'))

    if not all([title, category, content]):
        flash('タイトル、カテゴリ、本文は必須です。')
        return redirect(url_for('new_post'))
    if mood_at_event not in (None, *MOOD_LABELS) or mood_now not in (None, *MOOD_LABELS):
        abort(400)

    conn = get_db()
    user = conn.execute('SELECT id, nickname FROM users WHERE id = ?', (session['user_id'],)).fetchone()
    if user is None:
        conn.close()
        session.clear()
        return redirect(url_for('login'))
    author = '匿名ユーザー' if anonymous else user['nickname']
    cursor = conn.cursor()
    image_path = None
    cursor.execute(
        '''
        INSERT INTO posts (user_id, title, category, author, content, cause, reflection, improvement_habit, is_anonymous, mood_at_event, mood_now, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (user['id'], title, category, author, content, cause, reflection, improvement_habit,
         int(anonymous), mood_at_event, mood_now, datetime.now().isoformat()),
    )
    post_id = cursor.lastrowid
    try:
        image_path = save_post_image(conn, post_id, image_data)
        conn.commit()
    except Exception:
        conn.rollback()
        conn.close()
        if image_path and os.path.exists(image_path):
            os.remove(image_path)
        raise
    conn.close()

    flash('投稿を保存しました。')
    return redirect(url_for('show_post', post_id=post_id))


@app.route('/posts/<int:post_id>')
def show_post(post_id):
    conn = get_db()
    post = conn.execute('SELECT * FROM posts WHERE id = ? AND is_deleted = 0', (post_id,)).fetchone()
    if post is None:
        conn.close()
        abort(404)
    comments = conn.execute(
        'SELECT * FROM comments WHERE post_id = ? ORDER BY id DESC',
        (post_id,),
    ).fetchall()
    image = conn.execute(
        'SELECT storage_key, alt_text FROM post_images WHERE post_id = ?', (post_id,)
    ).fetchone()
    like_count = conn.execute('SELECT COUNT(*) FROM likes WHERE post_id = ?', (post_id,)).fetchone()[0]
    reaction_counts = {
        reaction_type: conn.execute(
            'SELECT COUNT(*) FROM reactions WHERE post_id = ? AND reaction_type = ?',
            (post_id, reaction_type),
        ).fetchone()[0]
        for reaction_type in ('empathy', 'useful', 'support')
    }
    my_reaction = None
    if session.get('user_id') is not None:
        row = conn.execute(
            'SELECT reaction_type FROM reactions WHERE post_id = ? AND user_id = ?',
            (post_id, session['user_id']),
        ).fetchone()
        my_reaction = row['reaction_type'] if row else None
    conn.close()

    return render_template(
        'post_detail.html', post=post, comments=comments, like_count=like_count,
        reaction_counts=reaction_counts, my_reaction=my_reaction, image=image,
    )


@app.route('/posts/<int:post_id>/comments', methods=['POST'])
@login_required
def create_comment(post_id):
    content = (request.form.get('content') or '').strip()

    if not content:
        flash('コメント本文は必須です。')
        return redirect(url_for('show_post', post_id=post_id))

    conn = get_db()
    user = conn.execute('SELECT id, nickname FROM users WHERE id = ?', (session['user_id'],)).fetchone()
    if user is None:
        conn.close()
        session.clear()
        return redirect(url_for('login'))
    conn.execute(
        'INSERT INTO comments (post_id, user_id, author, content, created_at) VALUES (?, ?, ?, ?, ?)',
        (post_id, user['id'], user['nickname'], content, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()

    flash('コメントを投稿しました。')
    return redirect(url_for('show_post', post_id=post_id))


@app.route('/posts/<int:post_id>/like', methods=['POST'])
@app.route('/posts/<int:post_id>/reactions', methods=['POST'])
@login_required
def toggle_reaction(post_id):
    reaction_type = request.form.get('reaction_type') or 'empathy'
    if reaction_type not in ('empathy', 'useful', 'support'):
        abort(400)
    conn = get_db()
    user_id = session['user_id']
    post = conn.execute(
        'SELECT user_id FROM posts WHERE id = ? AND is_deleted = 0', (post_id,)
    ).fetchone()
    if post is None:
        conn.close()
        abort(404)
    existing = conn.execute(
        'SELECT id, reaction_type FROM reactions WHERE post_id = ? AND user_id = ?',
        (post_id, user_id),
    ).fetchone()
    if existing and existing['reaction_type'] == reaction_type:
        conn.execute('DELETE FROM reactions WHERE id = ?', (existing['id'],))
    elif existing:
        conn.execute(
            'UPDATE reactions SET reaction_type = ?, created_at = ? WHERE id = ?',
            (reaction_type, datetime.now().isoformat(), existing['id']),
        )
    else:
        conn.execute(
            'INSERT INTO reactions (post_id, user_id, reaction_type, created_at) VALUES (?, ?, ?, ?)',
            (post_id, user_id, reaction_type, datetime.now().isoformat()),
        )
    if post['user_id'] is not None:
        refresh_user_badges(conn, post['user_id'])
    conn.commit()
    conn.close()
    return redirect(url_for('show_post', post_id=post_id))


@app.route('/posts/<int:post_id>/report', methods=['POST'])
@login_required
def report_post(post_id):
    reason = request.form.get('reason') or ''
    if reason not in ('personal_data', 'harassment', 'spam', 'other'):
        abort(400)
    conn = get_db()
    post = conn.execute(
        'SELECT id FROM posts WHERE id = ? AND is_deleted = 0', (post_id,)
    ).fetchone()
    if post is None:
        conn.close()
        abort(404)
    pending = conn.execute(
        "SELECT id FROM reports WHERE post_id = ? AND user_id = ? AND status = 'pending'",
        (post_id, session['user_id']),
    ).fetchone()
    if pending is None:
        conn.execute(
            'INSERT INTO reports (post_id, user_id, reason, created_at) VALUES (?, ?, ?, ?)',
            (post_id, session['user_id'], reason, datetime.now().isoformat()),
        )
        conn.commit()
    conn.close()
    flash('通報を受け付けました。')
    return redirect(url_for('show_post', post_id=post_id))


@app.route('/admin')
@admin_required
def admin_dashboard():
    conn = get_db()
    reports = conn.execute(
        '''SELECT r.id, r.post_id, r.reason, r.created_at, r.status, p.title
           FROM reports r LEFT JOIN posts p ON p.id = r.post_id
           ORDER BY r.id DESC'''
    ).fetchall()
    comments = conn.execute(
        '''SELECT c.id, c.author, c.content, c.post_id, p.title
           FROM comments c JOIN posts p ON p.id = c.post_id
           ORDER BY c.id DESC LIMIT 100'''
    ).fetchall()
    conn.close()
    return render_template('admin.html', reports=reports, comments=comments)


@app.route('/admin/reports/<int:report_id>/resolve', methods=['POST'])
@admin_required
def resolve_report(report_id):
    conn = get_db()
    cursor = conn.execute(
        "UPDATE reports SET status = 'reviewed' WHERE id = ? AND status = 'pending'",
        (report_id,),
    )
    conn.commit()
    conn.close()
    if cursor.rowcount == 0:
        abort(404)
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/posts/<int:post_id>/delete', methods=['POST'])
@admin_required
def admin_delete_post(post_id):
    conn = get_db()
    image = conn.execute(
        'SELECT storage_key FROM post_images WHERE post_id = ?', (post_id,)
    ).fetchone()
    cursor = conn.execute(
        'UPDATE posts SET is_deleted = 1, updated_at = ? WHERE id = ? AND is_deleted = 0',
        (datetime.now().isoformat(), post_id),
    )
    if cursor.rowcount:
        conn.execute('DELETE FROM post_images WHERE post_id = ?', (post_id,))
    conn.commit()
    conn.close()
    if cursor.rowcount == 0:
        abort(404)
    if image:
        image_path = os.path.join(app.config['UPLOAD_FOLDER'], image['storage_key'])
        if os.path.exists(image_path):
            os.remove(image_path)
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/comments/<int:comment_id>/delete', methods=['POST'])
@admin_required
def admin_delete_comment(comment_id):
    conn = get_db()
    cursor = conn.execute('DELETE FROM comments WHERE id = ?', (comment_id,))
    conn.commit()
    conn.close()
    if cursor.rowcount == 0:
        abort(404)
    return redirect(url_for('admin_dashboard'))


@app.route('/posts/<int:post_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_post(post_id):
    conn = get_db()
    post = conn.execute(
        'SELECT * FROM posts WHERE id = ? AND user_id = ? AND is_deleted = 0',
        (post_id, session['user_id']),
    ).fetchone()
    image = conn.execute(
        'SELECT storage_key, alt_text FROM post_images WHERE post_id = ?', (post_id,)
    ).fetchone()
    conn.close()
    if post is None:
        abort(404)
    post = dict(post)
    post['image_storage_key'] = image['storage_key'] if image else None
    post['image_alt'] = image['alt_text'] if image else None
    if request.method == 'GET':
        return render_template('post_form.html', post=post, editing=True)

    title = (request.form.get('title') or '').strip()
    category = (request.form.get('category') or '').strip()
    content = (request.form.get('content') or '').strip()
    if not all((title, category, content)):
        flash('タイトル、カテゴリ、本文は必須です。')
        return render_template('post_form.html', post=post, editing=True), 400

    anonymous = request.form.get('is_anonymous') == 'on'
    mood_at_event = request.form.get('mood_at_event') or None
    mood_now = request.form.get('mood_now') or None
    if mood_at_event not in (None, *MOOD_LABELS) or mood_now not in (None, *MOOD_LABELS):
        abort(400)
    image_data = validate_image_upload(request.files.get('image'), request.form.get('image_alt'))
    conn = get_db()
    user = conn.execute('SELECT nickname FROM users WHERE id = ?', (session['user_id'],)).fetchone()
    replace_image = image_data is not None or request.form.get('remove_image') == 'on'
    if replace_image:
        conn.execute('DELETE FROM post_images WHERE post_id = ?', (post_id,))
    conn.execute(
          '''UPDATE posts SET title = ?, category = ?, author = ?, content = ?, cause = ?,
              reflection = ?, improvement_habit = ?, is_anonymous = ?, mood_at_event = ?, mood_now = ?, updated_at = ?
           WHERE id = ? AND user_id = ?''',
        (title, category, '匿名ユーザー' if anonymous else user['nickname'], content,
         (request.form.get('cause') or '').strip(), (request.form.get('reflection') or '').strip(),
            (request.form.get('improvement_habit') or '').strip(), int(anonymous), mood_at_event, mood_now,
         datetime.now().isoformat(), post_id, session['user_id']),
    )
    image_path = None
    try:
        image_path = save_post_image(conn, post_id, image_data)
        conn.commit()
    except Exception:
        conn.rollback()
        conn.close()
        if image_path and os.path.exists(image_path):
            os.remove(image_path)
        raise
    conn.close()
    if replace_image and image:
        old_image_path = os.path.join(app.config['UPLOAD_FOLDER'], image['storage_key'])
        if os.path.exists(old_image_path):
            os.remove(old_image_path)
    return redirect(url_for('show_post', post_id=post_id))


@app.route('/posts/<int:post_id>/delete', methods=['POST'])
@login_required
def delete_post(post_id):
    conn = get_db()
    image = conn.execute(
        'SELECT storage_key FROM post_images WHERE post_id = ?', (post_id,)
    ).fetchone()
    cursor = conn.execute(
        'UPDATE posts SET is_deleted = 1, updated_at = ? WHERE id = ? AND user_id = ?',
        (datetime.now().isoformat(), post_id, session['user_id']),
    )
    if cursor.rowcount:
        conn.execute('DELETE FROM post_images WHERE post_id = ?', (post_id,))
    conn.commit()
    conn.close()
    if cursor.rowcount == 0:
        abort(404)
    if image:
        image_path = os.path.join(app.config['UPLOAD_FOLDER'], image['storage_key'])
        if os.path.exists(image_path):
            os.remove(image_path)
    flash('投稿を削除しました。')
    return redirect(url_for('index'))


@app.route('/uploads/<path:filename>')
def uploaded_image(filename):
    if not re.fullmatch(r'[0-9a-f]{32}\.(jpg|png|gif)', filename):
        abort(404)
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename, max_age=3600)


with app.app_context():
    init_db()


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000, debug=True)
