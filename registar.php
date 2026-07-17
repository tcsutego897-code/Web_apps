setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
        
        // テーブルがなければ自動作成
        $db->exec('CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            created_at TEXT NOT NULL
        )');

        // データベースにデータを挿入
        $stmt = $db->prepare('INSERT INTO users (name, email, created_at) VALUES (:name, :email, :created_at)');
        $stmt->execute([
            ':name' => $name,
            ':email' => $email,
            ':created_at' => date('Y-m-d H:i:s')
        ]);
    }
}

// 登録が終わったら自動で一覧ページにジャンプする
header('Location: list.php');
exit;