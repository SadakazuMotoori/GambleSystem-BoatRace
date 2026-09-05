# 公式B/Kファイル取得処理

工程1の取得処理。既存の `official_files.py` に、HTTP取得と履歴管理を追加する。
既存の `settings.py`、`data_paths.py`、`pyproject.toml` の変更と依存パッケージの追加は不要。

## 配置

| ファイル | リポジトリ内の保存先 |
|---|---|
| official_http.py | src/boatrace/official_http.py |
| official_download.py | src/boatrace/official_download.py |
| test_official_http.py | tests/test_official_http.py |
| test_official_download.py | tests/test_official_download.py |
| 本資料 | docs/BoatRace_Official_Download_Implementation.md |

## 対象と既定値

1回の呼出しで、明示した1日・1種別のみを扱う。日付範囲の一括取得、定期実行、展開、パーサー、既存の `boat` CLIへの組込みは後続工程。

| 項目 | 値・動作 |
|---|---|
| HTTP通信タイムアウト | 30秒 |
| 本文取得の期限 | 120秒。受信の前後で確認するため、通信タイムアウト分の超過はあり得る |
| 圧縮ファイルのサイズ上限 | 16 MiB |
| 最大試行回数 | 初回を含め3回。1〜3回に設定可能 |
| 再試行前の待機 | 初回失敗後5秒、2回目失敗後10秒 |
| 再試行するHTTP状態 | 408・500・502・503・504 |
| その他の再試行対象 | 接続失敗、受信中の切断・タイムアウト、本文取得の期限超過。証明書検証エラーを除く |
| 403・404・429 | 自動再試行せず失敗として記録。404を非開催と解釈しない |
| リダイレクト | 追跡せず停止 |
| 応答本文 | HTTP 200のみ。空本文、サイズ上限超過、Content-Lengthの不正・不一致、identity以外のContent-Encodingを拒否 |
| 7-Zipの検査 | `7z t -tLzh` 相当。制限30秒、終了コード0のみ合格 |
| 同時実行 | 同じデータルートでは取得処理1件だけ。待機やロックの自動解除はしない |

これらは本プロジェクトの運用上の制限であり、公式が認めたアクセス頻度や公開時刻を示す数値ではない。

## 保存と履歴

正式アーカイブの保存先は `official_files.py` の規則を使う。
一意な作業ディレクトリを正式保存先と同じ親ディレクトリ内に作り、そこへ受信する。
HTTP本文の保存完了後にSHA-256とサイズを確定し、7-ZipでLZHとして検査する。
検査後にもハッシュを照合し、検査済みバイトの記録を保存してから正式名を確定する。

正式名と履歴ファイルの確定には `os.link` を使う。同名ファイルが既にあれば失敗し、既存データを置き換えない。ハードリンクに対応するローカルファイルシステムが必要。対応しない場合は停止し、別の保存方法へ自動的に切り替えない。
通常終了時と例外処理時に一時ファイルを削除する。強制終了や電源断では一時ファイルやロックが残る可能性がある。電源断に対する完全なトランザクション保証はない。

履歴は次の場所へ、実行ごとに新しいIDで保存する。

```text
manifest/boatrace_official/{program|result}/{YYYY}/{MM}/{b|k}{YYMMDD}/{実行ID}/
```

| ファイル | 内容 |
|---|---|
| started.json | 開始時刻、種別、日付、URL、保存先、設定値、プログラム版、関連3モジュールのソースSHA-256 |
| attempt-N.json | 各HTTP試行の開始・終了、HTTP状態、取得完了時のサイズ・SHA-256、失敗理由 |
| archive_test.json | 7-Zipのコマンド、終了コードまたは時間切れ、標準出力・標準エラー |
| verified.json | 検査済みバイトのサイズ・SHA-256、確認時刻、確認元。新規取得では正式名確定前に保存する |
| finished.json | 最終状態と実行内容。履歴保存失敗の場合は残らないことがある |
| cleanup_error.json | 自分のロックを解除できなかった場合の記録 |

