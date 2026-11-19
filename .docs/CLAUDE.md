# プロジェクト概要・設計ルール (CLAUDE.md)

## 目的・目標
毎朝手動で実行している複数CLIツール（`claude`, `codex`, `agy`, `agent`, `@opencode/cli`, `grok` 等）のアップデート作業を、ブラウザ上の直感的なUIから**ワンクリックで一括（並列）または個別実行**できるようにし、アプリの登録・編集・解除（一時無効化含む）も極めて簡単に行えるようにする。

## 活用技術
- **バックエンド**: Python 3.14（標準ライブラリのみ：`http.server.ThreadingHTTPServer`, `subprocess`, `concurrent.futures.ThreadPoolExecutor`, `queue`, `json`, `logging`）
- **リアルタイム通信**: Server-Sent Events (SSE)
- **コマンド実行環境**: Windows PowerShell 7 (`pwsh`) / Windows PowerShell 5.1 (`powershell`) / Command Prompt (`cmd`)
  - ユーザープロファイルで定義された PowerShell 関数（例：`agent`）も動作するよう、PowerShell 実行時はプロファイルを読み込む仕様とする。
- **フロントエンド**: バニラ HTML5 / CSS3 / JavaScript (ES Modules)
  - 外部フレームワークやビルドツール不要で、保守性と軽量性を両立。

## 開発ルール
1. **言語**: すべてのUI文言、コードコメント、ドキュメントは日本語で記述する。
2. **フロントエンド分離**: `index.html`、`css/styles.css`、`js/*.js` を明確に分離し、インラインスタイルやインラインイベントハンドラを使用しない。
3. **モジュール分割**: 単一責任の原則 (SRP) に従い、1ファイル1000行以内を維持する。
4. **安全性**: ローカルループバック (`127.0.0.1`) のみにバインドし、破壊的操作（アプリ削除やデータリセット）には必ず確認を求める。
5. **起動コマンド**: Python インストールマネージャー経由で `py -3.14 main.py` を使用する。
