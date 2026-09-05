# BoatRace

尼崎ボートレースを対象とした、**確率予測・価値判定・紙上運用システム**。

本書はプロジェクトの概要、開発工程、現在地を共有するためのREADMEであり、セッション移動時の引き継ぎ資料としても使用する。

**現在地：工程0は職場PC・自宅PCの両方で検証完了。工程1は公式データの手動調査中。**

次回は職場PCで、取得済み競走成績`k260902.lzh`の保存先・サイズ・SHA-256の実行結果をルークへ提示し、確認後に内部検査へ進む。

## 1. 本システムの概要

過去成績、選手・モーター情報、展示情報、気象・水面状況、オッズなどを蓄積し、各買い目の的中確率と期待値を算出する。

目的は「的中数を増やすこと」ではなく、長期検証によって次の判断を一貫して行える仕組みを作ることにある。

- レースごとの確率予測
- 市場オッズと予測確率の比較
- 期待値のある買い目の抽出
- 見送りを含む購入判断
- 紙上運用による収支・精度・再現性の検証
- 兼業運用を前提とした段階的な自動化

当面は実購入を必須とせず、尼崎の開催を対象にデータ収集と紙上検証を進める。

利益を保証するシステムではなく、検証可能性、再現性、損失管理を重視する。

## 2. 開発方針

- 開発言語：Python 3.14系
- Python・パッケージ管理：uv
- CLI：Typer
- 環境設定：Pydantic Settings
- データベース：DuckDB 1.4.5
- 品質管理：Ruff、Pyright、pytest、pytest-cov
- 対象環境：Windows 11 x64
- 文字コード：ソースコードはUTF-8。番組表Bの確認済みサンプルはCP932。競走成績Kは展開後に確認する
- Git管理対象：ソースコード、テスト、設定雛形、設計資料、依存関係ロック
- Git管理対象外：`.env`、`.venv`、収集データ、データベース、ログ、生成物
- 取得した元データは上書きせず、そのまま保存する
- 使用データ、取得日時、ハッシュ、設定、プログラム版を追跡可能にする
- 最初から完全自動化せず、手動確認を残した状態から段階的に自動化する

## 3. 開発項目と工程

### 工程0：開発基盤

- プロジェクト構成とGit管理の整備
- Python、uv、品質管理ツールの固定
- 環境変数と外部データ領域の管理
- `boat doctor`による動作環境診断
- 職場PCと自宅PCでの再現性確認

### 工程1：データ収集基盤

- 公式データ取得元とファイル規則の調査
- 番組表、競走成績、選手期別成績の取得
- 元データ、取得履歴、ハッシュ、失敗記録の保存
- 再取得防止、重複防止、欠損検知
- 7-ZipによるLZH展開
- CP932テキストの読込
- 尼崎会場ブロックの抽出

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
- 人間が最終判断できるCLIまたは運用画面の整備

## 4. 現時点の実装・調査状況

### 工程0：開発基盤（両PCで検証完了）

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
- `uv run boat`を実装
- `uv run boat doctor`を実装
- Python、GIL、設定、データ領域、7-Zip、DuckDBの診断を実装
- 設定、データ領域、診断、CLIの自動テストを実装
- `.gitignore`と`.gitattributes`を整備
- 共通設定雛形`.env.example`を作成

### 工程0の品質確認

職場PCと自宅PCの両方で、次の確認がすべて成功している。実行環境はPython 3.14.7（標準GIL）、uv 0.12.9、7-Zip 26.02、DuckDB 1.4.5。Python依存関係は`uv.lock`で揃えている。

```powershell
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest
uv run boat
uv run boat doctor
```

確認結果：

- Ruff：合格
- Pyright：エラー0件
- pytest：14件合格
- `boat`：起動成功
- `boat doctor`：5項目すべて`[OK]`
- Git：自宅PCで`git fetch --prune origin`後に`main`と`origin/main`の一致、検証後に未コミット変更なしを確認

自宅PCでの最終確認済みコミット（本README更新前）：`bca1b67 プロジェクト概要`。今後の同期では、その時点のGit履歴を確認する。

### 工程1：公式データ調査（手動調査中）

BOAT RACE公式の日次ダウンロードを初期データ源として使用する。