時刻はタイムゾーン付きUTC。`acquired_at` は今回の新規取得で正式保存した時刻で、既存ファイルの確認時はnull。
HTTP試行の終了時刻も別に保持する。ファイルのLastWriteTimeから過去の取得日時を推測しない。

`verified.json` は取得成功の証明ではなく、検査済みのバイトを示す。正式保存の成否は `finished.json` と実ファイルを合わせて判断する。
正式ファイルだけ、または履歴だけが残る場合も、次回実行時に照合する。

## 返却状態

| status | 意味・次の動作 |
|---|---|
| downloaded | 新規取得・LZH検査・正式保存・終了履歴の保存が完了 |
| skipped_verified | 既存ファイルが過去の検査済みハッシュと一致。通信と7-Zip再検査を省略 |
| existing_untracked | 過去の検査記録がない既存ファイルを今回検査して登録。過去の取得日時は不明のまま |
| history_mismatch | 既存ファイルが過去の検査記録と不一致。上書きせず停止 |
| history_missing_file | 検査記録があるが正式ファイルがない。再取得せず停止 |
| busy | 取得ロックが存在。取得せず停止 |
| failed | 通信、ディスク、検査、履歴形式、保存競合などの失敗。errorを確認 |
| started | started.json内の開始状態。正常に返る処理結果では使わない |

`cleanup_errors` が空でない場合は、取得の成否とは別にロック解除失敗を確認する。
開始・終了履歴を書けない場合は `HistoryWriteError` を送出する。例外発生だけを理由に再取得しない。
`KeyboardInterrupt` は失敗記録と片付けを試みてから再送出する。

ロックの場所は `manifest/boatrace_official/.download.lock/`。`owner.json` の実行ID・PID・開始時刻を確認し、取得処理が終了したと確認できるまで削除しない。
履歴の破損、検査済みハッシュ同士の食い違い、履歴とファイルの片方だけが残る状態を検出した場合は、自動修復せず記録と現物を調べる。

## 呼出し例

Windowsでのテスト・静的検査が完了してから使う。
下の関数は指定パスにアーカイブがない場合、実際にHTTP取得する。
最初の実機確認には、既に手動取得した2026-09-02のKを使い、存在確認後に呼び出す。

```python
from datetime import date

from boatrace.official_download import download_official_file
from boatrace.official_files import OfficialDataKind
from boatrace.settings import load_settings

settings = load_settings()
report = download_official_file(
    settings.data_dir,
    date(2026, 9, 2),
    OfficialDataKind.RESULT,
    settings.seven_zip_path,
)
print(report.status)
print(report.sha256)
print(report.history_dir)
print(report.error)
```

既存ファイルの初回確認なら `existing_untracked`、2回目は `skipped_verified` が想定される。
いずれもHTTP取得回数は0。新規取得では実行結果の `status` を必ず判定する。

## 検証範囲と後続工程

単体テストは通信失敗、有限の再試行、途中ファイルの削除、上書き競合、履歴保存失敗、既存ファイルの変更・消失、ロック、中断を検証する。
HTTP部分はPython標準のHTTPResponseに模擬受信バイトを渡す試験に加え、127.0.0.1の試験サーバーで実際のopenerとリダイレクト拒否を確認する。公式サイトへはアクセスしない。
7-Zip呼出しはテスト用の応答に置き換えるため、実際の7-Zip検査はWindowsで別途確認する。

こちらのテスト実行環境はPython 3.12。型検査はPython 3.14・strictを指定。KAZのWindows・Python 3.14環境で全テストとRuff・Pyrightを実行し、その後に既存Kの検査・履歴登録を確認する。
実通信による新規取得、内部TXT名・展開サイズの検査、安全な展開、既存CLIへの接続は、実装と実機確認の進行に合わせて別途扱う。

実装時に確認した標準API：

- [Python 3.14: os.link](https://docs.python.org/3.14/library/os.html#os.link)
- [Python 3.14: urllib.request](https://docs.python.org/3.14/library/urllib.request.html)
- [Python 3.14: subprocess.run](https://docs.python.org/3.14/library/subprocess.html#subprocess.run)
