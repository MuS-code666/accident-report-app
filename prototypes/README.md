# Phase 1 技術検証の手順

3つの機能を独立したプロトタイプとして検証する。結果は `data/results/`（Git対象外）に保存される。
開発PCは本番端末（Core i5-1235U・16GB）より速いため、処理時間は本番端末でも測り直すこと。

## 準備（初回のみ・ネットワーク接続が必要）

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e .[dev]
winget install Ollama.Ollama          # 署名付き。127.0.0.1:11434 でのみ待ち受ける
ollama pull qwen3:4b
ollama pull qwen3:8b
# 音声認識モデル（hf_xet はネイティブDLLのため使わない）
$env:HF_HUB_DISABLE_XET = "1"
.venv\Scripts\python prototypes\asr\run.py --bench --models small,medium,large-v3-turbo
```

準備が終われば、以降はネットワークを切っても動く。

## A. 音声認識

```powershell
# 合成音声（Windows の日本語読み上げ）で検証用音声を作る
powershell -ExecutionPolicy Bypass -File prototypes\asr\make_test_audio.ps1 -OutDir data\asr_test
# モデルごとの文字誤り率（CER）と処理速度
.venv\Scripts\python prototypes\asr\run.py --bench --models small,medium,large-v3-turbo
# 内蔵マイクで試す（録音を保存し、正解文 .txt を書けば --bench --dir data\asr_recordings で測れる）
.venv\Scripts\python prototypes\asr\run.py --record --save
```

合成音声は雑音がなく明瞭なので、実際の声より良い結果になる。実際の声での測定を必ず行う。

## B. AIインタビュー

```powershell
# 架空事例3件を模擬回答者で自動インタビュー（抽出は4B、文章は8B）
.venv\Scripts\python prototypes\interview\run.py --all --model qwen3:4b --writer-model qwen3:8b
# キーボードで対話（/dk=分からない /na=該当なし /later=後で /end=終了）
.venv\Scripts\python prototypes\interview\run.py --interactive
```

主な指標: 質問数、同じ項目の再質問、抽出の正解率、1問あたりの待ち時間、文章生成時間、
回答にない数字・禁止語（聞き取っていない事実）の有無。

## C. 事故状況図

```powershell
# 架空事例2件（採点つき）。「文章のみ」と「追加質問あり」を比較
.venv\Scripts\python prototypes\diagram\run.py --all
# 実際のサンプル（ローカルの Excel から事故概要を読む。追加質問にはキーボードで答える）
.venv\Scripts\python prototypes\diagram\run.py --excel 事故報告書.xlsx --interactive
```

出力: `data/results/diagram/*_A_text_only_*.png`（文章のみ）と `*_B_with_questions_*.png`（追加質問あり）。
