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

$id = (int)($_GET['id'] ?? 0);

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    $id = (int)($_POST['id'] ?? 0);
    $name = trim($_POST['name'] ?? '');
    $email = trim($_POST['email'] ?? '');

    if ($id > 0 && $name !== '' && $email !== '') {
        $stmt = $db->prepare('UPDATE users SET name = :name, email = :email WHERE id = :id');
        $stmt->execute([
            ':name' => $name,
            ':email' => $email,
            ':id' => $id,
        ]);
    }

    header('Location: list.php');
    exit;
}

$stmt = $db->prepare('SELECT id, name, email, image_path FROM users WHERE id = :id');
$stmt->execute([':id' => $id]);
$user = $stmt->fetch(PDO::FETCH_ASSOC);

if (!$user) {
    echo '対象データが見つかりません。';
    exit;
}
?>
<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <title>データ編集</title>
</head>
<body>
    <h1>登録データの編集</h1>
    <form action="edit.php" method="POST">
        <input type="hidden" name="id" value="<?php echo (int)$user['id']; ?>">
        <label>名前: <input type="text" name="name" value="<?php echo htmlspecialchars($user['name'], ENT_QUOTES, 'UTF-8'); ?>" required></label><br>
        <label>メール: <input type="email" name="email" value="<?php echo htmlspecialchars($user['email'], ENT_QUOTES, 'UTF-8'); ?>" required></label><br>
        <button type="submit">更新する</button>
    </form>
    <p><a href="list.php">一覧に戻る</a></p>
</body>
</html>
