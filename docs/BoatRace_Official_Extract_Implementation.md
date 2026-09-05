# 公式B/Kテキストの展開処理

工程1の取得処理に続く、安全な展開と履歴照合の実装。
既存の取得処理・設定・依存パッケージは変更しない。

## 配置

| ファイル | リポジトリ内の保存先 |
|---|---|
| official_extract.py | src/boatrace/official_extract.py |
| test_official_extract.py | tests/test_official_extract.py |
| 本資料 | docs/BoatRace_Official_Extract_Implementation.md |

## 実行単位と確認範囲

`extract_official_file()` は指定した1日・1種別のLZHを読み込み、`official_files.py` の規則に従ってTXTを保存する。
原LZHがない場合は失敗し、取得処理を呼び出さない。

検査するもの：

- 原LZHのサイズとSHA-256。取得履歴があれば照合する。
- 内部名が対象日の `BYYMMDD.TXT` または `KYYMMDD.TXT` と完全一致すること。
- 内部一覧が通常ファイル1件だけで、ディレクトリ・リンク・想定外項目・重複項目がないこと。
- 圧縮方式、CRC情報、圧縮サイズ・展開後サイズ。
- 7-Zip展開コマンドの終了コードが0であること。CRCエラーなどを伴う出力は採用しない。
- 実際に受け取ったTXTのサイズ、SHA-256、CP932の厳密デコード、開始・終了マーカー。
- 検査・展開中に原LZHや正式展開先が変わっていないこと。

本文のレース日付・会場ブロック・成績行などの意味的整合性は、後続のパーサーで扱う。
CP932のデコードは検査用。元のTXTバイト列をそのまま保存し、改行・空白・文字コードを変換しない。

## 展開方式

1. 原LZHをサイズ上限付きで読み、SHA-256を計算する。
2. 一意な作業ディレクトリに原LZHのコピーを作る。以後の7-Zip検査・展開はこの同じコピーに対して実行する。
3. `7z l -slt -ba -tLzh` 相当で内部一覧を得る。
4. 内部一覧を検査し、想定したTXT1件だけを受け入れる。
5. `7z e -so -tLzh` 相当でTXTのバイト列を標準出力へ展開する。プログラムが指定した一時TXTへ、上限付きで書き込む。
6. 7-Zipの終了コード、サイズ、CP932、外側マーカーを確認し、原LZHと正式展開先も再確認する。
7. 検査済みバイトの履歴を保存し、既存ファイルを置き換えないハードリンクで正式TXTを確定する。

アーカイブ内のパスを、そのままファイルシステム上の出力パスには使わない。
今回の実装では `7z t` を別途繰り返さず、実際の展開時に7-Zipが返すCRC等の検査結果を終了コードで確認する。
7-Zipの通常メッセージとエラーは標準エラーへ分け、TXT本文に混入させない。

