<?php
$dbFile = __DIR__ . '/data.sqlite';
$db = new PDO('sqlite:' . $dbFile);
$db->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);

$db->exec('CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    image_path TEXT,
    created_at TEXT NOT NULL
)');

$columns = $db->query('PRAGMA table_info(users)')->fetchAll(PDO::FETCH_ASSOC);
$hasImageColumn = false;
foreach ($columns as $column) {
    if (($column['name'] ?? '') === 'image_path') {
        $hasImageColumn = true;
        break;
    }
}
if (!$hasImageColumn) {
    $db->exec('ALTER TABLE users ADD COLUMN image_path TEXT');
}

$stmt = $db->query('SELECT id, name, email, image_path FROM users ORDER BY id');
$users = $stmt->fetchAll(PDO::FETCH_ASSOC);
?>
<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <title>登録データ一覧</title>
    <style>
        table { border-collapse: collapse; width: 90%; margin: 20px 0; }
        th, td { border: 1px solid #ccc; padding: 10px; text-align: left; vertical-align: top; }
        th { background-color: #f4f4f4; }
        .no-data { margin-top: 20px; color: #555; }
        .nav { margin-top: 20px; }
        img { max-width: 180px; height: auto; display: block; }
    </style>
</head>
<body>
    <h1>登録ユーザー一覧</h1>
    <div class="nav">
        <a href="index.php">新しいデータを追加する</a>
    </div>
    <?php if (empty($users)): ?>
        <p class="no-data">まだ登録データがありません。</p>
    <?php else: ?>
        <table>
            <tr>
                <th>ID</th>
                <th>名前</th>
                <th>メールアドレス</th>
                <th>画像</th>
            </tr>
            <?php foreach ($users as $user): ?>
                <tr>
                    <td><?php echo htmlspecialchars($user['id'], ENT_QUOTES, 'UTF-8'); ?></td>
                    <td><?php echo htmlspecialchars($user['name'], ENT_QUOTES, 'UTF-8'); ?></td>
                    <td><?php echo htmlspecialchars($user['email'], ENT_QUOTES, 'UTF-8'); ?></td>
                    <td>
                        <?php if (!empty($user['image_path'])): ?>
                            <img src="<?php echo htmlspecialchars($user['image_path'], ENT_QUOTES, 'UTF-8'); ?>" alt="アップロード画像">
                        <?php else: ?>
                            なし
                        <?php endif; ?>
                    </td>
                </tr>
            <?php endforeach; ?>
        </table>
    <?php endif; ?>
</body>
</html>
