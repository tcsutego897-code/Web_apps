<?php
$dbFile = __DIR__ . '/data.sqlite';
$db = new PDO('sqlite:' . $dbFile);
$db->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);

$format = $_GET['format'] ?? 'csv';
$stmt = $db->query('SELECT id, name, email, image_path, created_at FROM users ORDER BY id');
$rows = $stmt->fetchAll(PDO::FETCH_ASSOC);

if ($format === 'excel') {
    header('Content-Type: application/vnd.ms-excel; charset=UTF-8');
    header('Content-Disposition: attachment; filename="users.xls"');
    echo "ID\t名前\tメール\t画像パス\t登録日時\n";
    foreach ($rows as $row) {
        echo implode("\t", [
            $row['id'],
            str_replace("\n", ' ', $row['name']),
            str_replace("\n", ' ', $row['email']),
            $row['image_path'] ?? '',
            $row['created_at'] ?? '',
        ]) . "\n";
    }
    exit;
}

header('Content-Type: text/csv; charset=UTF-8');
header('Content-Disposition: attachment; filename="users.csv"');

$fp = fopen('php://output', 'w');
fputcsv($fp, ['ID', '名前', 'メール', '画像パス', '登録日時']);
foreach ($rows as $row) {
    fputcsv($fp, [
        $row['id'],
        $row['name'],
        $row['email'],
        $row['image_path'] ?? '',
        $row['created_at'] ?? '',
    ]);
}
fclose($fp);
exit;
