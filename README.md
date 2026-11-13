# Akashic Record

FastAPIで作る、Excelデータ検索・閲覧アプリです。フェーズ1〜7のアプリ基盤、DBスキーマ、`.xlsx` プレビュー・取り込み、検索、一覧・詳細表示、レスポンシブ対応、ログイン・権限制御を実装しています。フェーズ8のRender設定とデプロイ前検証を追加していますが、Renderへの実デプロイと本番環境の確認は未実施です。

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

## Renderへのデプロイ

リポジトリ直下の [`render.yaml`](./render.yaml) は、SingaporeリージョンにWebサービスとPostgreSQLを作成するRender Blueprintです。WebサービスとDBには有料プランを指定しているため、Blueprintを作成すると料金が発生します。プラン・リージョン・料金をRenderの画面で確認してから作成してください。

1. 変更をGitHubへpushし、Renderで **New > Blueprint** からこのリポジトリを選択します。
2. 表示されるWebサービスとPostgreSQLのプラン・リージョン・料金を確認してBlueprintを適用します。`SESSION_SECRET_KEY` は自動生成し、`DATABASE_URL` はDBサービスの接続文字列を使います。
3. デプロイログで `python -m alembic upgrade head` の成功を確認します。マイグレーションが成功した後にWebサーバーが起動し、`/health/ready` がDB接続を検査します。
4. RenderのサービスShellで、管理者アカウントを作成します。パスワードは対話入力してください。

   ```text
   python -m app.cli create-user --email viewer@example.com --role viewer
   python -m app.cli create-user --email importer@example.com --role importer
   ```

5. デプロイ後、`/login`、Excel取り込み、検索、レコード詳細、`/health/ready` を確認します。ログはRenderのサービス画面から確認できます。

### DBバックアップと復旧

- Render Postgresのバックアップ機能と保持期間をDBサービス画面で確認し、利用プランで自動バックアップが利用できることを公開前に確認します。復元可能な時点と保持期間を運用記録に残してください。
- 大きな取り込みやスキーマ変更の前には、利用可能な手動バックアップを作成します。
- DB障害時はRenderのDB画面で障害時刻より前のバックアップへ復元し、アプリの `/health/ready` とログイン・検索を確認します。復元操作の前に、現在のDB状態を別途保全できるか確認してください。
- マイグレーションに失敗したデプロイはログのエラーを確認し、DBを確認せずに手作業でスキーマを変更したり、データを削除したりしないでください。原因を直してから再デプロイします。

ローカルでデプロイ前のDB確認テストを実行するには、`python -m pytest tests/test_deployment.py -q` を使います。このテストは一時SQLite DBへAlembicマイグレーションを適用し、接続を作り直した後もデータを読めることを確認します。Render Blueprint自体は実際のRenderアカウントに適用して初めて、クラウド上のデプロイ・バックアップ・HTTPSを確認できます。

## 画面と確認用エンドポイント

- `/` — DBに保存されたデータセット一覧
- `/search` — データセットの選択、項目別の部分一致・完全一致、複数条件検索、並べ替え、ページ分割、代表項目の一覧
- `/datasets/{dataset_id}/records/{record_id}` — 検索結果から開くレコード全項目の詳細
- `/import` — `.xlsx` のシート選択・見出し確認・プレビュー・取り込み
- `/login` — 登録済み利用者のログイン
- `/logout` — CSRF確認付きのログアウト（POST）
- `/health/live` — アプリの起動確認（DB接続不要）
- `/health/ready` — PostgreSQL接続確認。接続できない場合は `503` を返します。

未ログイン利用者はデータ画面へ入れません。`viewer` は閲覧のみ、`importer` は閲覧とExcel取り込みができます。アカウントの公開登録はなく、下記CLIから管理者が発行します。データセットは現行設計では認証済み利用者間で共有します。レコード詳細ではレコードIDだけでなくデータセットIDとの組み合わせも検証します。

パスワードはソルト付きPBKDF2-HMAC-SHA256でハッシュ化し、平文では保存しません。セッションCookieは署名付き・HttpOnly・SameSite=Laxで、有効期間は8時間です。本番環境ではSecure属性も有効になります。ログイン・ログアウト・Excel取り込みPOSTにはCSRFトークンを検証します。ログイン失敗の表示はアカウントの有無を区別せず、DB障害の詳細は画面に出しません。

