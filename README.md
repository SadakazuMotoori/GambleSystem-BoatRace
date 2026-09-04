# BoatRace

尼崎ボートレースを対象とした、**確率予測・価値判定・紙上運用システム**。

本書はプロジェクトの概要、開発工程、現在地を共有するためのREADMEであり、セッション移動時の引き継ぎ資料としても使用する。

## 1. 本システムの概要

過去成績、選手・モーター情報、展示情報、気象・水面状況、オッズなどを蓄積し、各買い目の的中確率と期待値を算出する。

目的は「的中数を増やすこと」ではなく、長期検証により次の判断を一貫して行える仕組みを作ることにある。

- レースごとの確率予測
- 市場オッズと予測確率の比較
- 期待値のある買い目の抽出
- 見送りを含む購入判断
- 紙上運用による収支・精度・再現性の検証
- 兼業運用を前提とした段階的な自動化

当面は実購入を必須とせず、尼崎の全開催を対象にデータ収集と紙上検証を進める。利益を保証するシステムではなく、損失管理と検証可能性を重視する。

## 2. 開発方針

- 開発言語：Python 3.14系
- パッケージ・Python管理：uv
- 品質管理：Ruff、Pyright、pytest、pytest-cov
- CLI：Typer
- 環境設定：Pydantic Settings
- データベース：DuckDB 1.4.5
- 対象環境：Windows 11 x64
- Git管理対象：ソースコード、設定雛形、テスト、設計資料、依存関係ロック
- Git管理対象外：`.env`、`.venv`、収集データ、データベース、ログ、生成物
- 重要な判断には、使用データ・設定・プログラム版を追跡できる情報を残す
- 最初から完全自動化せず、手動確認を残した状態から段階的に自動化する

## 3. 開発項目と工程

### 工程0：開発基盤

- プロジェクト構成とGit管理の整備
- Python、uv、品質管理ツールの固定
- 環境変数と外部データ領域の管理
- `boat doctor`による動作環境診断
- 職場PCと自宅PCでの再現性確認

### 工程1：データ収集基盤

- 尼崎の開催・レース・出走表・結果データ取得
- 選手、モーター、展示、気象、水面、オッズ情報の取得
- 元データの保存と取得履歴・失敗記録の管理
- 再取得、重複防止、欠損検知の実装

### 工程2：データ整形・蓄積

- 取得データの形式統一と検証
- DuckDBおよびParquetへの格納
- 分析用テーブルと特徴量生成
- 将来情報の混入を防ぐ時系列管理

### 工程3：予測・価値判定

- ルールベースの初期予測モデル作成
- 買い目ごとの確率推定
- オッズを用いた期待値計算
- 購入候補と見送り条件の判定
- 資金配分と損失上限の設定

### 工程4：バックテスト・紙上運用

- 過去データによる時系列バックテスト
- 的中率、回収率、期待値、最大ドローダウンの評価
- 日次処理による紙上購入記録
- 予測時点で利用可能だった情報の保存

### 工程5：運用自動化

- 定時データ収集と予測処理
- 実行結果、異常、欠損の通知
- レポート自動生成
- 人間が最終判断できる運用画面またはCLIの整備

## 4. 現時点で完了している作業

### 共通プロジェクト

- GitリポジトリとPythonパッケージを作成
- ブランチ名を`main`に統一
- Pythonを`>=3.14,<3.15`に固定
- uvを`>=0.12.9,<0.13`に固定
- Ruff、Pyright、pytest、pytest-covを開発依存関係として登録
- Windowsの権限問題を避けるため`pyright[nodejs]`を採用
- `nodejs-wheel-binaries`を`uv.lock`へ記録
- Typer、Pydantic Settings、DuckDB 1.4.5を実行時依存関係として登録
- `.env`を型付き設定として読み込む機能を実装
- 外部データ領域の9ディレクトリを検証・生成する機能を実装
- CLIコマンド`uv run boat`と`uv run boat doctor`を実装
- Python、設定、データ領域、7-Zip、DuckDBの環境診断を実装
- 設定、データ領域、診断、CLIの自動テストを作成
- `.gitignore`と`.gitattributes`を整備
- 共通設定雛形`.env.example`を作成

### 品質確認

次の確認はすべて成功している。

```powershell
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest
uv run boat
uv run boat doctor
```

確認結果：Ruff合格、Pyrightエラー0件、pytest 14件合格、CLIと環境診断に成功。

### 職場PC

- リポジトリ：`C:\Gamble\BoatRace`
- 外部データ領域：`C:\Gamble\BoatRaceData`
- `.env`に職場PC固有のデータパスを設定済み
- 9個のデータディレクトリを作成済み

### 自宅PC

- リポジトリ：`E:\Project\Gamble\BoatRace`
- 外部データ領域：`E:\Project\Gamble\BoatRaceData`
- `.env`に自宅PC固有のデータパスを設定済み
- 9個のデータディレクトリを作成済み
- `UV_LINK_MODE=copy`をユーザー環境変数に設定済み
- Gitのリポジトリ設定`core.autocrlf=false`を設定済み

外部データ領域は両PCとも次の構成である。

```text
raw/
manifest/
staging/
lake/
db/
models/
reports/
logs/
tmp/
```

### Git同期状態

- 使用ブランチ：`main`
- `.env`、`.venv`、`BoatRaceData`は同期しない
- ソースコード、テスト、`pyproject.toml`、`uv.lock`、READMEをGitで同期する

## 5. 次に予定している作業

工程0の職場PC側実装とGit同期は完了している。次の順序で進める。

1. 自宅PCで`git pull --ff-only`と`uv sync --locked`を実行する
2. 自宅PCでも全品質検査と`boat doctor`を実行する
3. 両PCで結果が一致した時点で工程0を完了とする
4. 工程1として、尼崎向けデータ取得元の調査と収集仕様を確定する
5. 最初のデータ取得処理と元データ保存処理を実装する

## 6. 作業再開時の確認

```powershell
git status --short --branch
git pull --ff-only
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest
uv run boat
uv run boat doctor
```

環境固有の`.env`と外部データ領域が存在することも確認する。`.env`や実データをGitへ追加してはならない。

---

最終更新：2026-09-03  
現在地：工程0「開発基盤」の職場PC側実装とGit同期が完了。次は自宅PCでの再検証。
