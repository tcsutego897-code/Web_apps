<?php
// SQLite を使った簡単なデータベース登録処理
$dbFile = __DIR__ . '/data.sqlite';
$db = new PDO('sqlite:' . $dbFile);
$db->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
$db->exec('CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    created_at TEXT NOT NULL
)');

if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    header('Location: index.html');
    exit;
}

$name = trim($_POST['name'] ?? '');
$email = trim($_POST['email'] ?? '');

if ($name === '' || $email === '') {
    header('Location: index.html?error=1');
    exit;
}

$stmt = $db->prepare('INSERT INTO users (name, email, created_at) VALUES (:name, :email, datetime("now"))');
$stmt->execute([
    ':name' => $name,
    ':email' => $email,
]);

header('Location: list.php');
exit;