現時点はPowerShellによる手動取得・形式調査まで。Pythonによるデータ取得、展開、パーサーは未実装。取得履歴の自動管理や、一時ファイルから正式名へ移動する処理も今後実装する。今回の手動取得では`raw`の正式名へ直接保存した。

2026-09-02のB/Kファイル取得で確認したURL形式：

```text
番組表：
https://www1.mbrace.or.jp/od2/B/{YYYYMM}/b{YYMMDD}.lzh

競走成績：
https://www1.mbrace.or.jp/od2/K/{YYYYMM}/k{YYMMDD}.lzh
```

今後実装する取得処理の方針：

- 取得済みファイルは再取得しない
- 元ファイルを上書きしない
- 大量アクセスを行わない
- 無制限の再試行を行わない
- 取得後にSHA-256を記録する
- 一時ファイルへの取得完了後に正式名へ移動する
- `raw`には取得した元ファイルをそのまま保存する
- 展開物は`staging`へ保存する

### 番組表サンプルの確認

対象日：

```text
2026-09-02
```

元ファイル：

```text
C:\Gamble\BoatRaceData\raw\boatrace_official\program\2026\09\b260902.lzh
```

確認結果：

- サイズ：30,528 bytes
- SHA-256：`2FF7A48F93F38BD3ECEE7B2839EA509BE994C7FF8B1664BDC1A980D03A05063E`
- 圧縮形式：LZH
- 圧縮方式：`-lh5-`
- 内部ファイル：`B260902.TXT`
- 展開後サイズ：142,691 bytes
- 展開成功時の7-Zip終了コード：`0`

展開先：

```text
C:\Gamble\BoatRaceData\staging\boatrace_official\program\2026\09\b260902\B260902.TXT
```

展開後ファイルのSHA-256：

```text
032C48A2C7AC2E2F5EF516EECA2A61EA29B7EBDCAA9CBFBC615B7A2C589CF186
```

番組表テキストの確認結果：

- 文字コード：CP932
- ファイル開始マーカー：`STARTB`
- 会場開始マーカー：`{会場コード}BBGN`
- 会場終了マーカー：`{会場コード}BEND`
- 尼崎会場コード：`13`
- 尼崎ブロック：`13BBGN`から`13BEND`
- 確認した尼崎ブロック：156行
- レース見出しと6艇分の選手行を確認
- 1日分のファイルに複数会場を収録
- 選手名には全角空白が含まれる

今後の解析方針：

- 選手行はCP932上の固定バイト幅として解析する
- 単純な空白区切りによる解析は使用しない
- 各列のバイト位置や例外行の扱いは、追加調査とテストで確定する

### 競走成績サンプルの取得記録

対象日：`2026-09-02`。職場PCで取得済み。内部検査・展開・文字コード・行構造の確認は未着手。

元ファイル：

```text
C:\Gamble\BoatRaceData\raw\boatrace_official\result\2026\09\k260902.lzh
```

取得時の実行ログ：

- サイズ：36,829 bytes
- SHA-256：`AA44B6FD89F1B66E915611189E1F8C28F728A1B62C36DCAC8441A3BA8DC4870E`

このハッシュは取得したファイルの同一性を確認するための記録。アーカイブの内容が正常かどうかは、次回の内部検査・展開で確認する。

次回は第7節の実行結果を提示し、ルークが確認してから内部検査へ進む。

## 5. 開発環境とデータ領域

### 職場PC

```text
リポジトリ：
C:\Gamble\BoatRace

外部データ領域：
C:\Gamble\BoatRaceData
```

職場PCでは工程0の全品質検査と`boat doctor`が成功している。

### 自宅PC

```text
リポジトリ：
E:\Project\Gamble\BoatRace

外部データ領域：
E:\Project\Gamble\BoatRaceData
```

自宅PCでも工程0の全品質検査、14件のテスト、`boat doctor`の5項目、CLI起動が成功している。コードと依存関係の同期は完了している。

自宅PCには次のPC固有設定が存在する。

- `.env`
- 9個のデータディレクトリ
- ユーザー環境変数`UV_LINK_MODE=copy`
- Gitローカル設定`core.autocrlf=false`

### Git同期対象

同期するもの：

- ソースコード
- テスト
- `README.md`
- `.env.example`
- `pyproject.toml`
- `uv.lock`
- その他の設計資料

同期しないもの：

- `.env`
- `.venv`
- `BoatRaceData`
- DuckDBファイル
- 収集済みLZH・TXT
- ログや一時ファイル

