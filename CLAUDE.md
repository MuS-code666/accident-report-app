# CLAUDE.md

このファイルは、このリポジトリで作業する Claude Code へのガイドです。

## プロジェクト概要

accident-report-app — 工場内事故の報告書を、AIの聞き取り（音声入力）で作る完全オフラインの Windows アプリ。Python 3.12 + PySide6。

- 起動: `start_app.bat`（中身は `.venv\Scripts\pythonw -m accident_report.app`）。Ollama を先に起動しておく
- テスト: `.venv\Scripts\python -m pytest -q`（テンプレートが必要なテストは、`templates/` がなければスキップ）
- `python` は標準の Python を指すので、必ず `.venv\Scripts\python` を使う
- 進捗: Phase 1（技術検証）完了・合格基準は `prototypes/README.md`。Phase 2（基本機能）作業中

## ディレクトリ構成

- `src/accident_report/app/` 画面（開始・聞き取り・確認・設定）。重い処理は `services.run_task` で裏で動かす
- `src/accident_report/interview/` 聞き取りの進行管理（Python側）と回答の抽出（LLM）
- `src/accident_report/writing/` 通知用の文章生成と根拠チェック
- `src/accident_report/export/` Excel 転記（`notice_excel.py`。転記位置はファイル冒頭に記載）
- `src/accident_report/storage/` SQLite（`data/app.db`）。案件・利用者・用語・設定。録音は `data/audio/`
- `src/accident_report/diagram/` 作図（Phase 3 で画面に組み込む）
- `src/accident_report/speech/` 音声認識
- `config/` 質問セット・用語。`prototypes/` Phase 1 の検証用スクリプト

## Git運用ルール

- リモート: `origin` → https://github.com/MuS-code666/accident-report-app.git
- デフォルトブランチ: `main`
- **コードを変更するたびに、コミットして GitHub (`origin`) にプッシュすること。**
  - 変更は意味のある単位でコミットする
  - コミットメッセージには「何を・なぜ」変更したかを簡潔に書く
  - コミット後は `git push` まで行い、ローカルとリモートを常に同期させる
- `.env` や認証情報などの秘密情報はコミットしない
- **実データは GitHub に置かない。** 記入済み事故報告書（Excel）、氏名・事故内容を含むデータ、音声・写真、案件DB、出力した報告書が対象。`.gitignore` で除外しているが、コミット前に `git status` で必ず確認する
- 空欄テンプレートをコミットする場合は、個別の事故情報（氏名、文章、写真、図形）が残っていないことを確認してから `.gitignore` に例外を追加する

## 確定した設計条件（2026-11-27）

- 端末: Windows PC 1台（タブレットは対象外）。CPU Core i5-1235U、メモリ16GB、GPUなし。Excelインストール済み
- 音声入力: PCの内蔵マイク、室内で使用
- 言語: 日本語のみ
- 出力: Excelファイルを同じPC内に出力（端末間の受け渡しは不要）
- 処理時間: AIの1回の処理（文章生成・作図など）を10分以内

## 帳票の決定事項（2026-11-27）

- 通知用の作業区分（E5:I6）: 選んだ作業名だけを残す形に文字を書き換える（○図形は使わない）
- 通知用の38〜63行目: 不要。印刷範囲は A1:I37
- 写真: 本来は別紙に貼る（サンプルで相手情報欄に貼られていたのは例外）
- 日時: 西暦表記で「時ごろ」まで書く（例: 2026年10月23日 14時ごろ）
- 詳細報告: 正式な空欄テンプレートが届くまで、サンプルから作った暫定テンプレートを使う
- openpyxl はグループ化図形・画像を読み込めず、保存すると消える。テンプレートは図形なしとし、事故状況図は PNG で貼る

## 実行環境の制約（2026-11-27 判明）

- 開発PCと本番端末の両方で **Windows スマートアプリコントロール（SAC）が有効**。署名がなく評判の低い DLL / EXE は読み込みを拒否される
  - ブロック確認済み: llama-cpp-python の `llama.dll`、PyAV（`av`）
  - 読み込める: ctranslate2、onnxruntime、PySide6、numpy、sounddevice
- LLM は **Ollama**（Ollama Inc. の署名あり）を使う。127.0.0.1:11434 のみで待ち受けることを確認済み。llama.cpp 組み込み案は保留
- faster-whisper は import 時に PyAV を読むため、PyAV を使わない形で読み込む（音声は numpy 配列で渡す）
- 自作アプリの配布にはコード署名が必要になる見込み（Phase 5 の課題）
- 開発PCは Core Ultra 7 258V・32GB で本番端末より速い。処理時間の測定値は参考値とし、ベンチマークは本番端末でも実行する

## 技術選定の決定事項（2026-11-28）

- 音声認識: **faster-whisper `large-v3-turbo`（CPU・int8）＋用語登録（`config/vocabulary/terms.txt` を initial_prompt に渡す）** を採用
  - 根拠: 実際の声10文（内蔵マイク・室内）で文字誤り率 6.0%。small 9.4%、medium 8.3% より良い
  - 1発話の処理は開発PCで最長約8秒。本番端末（i5-1235U）での測定はまだ
  - 残っている誤認識: 「後退→交代」（2回の検証で続けて発生）、「カウンターウェイト→アウターウェイト」。確認画面で修正する前提で、用語の後処理は Phase 2 で検討する

## ローカル環境

- 暫定テンプレート: `templates/notice_template.xlsx`、`templates/detail_template.xlsx`（Git 対象外）。`python tools/make_templates.py 事故報告書.xlsx` で再作成できる

- Python 3.12、仮想環境は `.venv/`（Git対象外）
- 帳票サンプル: `事故報告書.xlsx`（実データのため Git 対象外）
