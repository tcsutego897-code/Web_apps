<?php
$dbFile = __DIR__ . '/data.sqlite';
$db = new PDO('sqlite:' . $dbFile);
$db->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);

// 検索ワードを取得
$keyword = $_GET['keyword'] ?? '';

if ($keyword !== '') {
    // 検索ワードがある場合：LIKE検索（前方・後方一致）
    $stmt = $db->prepare('SELECT * FROM users WHERE name LIKE :keyword OR email LIKE :keyword');
    $stmt->execute([':keyword' => '%' . $keyword . '%']);
} else {
    // 検索ワードがない場合：全件取得
    $stmt = $db->query('SELECT * FROM users');
}

$users = $stmt->fetchAll(PDO::FETCH_ASSOC);
?>

<!-- 以下で $users をループしてテーブル等で表示してください -->
<ul>
    <?php foreach ($users as $user): ?>
        <li><?= htmlspecialchars($user['name']) ?> (<?= htmlspecialchars($user['email']) ?>)</li>
    <?php endforeach; ?>
</ul>