職場PCで取得した番組表・競走成績サンプルは、自宅PCへ自動同期されない。

## 6. 自宅PCでの再同期手順

初回の同期と検証は完了済み。以下は、今後コードや依存関係の更新を自宅PCへ取り込む際の手順。PowerShell 7で実行する。

最初の`git status`で未コミット変更があれば内容を確認してから進める。各コマンドでエラーが出た場合は、その結果をルークへ提示して確認する。

```powershell
Set-Location -LiteralPath "E:\Project\Gamble\BoatRace"

git status --short --branch
git pull --ff-only
uv sync --locked

uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest
uv run boat
uv run boat doctor

git status --short --branch
```

期待結果：

- Ruff：合格
- Pyright：エラー0件
- pytest：14件合格
- `boat doctor`：5項目すべて`[OK]`
- Git：`main`と`origin/main`が一致
- 未コミット変更なし

自宅PCの`.env`と`E:\Project\Gamble\BoatRaceData`は、既存のものをそのまま使用する。

## 7. 次回最初に行う作業

作業環境は職場PC。取得済みの`k260902.lzh`について、保存先・サイズ・SHA-256の実行結果をルークへ提示する。以下は保存済みファイルの確認用コマンド。

新しいPowerShellウィンドウでは以前の変数を引き継がないため、必要なパスを定義し直す。

```powershell
Set-Location -LiteralPath "C:\Gamble\BoatRace"

$brResultDirectory = "C:\Gamble\BoatRaceData\raw\boatrace_official\result\2026\09"
$brResultFile = Join-Path $brResultDirectory "k260902.lzh"

Get-Item -LiteralPath $brResultFile |
    Select-Object FullName, Length, LastWriteTime

Get-FileHash -LiteralPath $brResultFile -Algorithm SHA256
```

次回は、このコマンドの実行結果をそのままルークへ貼り付ける。

ルークが次を確認する。

- ファイルが存在し、保存先が第4節の記録と一致するか
- サイズが36,829 bytesであるか
- SHA-256が第4節の取得ログと一致するか

ファイルが見つからない場合や記録と一致しない場合は、その結果から原因を確認する。確認が完了するまで、競走成績ファイルの内部検査・展開処理へ進まない。

## 8. その後に予定している作業

競走成績ファイルの取得確認後、次の順序で進める。

1. `k260902.lzh`の内部ファイル、サイズ、圧縮方式、CRCを確認
2. `staging`領域へ上書きせず展開
3. 展開後ファイルのサイズとSHA-256を記録
4. CP932を候補として読み込み、文字コードと日本語表示を確認
5. K系ファイルの開始・終了マーカーを確認
6. 尼崎会場コード`13`のブロックを抽出
7. 競走成績のレース・着順・払戻情報の行構造を確認
8. 公式データ取得仕様を文書化
9. 日付からB/KファイルのURLと保存先を生成する機能を実装
10. キャッシュ、SHA-256、部分ファイルを考慮したダウンロード機能を実装
11. 7-Zipによる安全な展開機能を実装
12. 番組表と競走成績のパーサーを実装
13. 各段階に自動テストを追加

## 9. 作業再開時の確認

- READMEを読み、現在地と次の作業を確認する
- 作業は一つずつ提示し、KAZが実行結果を貼り、ルークが確認してから次へ進める
- PowerShellの変数は、新しいウィンドウで必要なものを定義し直す

職場PCではPowerShell 7で次を実行する。自宅PCでは第6節のパスと手順を使用する。未コミット変更がある場合は内容を確認し、コマンドのエラーを解決してから次へ進める。

```powershell
Set-Location -LiteralPath "C:\Gamble\BoatRace"

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

環境固有の`.env`と外部データ領域が存在することも確認する。

`.env`、`.venv`、収集データをGitへ追加してはならない。

---

最終更新：2026-09-05  
現在地：工程0は職場PC・自宅PCの両方で検証完了。工程1は公式データの手動調査中で、Pythonによる収集処理は未実装。  
番組表B：取得・展開・CP932読込・尼崎ブロック抽出まで確認済み。  
競走成績K：取得済み。内部検査・展開・文字コード・構造確認は未着手。  
次の作業：職場PCで`k260902.lzh`の保存先・サイズ・SHA-256の実行結果をルークへ提示し、確認後に内部検査へ進む。
