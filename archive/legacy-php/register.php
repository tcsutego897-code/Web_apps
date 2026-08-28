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

if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    header('Location: index.php');
    exit;
}

$name = trim($_POST['name'] ?? '');
$email = trim($_POST['email'] ?? '');

if ($name === '' || $email === '') {
    header('Location: index.php?error=1');
    exit;
}

$imagePath = null;
$uploadDir = __DIR__ . '/uploads';
if (!is_dir($uploadDir)) {
    mkdir($uploadDir, 0755, true);
}

if (isset($_FILES['image']) && $_FILES['image']['error'] === UPLOAD_ERR_OK) {
    $allowedExtensions = ['jpg', 'jpeg', 'png', 'gif', 'webp'];
    $allowedMimeTypes = ['image/jpeg', 'image/png', 'image/gif', 'image/webp'];
    $fileName = $_FILES['image']['name'];
    $tmpName = $_FILES['image']['tmp_name'];
    $size = (int)($_FILES['image']['size'] ?? 0);
    $extension = strtolower(pathinfo($fileName, PATHINFO_EXTENSION));
    $mimeType = $_FILES['image']['type'] ?? '';

    if ($size > 2 * 1024 * 1024 || !in_array($extension, $allowedExtensions, true) || !in_array($mimeType, $allowedMimeTypes, true)) {
        header('Location: index.php?error=2');
        exit;
    }

    $safeBaseName = preg_replace('/[^A-Za-z0-9._-]/', '_', pathinfo($fileName, PATHINFO_FILENAME));
    $storedFileName = 'img_' . time() . '_' . substr(md5($safeBaseName . microtime(true)), 0, 8) . '.' . $extension;
    $destination = $uploadDir . '/' . $storedFileName;

    if (!move_uploaded_file($tmpName, $destination)) {
        header('Location: index.php?error=3');
        exit;
    }

    $imagePath = 'uploads/' . $storedFileName;
}

$stmt = $db->prepare('INSERT INTO users (name, email, image_path, created_at) VALUES (:name, :email, :image_path, datetime("now"))');
$stmt->execute([
    ':name' => $name,
    ':email' => $email,
    ':image_path' => $imagePath,
]);

header('Location: list.php');
exit;
