# Akashic Record

FastAPIで作る、Excelデータ検索・閲覧アプリです。フェーズ1〜3のアプリ基盤、DBスキーマ、`.xlsx` プレビュー・取り込みに加え、フェーズ4のデータ検索・閲覧を実装しています。ログインと権限制御はまだ未実装です。

## 必要なもの

- Python 3.12
- PostgreSQL 16以降（ローカル、または接続可能なRender PostgreSQL）

仮想環境を作らず、現在のPython環境に依存関係をインストールします。

## セットアップ（Windows PowerShell）

1. PostgreSQLに `akashic_record` データベースを作成します。
2. 設定ファイルを作成し、接続先を必要に応じて変更します。

   ```powershell
   Copy-Item .env.example .env
   ```

3. Pythonパッケージをインストールします。

   ```powershell
   python -m pip install -r requirements.txt
   ```

4. データベースのマイグレーションを適用します。

   ```powershell
   python -m alembic upgrade head
   ```

5. 開発サーバーを起動します。

   ```powershell
   python -m uvicorn app.main:app --reload
   ```

   ブラウザーで `http://127.0.0.1:8000` を開きます。

開発用テストを実行する場合は `python -m pip install -r requirements-dev.txt` の後に `python -m pytest` を実行してください。

## 画面と確認用エンドポイント

- `/` — DBに保存されたデータセット一覧
- `/search` — データセットの選択、項目別の部分一致・完全一致、複数条件検索、並べ替え、ページ分割、代表項目の一覧
- `/datasets/{dataset_id}/records/{record_id}` — 検索結果から開くレコード全項目の詳細
- `/import` — `.xlsx` のシート選択・見出し確認・プレビュー・取り込み
- `/login` — ログイン画面の骨組み
- `/health/live` — アプリの起動確認（DB接続不要）
- `/health/ready` — PostgreSQL接続確認。接続できない場合は `503` を返します。

設定は環境変数から読み込みます。`.env` を使う場合は `DATABASE_URL` にSQLAlchemy形式のURLを指定してください（例: `postgresql+psycopg://user:password@host:5432/database`）。`.env` はGitの追跡対象外です。

### Excel取り込み

1. `/import` で `.xlsx` ファイルと見出し行を指定します。
2. 表示シートを選び、先頭5件のプレビューと警告を確認します。
3. 必要に応じてデータセット名・項目の表示名を変更し、取り込みを確定します。
4. 成功すると、取り込み件数が表示され、データセット一覧に追加されます。

ファイルはプレビューから確定までシステムの一時フォルダーに短時間保管し、取り込み成功後に削除します。未確定のファイルも30分後に期限切れとなり、次のアップロード時に削除されます。元ファイルをアプリの永続データとして保存しません。数式は実行せず、保存済みの計算結果だけを使用します。結果がない数式は警告付きの空欄、全セルが空の行は除外し、Excel上の行番号を記録します。非表示シートは選択肢から除外し、結合セルは左上セルだけを使います。行の値に問題がある場合は元のExcel行番号とエラー内容を取り込み履歴に記録します。失敗時はデータセットとレコードをまとめてロールバックし、成功扱いにしません。

`.env.example` の `MAX_UPLOAD_SIZE_BYTES`（初期値10 MiB）、`MAX_WORKBOOK_UNCOMPRESSED_BYTES`（50 MiB）、`MAX_IMPORT_ROWS`（50,000件）は安全のための初期値です。計画書にある通り、実データの見本や本番環境を確認してから必要に応じて調整してください。

現段階ではログイン・権限制御が未実装です。外部公開せず、ローカル開発環境で使用してください。公開前にフェーズ7のアクセス制御を実装する必要があります。

## ディレクトリ構成

```text
app/
  config.py       環境設定
  database.py     SQLAlchemyの接続とモデル基底
  models.py       データセット、項目、レコード、履歴、ユーザー
  services/       Excel取り込み・一時ファイル管理、データ検索
  main.py         FastAPIアプリの組み立て
  routers/        ページ、Excel取り込み、ヘルスチェックのルート
  templates/      共通レイアウトと各画面
  static/         共通スタイル
migrations/       Alembicの設定とバージョン管理（行単位エラー履歴を含む）
tests/            画面、DBモデル、Excel取り込み、データ検索の動作確認
```

`python -m alembic upgrade head` で最新スキーマを適用します。レコードの可変列はPostgreSQLでJSONBとして保存し、項目名や表示順は項目テーブルで管理します。利用者のパスワードは平文ではなくハッシュ値のみを保存します。データセットごとに列構成を分けられ、失敗した取り込みもデータセット未作成の状態で履歴に記録できます。
