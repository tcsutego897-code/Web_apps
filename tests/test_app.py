import io
import unittest
import sqlite3
import tempfile
from pathlib import Path

from PIL import Image

from app import app, init_db


class BoardAppTests(unittest.TestCase):
    def setUp(self):
        app.config['TESTING'] = True
        app.config['SECRET_KEY'] = 'test-secret'
        self.original_database = app.config.get('DATABASE')
        self.original_upload_folder = app.config.get('UPLOAD_FOLDER')
        self.temp_dir = tempfile.TemporaryDirectory()
        app.config['DATABASE'] = str(Path(self.temp_dir.name) / 'test.sqlite')
        app.config['UPLOAD_FOLDER'] = str(Path(self.temp_dir.name) / 'uploads')
        with app.app_context():
            init_db()
        self.client = app.test_client()

    def tearDown(self):
        if self.original_database is None:
            app.config.pop('DATABASE', None)
        else:
            app.config['DATABASE'] = self.original_database
        app.config['UPLOAD_FOLDER'] = self.original_upload_folder
        self.temp_dir.cleanup()

    def post_with_csrf(self, path, data, **kwargs):
        with self.client.session_transaction() as session:
            data['_csrf_token'] = session['csrf_token']
        return self.client.post(path, data=data, **kwargs)

    def register_user(self, email='user@example.com'):
        self.client.get('/register')
        response = self.post_with_csrf('/register', {
            'nickname': 'テスト利用者',
            'email': email,
            'password': 'correct-horse-battery',
        })
        self.assertEqual(response.status_code, 302)
        return response

    def test_index_page_loads(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('不合格', response.get_data(as_text=True))

    def test_create_post(self):
        self.register_user()
        response = self.post_with_csrf('/posts', data={
            'title': '第一志望に落ちた体験',
            'category': '大学受験',
            'content': '面接で緊張してしまい、話せませんでした。',
            'cause': '準備不足',
            'reflection': '毎日復習をした',
            'improvement_habit': '1日10分の振り返り',
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('第一志望に落ちた体験', response.get_data(as_text=True))

    def test_comment_and_like(self):
        self.register_user()
        self.post_with_csrf('/posts', data={
            'title': 'テスト投稿',
            'category': '就活',
            'content': '内容です',
            'cause': '原因',
            'reflection': '反省',
            'improvement_habit': '改善策',
        })

        comment_response = self.post_with_csrf('/posts/1/comments', data={'content': '励まされます'})
        self.assertEqual(comment_response.status_code, 302)

        like_response = self.post_with_csrf('/posts/1/like', data={})
        self.assertEqual(like_response.status_code, 302)
        detail = self.client.get('/posts/1').get_data(as_text=True)
        self.assertIn('共感 1', detail)

    def test_reaction_can_change_and_be_removed(self):
        self.register_user()
        self.post_with_csrf('/posts', {
            'title': 'リアクション対象', 'category': '学業', 'content': '内容',
        })
        self.post_with_csrf('/posts/1/reactions', {'reaction_type': 'useful'})
        self.post_with_csrf('/posts/1/reactions', {'reaction_type': 'support'})
        conn = sqlite3.connect(app.config['DATABASE'])
        self.assertEqual(conn.execute('SELECT COUNT(*) FROM reactions').fetchone()[0], 1)
        self.assertEqual(conn.execute('SELECT reaction_type FROM reactions').fetchone()[0], 'support')
        conn.close()
        self.post_with_csrf('/posts/1/reactions', {'reaction_type': 'support'})
        conn = sqlite3.connect(app.config['DATABASE'])
        self.assertEqual(conn.execute('SELECT COUNT(*) FROM reactions').fetchone()[0], 0)
        conn.close()

    def test_public_read_and_authenticated_write(self):
        self.assertEqual(self.client.get('/').status_code, 200)
        response = self.client.get('/posts/new')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers['Location'])

    def test_registration_hashes_password_and_anonymous_post_hides_nickname(self):
        self.register_user()
        conn = sqlite3.connect(app.config['DATABASE'])
        password_hash = conn.execute('SELECT password_hash FROM users').fetchone()[0]
        conn.close()
        self.assertNotEqual(password_hash, 'correct-horse-battery')

        response = self.post_with_csrf('/posts', {
            'title': '匿名の体験談',
            'category': '就活',
            'content': '本文です',
            'is_anonymous': 'on',
        },)
        self.assertEqual(response.status_code, 302)
        page = self.client.get('/posts/1').get_data(as_text=True)
        self.assertIn('匿名ユーザー', page)
        self.assertNotIn('テスト利用者', page)

    def test_post_content_is_html_escaped(self):
        self.register_user()
        self.post_with_csrf('/posts', {
            'title': '安全表示',
            'category': '学業',
            'content': '<script>alert(1)</script>',
        })
        response = self.client.get('/posts/1')
        self.assertIn('&lt;script&gt;', response.get_data(as_text=True))
        self.assertNotIn('<script>alert(1)</script>', response.get_data(as_text=True))

    def test_moods_are_optional_and_render_as_labeled_values(self):
        self.register_user()
        self.post_with_csrf('/posts', {
            'title': '気持ちの記録', 'category': '就活', 'content': '体験談',
            'mood_at_event': 'anxious', 'mood_now': 'hopeful',
        })
        page = self.client.get('/posts/1').get_data(as_text=True)
        self.assertIn('当時: 不安', page)
        self.assertIn('現在: 前向き', page)

    def test_invalid_mood_is_rejected(self):
        self.register_user()
        response = self.post_with_csrf('/posts', {
            'title': '不正な気分', 'category': '学業', 'content': '体験談',
            'mood_at_event': 'not-a-mood',
        })
        self.assertEqual(response.status_code, 400)

    def test_image_is_validated_saved_and_served(self):
        self.register_user()
        image_bytes = io.BytesIO()
        Image.new('RGB', (3, 3), color='teal').save(image_bytes, format='PNG')
        image_bytes.seek(0)
        response = self.post_with_csrf('/posts', {
            'title': '画像付き投稿',
            'category': '学業',
            'content': '画像の説明を添付します。',
            'image_alt': '青緑色の小さな四角',
            'image': (image_bytes, 'upload.png'),
        }, content_type='multipart/form-data')
        self.assertEqual(response.status_code, 302)

        conn = sqlite3.connect(app.config['DATABASE'])
        storage_key = conn.execute('SELECT storage_key FROM post_images').fetchone()[0]
        conn.close()
        image_path = Path(app.config['UPLOAD_FOLDER']) / storage_key
        self.assertTrue(image_path.is_file())
        detail = self.client.get('/posts/1')
        self.assertIn('青緑色の小さな四角', detail.get_data(as_text=True))
        served = self.client.get(f'/uploads/{storage_key}')
        self.assertEqual(served.status_code, 200)
        self.assertEqual(served.mimetype, 'image/png')
        served.close()

        self.post_with_csrf('/posts/1/delete', {})
        self.assertFalse(image_path.exists())

    def test_non_image_disguised_as_png_is_rejected(self):
        self.register_user()
        response = self.post_with_csrf('/posts', {
            'title': '不正画像',
            'category': '学業',
            'content': 'これは画像ではありません。',
            'image_alt': '偽装画像',
            'image': (io.BytesIO(b'not an image'), 'fake.png'),
        }, content_type='multipart/form-data')
        self.assertEqual(response.status_code, 400)

    def test_login_is_rate_limited_after_five_failures(self):
        self.client.get('/login')
        for attempt in range(5):
            response = self.post_with_csrf('/login', {
                'email': f'wrong{attempt}@example.com',
                'password': 'wrong-password',
            })
            self.assertEqual(response.status_code, 400)
        response = self.post_with_csrf('/login', {
            'email': 'another@example.com', 'password': 'wrong-password',
        })
        self.assertEqual(response.status_code, 429)

    def test_reports_are_reviewed_only_by_admin(self):
        self.register_user(email='admin@example.com')
        conn = sqlite3.connect(app.config['DATABASE'])
        conn.execute("UPDATE users SET role = 'admin' WHERE email = 'admin@example.com'")
        conn.commit()
        conn.close()
        self.post_with_csrf('/posts', {
            'title': '確認対象', 'category': '就活', 'content': '投稿本文',
        })
        response = self.post_with_csrf('/posts/1/report', {'reason': 'personal_data'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get('/admin').status_code, 200)
        response = self.post_with_csrf('/admin/reports/1/resolve', {})
        self.assertEqual(response.status_code, 302)
        conn = sqlite3.connect(app.config['DATABASE'])
        status = conn.execute('SELECT status FROM reports WHERE id = 1').fetchone()[0]
        conn.close()
        self.assertEqual(status, 'reviewed')

    def test_non_admin_cannot_open_admin_dashboard(self):
        self.register_user()
        self.assertEqual(self.client.get('/admin').status_code, 403)

    def test_anonymous_admin_access_redirects_to_login(self):
        response = self.client.get('/admin')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers['Location'])

    def test_login_logout_and_csrf_protection(self):
        self.client.get('/register')
        response = self.client.post('/register', data={
            'nickname': '利用者',
            'email': 'csrf@example.com',
            'password': 'correct-horse-battery',
        })
        self.assertEqual(response.status_code, 400)

        self.register_user(email='login@example.com')
        self.post_with_csrf('/logout', {})
        self.assertEqual(self.client.get('/mypage').status_code, 302)
        self.client.get('/login')
        response = self.post_with_csrf('/login', {
            'email': 'login@example.com',
            'password': 'correct-horse-battery',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get('/mypage').status_code, 200)

    def test_only_owner_can_edit_post(self):
        self.register_user(email='owner@example.com')
        self.post_with_csrf('/posts', {
            'title': '作成者の投稿', 'category': '学業', 'content': '本文',
            'is_anonymous': '',
        })
        other_client = app.test_client()
        other_client.get('/register')
        with other_client.session_transaction() as session:
            csrf_token = session['csrf_token']
        other_client.post('/register', data={
            '_csrf_token': csrf_token,
            'nickname': '別の利用者',
            'email': 'other@example.com',
            'password': 'another-correct-password',
        })
        self.assertEqual(other_client.get('/posts/1/edit').status_code, 404)

        self.assertEqual(self.client.get('/posts/1/edit').status_code, 200)
        response = self.post_with_csrf('/posts/1/edit', {
            'title': '更新された投稿', 'category': '学業', 'content': '更新後の本文',
            'is_anonymous': '',
        })
        self.assertEqual(response.status_code, 302)
        self.assertIn('更新された投稿', self.client.get('/posts/1').get_data(as_text=True))

    def test_legacy_like_rows_survive_idempotent_migration(self):
        legacy_database = str(Path(self.temp_dir.name) / 'legacy.sqlite')
        conn = sqlite3.connect(legacy_database)
        conn.execute(
            '''CREATE TABLE posts (
                id INTEGER PRIMARY KEY, title TEXT NOT NULL, category TEXT NOT NULL,
                author TEXT NOT NULL, content TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )'''
        )
        conn.execute(
            "INSERT INTO posts (id, title, category, author, content) VALUES (1, '旧投稿', '学業', '匿名', '本文')"
        )
        conn.execute(
            '''CREATE TABLE likes (
                id INTEGER PRIMARY KEY, post_id INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )'''
        )
        conn.execute('INSERT INTO likes (id, post_id) VALUES (1, 1)')
        conn.commit()
        conn.close()

        app.config['DATABASE'] = legacy_database
        try:
            with app.app_context():
                init_db()
                init_db()
            conn = sqlite3.connect(legacy_database)
            like_rows = conn.execute('SELECT COUNT(*), SUM(user_id IS NULL) FROM likes').fetchone()
            post_anonymous = conn.execute('SELECT is_anonymous FROM posts WHERE id = 1').fetchone()[0]
            conn.close()
            self.assertEqual(like_rows, (1, 1))
            self.assertEqual(post_anonymous, 1)
        finally:
            app.config['DATABASE'] = str(Path(self.temp_dir.name) / 'test.sqlite')

    def test_cli_can_promote_admin_and_set_legacy_password(self):
        conn = sqlite3.connect(app.config['DATABASE'])
        conn.execute(
            "INSERT INTO users (nickname, email, password_hash) VALUES ('旧利用者', 'legacy@example.com', NULL)"
        )
        conn.commit()
        conn.close()

        runner = app.test_cli_runner()
        password_result = runner.invoke(
            args=['set-legacy-password', 'legacy@example.com'],
            input='correct-horse-battery\ncorrect-horse-battery\n',
        )
        self.assertEqual(password_result.exit_code, 0, password_result.output)
        admin_result = runner.invoke(args=['promote-admin', 'legacy@example.com'])
        self.assertEqual(admin_result.exit_code, 0, admin_result.output)
        conn = sqlite3.connect(app.config['DATABASE'])
        password_hash, role = conn.execute(
            'SELECT password_hash, role FROM users WHERE email = ?', ('legacy@example.com',)
        ).fetchone()
        conn.close()
        self.assertNotEqual(password_hash, 'correct-horse-battery')
        self.assertEqual(role, 'admin')

    def test_badge_is_awarded_and_revoked_with_unique_reaction(self):
        self.register_user(email='author@example.com')
        self.post_with_csrf('/posts', {
            'title': '支えになった投稿', 'category': '学業', 'content': '本文',
        })
        conn = sqlite3.connect(app.config['DATABASE'])
        conn.execute("UPDATE badges SET threshold = 1 WHERE badge_key = 'shared_step'")
        conn.commit()
        conn.close()

        supporter = app.test_client()
        supporter.get('/register')
        with supporter.session_transaction() as session:
            csrf_token = session['csrf_token']
        response = supporter.post('/register', data={
            '_csrf_token': csrf_token,
            'nickname': '応援者',
            'email': 'supporter@example.com',
            'password': 'another-correct-password',
        })
        self.assertEqual(response.status_code, 302)
        supporter.get('/posts/1')
        with supporter.session_transaction() as session:
            csrf_token = session['csrf_token']
        supporter.post('/posts/1/reactions', data={
            '_csrf_token': csrf_token, 'reaction_type': 'empathy',
        })
        self.assertIn('共有の一歩', self.client.get('/mypage').get_data(as_text=True))

        with supporter.session_transaction() as session:
            csrf_token = session['csrf_token']
        supporter.post('/posts/1/reactions', data={
            '_csrf_token': csrf_token, 'reaction_type': 'empathy',
        })
        self.assertNotIn('共有の一歩', self.client.get('/mypage').get_data(as_text=True))


if __name__ == '__main__':
    unittest.main()
