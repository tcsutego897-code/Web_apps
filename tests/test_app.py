import unittest

from app import app, init_db


class BoardAppTests(unittest.TestCase):
    def setUp(self):
        app.config['TESTING'] = True
        app.config['SECRET_KEY'] = 'test-secret'
        app.config['DB_PATH'] = ':memory:'
        with app.app_context():
            init_db()
        self.client = app.test_client()

    def test_index_page_loads(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('不合格', response.get_data(as_text=True))

    def test_create_post(self):
        response = self.client.post('/posts', data={
            'title': '第一志望に落ちた体験',
            'category': '大学受験',
            'author': 'Aさん',
            'content': '面接で緊張してしまい、話せませんでした。',
            'cause': '準備不足',
            'reflection': '毎日復習をした',
            'improvement_habit': '1日10分の振り返り',
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('第一志望に落ちた体験', response.get_data(as_text=True))

    def test_comment_and_like(self):
        self.client.post('/posts', data={
            'title': 'テスト投稿',
            'category': '就活',
            'author': 'Bさん',
            'content': '内容です',
            'cause': '原因',
            'reflection': '反省',
            'improvement_habit': '改善策',
        })

        comment_response = self.client.post('/posts/1/comments', data={'author': 'Cさん', 'content': '励まされます'} )
        self.assertEqual(comment_response.status_code, 302)

        like_response = self.client.post('/posts/1/like')
        self.assertEqual(like_response.status_code, 302)


if __name__ == '__main__':
    unittest.main()
