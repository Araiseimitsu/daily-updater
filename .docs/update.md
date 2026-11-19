# 更新履歴 (update.md)

## 2026-11-19 - v1.0.0 初期構築
- プロジェクト `daily-updater` を新規作成。
- Python 標準ライブラリベースのバックエンド（並列実行エンジン、SSE リアルタイムログ配信、JSON永続化、REST APIサーバー）を実装。
- HTML / CSS / JS を完全分離したモダンローカルWeb UIを実装。
- 初期登録アプリとして以下の6ツールをプリセット登録：
  - `Claude Code` (`claude update`)
  - `Codex CLI` (`codex update`)
  - `Antigravity (agy)` (`agy update`)
  - `Cursor Agent` (`agent update`)
  - `OpenCode CLI` (`npm install -g @opencode/cli@latest`)
  - `Grok CLI` (`grok update`)
- PowerShell プロファイル関数（`agent` 等）に対応したコマンド実行方式を実装。
- ワンクリック起動用 `start_updater.bat` およびデスクトップショートカット作成用 `create_shortcut.ps1` を追加。

## 2026-11-19 - プロジェクト配置先の移動
- プロジェクトフォルダを `C:\Users\seizo\my_projects\2\daily-updater` へ移動。

## 2026-11-19 - 起動バッチの不具合修正
- `start_updater.bat` が LF 改行 + UTF-8 日本語メッセージを含んでいたため、`cmd.exe` の `chcp 65001` 切替時にパースずれが発生して起動できない問題を修正（CRLF 改行 + ASCII のみに変更し、実起動および `/api/bootstrap` 応答を検証済み）。

## 2026-11-19 - 専用アプリアイコン作成と適用
- 角丸透過デザインの専用アイコンを生成し、マルチ解像度 `.ico`（`assets/app_icon.ico`）および Web ファビコン・PNG アセット（`frontend/favicon.ico`, `frontend/assets/icon-*.png`）を作成。
- デスクトップショートカット（`C:\Users\seizo\Desktop\Daily CLI Updater.lnk`）および `create_shortcut.ps1` に専用アイコンを適用。
- Web アプリのファビコンおよびヘッダーロゴへ専用アイコンを適用。

## 2026-11-19 - UI・アイコンのミニマライズ刷新
- 青紫グラデーション・ネオン発光・多色ピルを廃止し、無彩色モノクローム主体（Zinc ダーク / クリーンライト）＋極細ヘアライン枠＋白黒反転プライマリボタン＋極小ステータスドットの先進的ミニマル UI へ刷新。
- カードグリッドを廃止し、視認性と一覧性に優れたコンパクトなリストビュー構成へ移行。
- アプリアイコンもオブシディアン・グラファイト＋純白ジオメトリック矢印のモノクロームデザインへ統一し、デスクトップショートカットおよび Web ファビコンを再適用。

## 2026-11-19 - Git リポジトリ初期化
- `.gitignore` を追加し、`git init -b main` および初回コミットを実施。
- `.gitignore` を Python / 仮想環境 / 機密情報 / アプリランタイムデータ / OS / IDE 向けの標準構成へ拡充。