検索条件と結果一覧を分け、詳細画面では項目名と値を対応させて表示します。幅の狭い画面ではメニューと条件フォームを縦に並べ、表は横スクロールできます。Excelのプレビュー・確定フォームには処理中表示と二重送信防止を備えています。

### アカウント発行と本番設定

1. DBマイグレーション適用後、閲覧者または取り込み担当者を作成します。パスワードは端末上で対話入力し、コマンド履歴や環境変数には保存しません。

   ```powershell
   python -m app.cli create-user --email viewer@example.com --role viewer
   python -m app.cli create-user --email importer@example.com --role importer
   ```

2. `ENVIRONMENT=production` で起動する場合、`.env` またはデプロイ環境に32文字以上のランダムな `SESSION_SECRET_KEY` を設定してください。空や短い鍵ではアプリを起動しません。鍵はGitへ登録せず、秘密管理機能に保存します。

   ```powershell
   python -c "import secrets; print(secrets.token_urlsafe(48))"
   ```

セッション署名鍵を変更すると既存ログインは無効になります。HTTPS終端を経由する本番環境でのみ `ENVIRONMENT=production` を設定し、Secure Cookieが利用できる構成にしてください。

設定は環境変数から読み込みます。`.env` を使う場合は `DATABASE_URL` にSQLAlchemy形式のURLを指定してください（例: `postgresql+psycopg://user:password@host:5432/database`）。`.env` はGitの追跡対象外です。

### Excel取り込み

1. `/import` で `.xlsx` ファイルと見出し行を指定します。
2. 表示シートを選び、先頭5件のプレビューと警告を確認します。
3. 必要に応じてデータセット名・項目の表示名を変更し、取り込みを確定します。
4. 成功すると、取り込み件数が表示され、データセット一覧に追加されます。

ファイルはプレビューから確定までシステムの一時フォルダーに短時間保管し、取り込み成功後に削除します。未確定のファイルも30分後に期限切れとなり、次のアップロード時に削除されます。元ファイルをアプリの永続データとして保存しません。数式は実行せず、保存済みの計算結果だけを使用します。結果がない数式は警告付きの空欄、全セルが空の行は除外し、Excel上の行番号を記録します。非表示シートは選択肢から除外し、結合セルは左上セルだけを使います。行の値に問題がある場合は元のExcel行番号とエラー内容を取り込み履歴に記録します。失敗時はデータセットとレコードをまとめてロールバックし、成功扱いにしません。

`.env.example` の `MAX_UPLOAD_SIZE_BYTES`（初期値10 MiB）、`MAX_WORKBOOK_UNCOMPRESSED_BYTES`（50 MiB）、`MAX_IMPORT_ROWS`（50,000件）は安全のための初期値です。計画書にある通り、実データの見本や本番環境を確認してから必要に応じて調整してください。

本番公開はフェーズ8のRenderデプロイ、HTTPS・秘密鍵・DBバックアップ・運用確認が完了してから行ってください。データセットは共有スコープであり、ユーザー単位の所有・割当管理はまだありません。

## ディレクトリ構成

```text
app/
  config.py       環境設定
  database.py     SQLAlchemyの接続とモデル基底
  models.py       データセット、項目、レコード、履歴、ユーザー
  security.py     パスワードハッシュ、セッション利用者、ロール、CSRF検証
  services/       Excel取り込み・一時ファイル管理、データ検索
  main.py         FastAPIアプリの組み立て
  routers/        認証、ページ、Excel取り込み、ヘルスチェックのルート
  templates/      共通レイアウトと各画面
  static/         共通スタイル
migrations/       Alembicの設定とバージョン管理（行単位エラー履歴を含む）
tests/            認証・権限、画面、DBモデル、Excel取り込み、データ検索の動作確認
```

`python -m alembic upgrade head` で最新スキーマを適用します。レコードの可変列はPostgreSQLでJSONBとして保存し、項目名や表示順は項目テーブルで管理します。利用者のパスワードは平文ではなくハッシュ値のみを保存します。データセットごとに列構成を分けられ、失敗した取り込みもデータセット未作成の状態で履歴に記録できます。
