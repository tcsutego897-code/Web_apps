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

$keyword = $_GET['keyword'] ?? '';

if ($keyword !== '') {
    $stmt = $db->prepare('SELECT * FROM users WHERE name LIKE :keyword OR email LIKE :keyword ORDER BY id');
    $stmt->execute([':keyword' => '%' . $keyword . '%']);
} else {
    $stmt = $db->query('SELECT * FROM users ORDER BY id');
}

$users = $stmt->fetchAll(PDO::FETCH_ASSOC);
?>
<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <title>検索結果</title>
    <style>
        body { font-family: sans-serif; margin: 20px; }
        ul { list-style: none; padding: 0; }
        li { margin-bottom: 12px; }
        img { max-width: 160px; margin-top: 6px; display: block; }
    </style>
</head>
<body>
    <h1>検索結果</h1>
    <p><a href="index.php">戻る</a></p>
    <ul>
        <?php foreach ($users as $user): ?>
            <li>
                <strong><?= htmlspecialchars($user['name']) ?></strong> (<?= htmlspecialchars($user['email']) ?>)
                <?php if (!empty($user['image_path'])): ?>
                    <br>
                    <img src="<?= htmlspecialchars($user['image_path']) ?>" alt="アップロード画像">
                <?php endif; ?>
            </li>
        <?php endforeach; ?>
    </ul>
</body>
</html>