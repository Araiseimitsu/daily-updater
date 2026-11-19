# Codeium Copilot 活用ガイド (copilot-instructions.md)

## プロジェクト構造の理解
本プロジェクトは以下の責務分離で構築されています。補完・提案を行う際は各モジュールの責務を遵守してください。

- `main.py`: エントリーポイント・サーバー起動・ブラウザオープン
- `config.py`: パス定義・初期アプリリスト・デフォルト設定
- `backend/logger.py`: ロギング設定（デバッグモード切替対応）
- `backend/storage.py`: `data/apps.json` および `data/settings.json` のスレッドセーフな永続化
- `backend/events.py`: SSE イベントブローカー
- `backend/runner.py`: 並列コマンド実行エンジン・プロセス制御・ANSIコード処理
- `backend/server.py`: REST API ルーティング・静的ファイル配信
- `frontend/index.html`: 画面構造のみ（インラインJS/CSS禁止）
- `frontend/css/styles.css`: スタイル定義
- `frontend/js/`: `api.js`（通信）, `state.js`（状態）, `ui.js`（描画）, `app.js`（制御）

## コーディング規約
- すべてのコメント・docstring・エラーメッセージは日本語で記述すること。
- Python の型ヒントを積極的に付与すること。
- 外部パッケージを追加する場合は必ず `requirements.txt` を更新すること。
- フロントエンドでHTMLへユーザー入力を埋め込む際は、必ずエスケープ処理または `textContent` を使用し XSS を防止すること。