`-so` による標準出力への展開と `-slt` による内部情報の取得は、配布マニュアルの説明に基づく。
[7-Zip: -so](https://7-zip.opensource.jp/chm/cmdline/switches/stdout.htm)、[7-Zip: -slt](https://7-zip.opensource.jp/chm/cmdline/switches/list_tech.htm)

## 既定値と対応範囲

| 項目 | 値・動作 |
|---|---|
| 原LZHのサイズ上限 | 16 MiB |
| 展開後TXTのサイズ上限 | 32 MiB |
| 7-Zip内部一覧の出力上限 | 64 KiB |
| 7-Zipの実行時間 | 一覧・展開それぞれ30秒 |
| 実際の展開出力上限 | 内部一覧の申告サイズ。1バイトでも超過したら子プロセスを終了する |
| 初期対応の圧縮方式 | 現物で確認した `-lh5-` と無圧縮の `-lh0-` |
| CRC欄 | サンプルと同じ8桁の16進表記が必要 |
| テキスト境界 | BはSTARTB〜FINALB、KはSTARTK〜FINALK。開始・終了はそれぞれ1個 |
| 同時展開 | 同じデータルートでは1件だけ。残存ロックを自動解除しない |
| 正式保存 | ハードリンクを利用できるローカルファイルシステムが必要 |

上限は本プロジェクトの初期運用値。公式の最大仕様を意味しない。
未対応方式や内部一覧の新しい項目を見つけた場合、無記録で読み飛ばさず停止する。
一覧・展開はタイムアウト、出力上限超過、中断時に子プロセスの終了と一時領域の片付けを試みる。
標準エラーは一時ファイルへ受け、履歴には先頭64 KiBと切り詰めの有無を記録する。標準エラーの一時ファイル自体にはサイズ制限を設けていない。

## 既存展開物

既存TXTに展開履歴がない場合は、原LZHから隔離領域へ再現したTXTとバイト単位で比較する。
一致すれば原本を変更せず、今回確認した内容だけを履歴へ登録する。過去の展開時刻は推測しない。

展開履歴がある場合は、原LZHとTXTの両方を過去のハッシュと照合する。
一致すれば7-Zipの再実行を省略する。
正式展開ディレクトリに想定外のファイルがある場合や、ディレクトリだけが残っている場合は、自動的に削除・補完しない。

新規保存時は正式ディレクトリを排他的に作り、その中に検査済みTXTを確定する。
そのため強制終了や電源断で空ディレクトリが残る可能性がある。次回は不完全な展開先として停止する。
ファイル・履歴の同時確定や、電源断に対する完全なトランザクション保証はない。

## 履歴

取得履歴と混ざらないよう、次の場所へ実行ごとの新しいIDで保存する。

```text
manifest/boatrace_official/extract/{program|result}/{YYYY}/{MM}/{b|k}{YYMMDD}/{実行ID}/
```

| ファイル | 内容 |
|---|---|
| started.json | 開始時刻、対象・保存先、設定値、プログラム版、関連2モジュールのソースSHA-256 |
| list_command.json | 内部一覧取得コマンドの結果、時間切れ、標準エラー |
| listing.json | 内部一覧の元の文字列 |
| extract_command.json | 展開コマンドの結果、受信バイト数、時間切れ、標準エラー |
| verified.json | 原LZHとTXTのハッシュ、TXTサイズ・名前・文字コード、検査時刻、確認元 |
| finished.json | 最終状態、サイズ・ハッシュ、失敗理由 |
| cleanup_error.json | ロック解除失敗があった場合の記録 |

`verified.json` は検査済みバイトの記録で、新規保存では正式TXTの確定前に書く。正式保存成功を単独で保証する記録ではない。
正式保存の成否は `finished.json` と現物を合わせて判断する。
時刻はタイムゾーン付きUTC。`extracted_at` は今回の新規正式保存時だけ設定し、既存TXTの確認ではnullにする。

## 返却状態

| status | 意味 |
|---|---|
| extracted | 検査済みTXTを新規保存し、終了履歴も保存した |
| existing_untracked | 既存TXTが原LZHから再現した内容と一致し、初回の展開履歴を登録した |
| skipped_verified | 原LZH・TXTが過去の展開履歴と一致した |
| archive_mismatch | 原LZHが取得履歴または過去の展開履歴と一致しない |
| text_mismatch | 既存TXTが過去の履歴または原LZHから再現した内容と一致しない |
| history_missing_file | 展開履歴があるが、正式展開先が存在しない |
| busy | 同じデータルートの展開ロックが存在する |
| failed | 内部一覧、CRC、文字コード、サイズ、不完全な展開先、保存競合などの異常 |
| started | started.jsonの開始状態。正常に返る処理結果には使わない |

成功判定にはstatusを使う。`text_verified` は候補TXTの検査結果を示すため、正式保存に失敗した場合にもtrueになり得る。
開始・終了履歴の保存失敗は `ExtractHistoryError` で通知する。
`cleanup_errors` が空でない場合は、成功状態でもロック解除失敗を確認する。

ロックは `manifest/boatrace_official/.extract.lock/`。`owner.json` の実行ID・PID・開始時刻を調べ、処理が終了したと確認できるまで削除しない。

## 呼出し例と実機確認順序

```python
from datetime import date

from boatrace.official_extract import extract_official_file
from boatrace.official_files import OfficialDataKind
from boatrace.settings import load_settings

settings = load_settings()
report = extract_official_file(
    settings.data_dir,
    date(2026, 9, 2),
    OfficialDataKind.RESULT,
    settings.seven_zip_path,
)
print(report.status)
print(report.archive_sha256)
print(report.text_sha256)
print(report.history_dir)
print(report.error)
```

Windowsで全テストとRuff・Pyrightを確認後、次の順で実機確認する。

1. 手動展開済みの2026-09-02 Kを照合・登録する。想定状態は `existing_untracked`。
2. 同じ対象を再実行し、`skipped_verified` を確認する。
3. 取得済みの2026-09-03 Kを新規展開する。正式展開先がない場合の想定状態は `extracted`。

2026-09-02 Kの確認済みTXT：183,232バイト、SHA-256は次の値。

```text
61ac60723cbbdbc41fc5d293499e8ef1d1031a4c6222ca0c1867b5986da31ea1
```

こちらのPython 3.12環境では模擬7-Zip出力による処理全体のテストと、実際の子プロセスを使った時間・出力制限のテストを行った。
添付のB/Kテキスト原本についてもCP932・開始終了マーカー検査が通ることを確認した。
7-Zip 26.02による実際のLZH一覧・展開と、Windows・Python 3.14環境での検証はKAZ側で確認する。
