<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <title>データベース登録アプリ</title>
</head>
<body>
    <h1>新しいデータの追加</h1>
    <div class="nav">
        <a href="list.php">登録済みデータ一覧を見る</a>
    </div>
    <!-- データをサーバーに送信するためのフォーム（送信先を修正しました） -->
    <form action="register.php" method="POST">
        <label for="user_name">名前:</label>
        <input type="text" id="user_name" name="name" required>
        <br>
        <label for="user_email">メール:</label>
        <input type="email" id="user_email" name="email" required>
        <br>
        <!-- クリックするとデータが送信されるボタン -->
        <button type="submit">登録する</button>
    </form>
    <!-- 検索フォーム -->
    <form method="GET" action="search.php">
        <input type="text" name="keyword" placeholder="名前やメールで検索" value="<?= htmlspecialchars($_GET['keyword'] ?? '') ?>">
        <button type="submit">検索する</button>
        <a href="search.php">全件表示・リセット</a>
    </form>
</body>
</html>