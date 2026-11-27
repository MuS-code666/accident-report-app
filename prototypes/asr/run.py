"""Phase 1-A: ローカル音声認識の検証。

使い方:
  # 検証用の音声で、モデルごとの文字誤り率（CER）と処理速度を測る
  python prototypes/asr/run.py --bench --dir data/asr_test --models small,medium,large-v3-turbo

  # プッシュ・トゥ・トーク（Enterで録音開始、もう一度Enterで停止）で試す
  python prototypes/asr/run.py --record --models large-v3-turbo

検証用の音声は「NN.wav」と正解文「NN.txt」の組で置く。実際の声で測るときは、
--record --save で録音を data/asr_recordings/ に保存し、正解文を手で書いて --bench に使う。
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np

from accident_report import PROJECT_ROOT
from accident_report.speech.asr import SAMPLE_RATE, Transcriber, cer, read_wav, write_wav

RESULTS_DIR = PROJECT_ROOT / "data" / "results"


def bench(directory: Path, models: list[str], threads: int) -> None:
    wavs = sorted(directory.glob("*.wav"))
    if not wavs:
        print(f"音声がありません: {directory}")
        return
    rows = []
    for name in models:
        t = time.perf_counter()
        tr = Transcriber(name, cpu_threads=threads)
        load_s = time.perf_counter() - t
        for use_vocab in (False, True):
            cers, rtfs, secs = [], [], []
            details = []
            for w in wavs:
                ref = w.with_suffix(".txt").read_text(encoding="utf-8").strip()
                res = tr.transcribe(read_wav(w), use_vocabulary=use_vocab)
                e = cer(ref, res.text)
                cers.append(e)
                rtfs.append(res.rtf)
                secs.append(res.seconds)
                details.append({"file": w.name, "ref": ref, "hyp": res.text, "cer": round(e, 3),
                                "seconds": round(res.seconds, 2)})
            row = {"model": name, "vocabulary": use_vocab, "threads": threads or os.cpu_count(),
                   "load_s": round(load_s, 1), "cer_mean": round(statistics.mean(cers), 3),
                   "rtf_mean": round(statistics.mean(rtfs), 3), "seconds_max": round(max(secs), 1),
                   "details": details}
            rows.append(row)
            print(f"{name:16s} 用語登録{'あり' if use_vocab else 'なし'}: CER {row['cer_mean']:.1%}  "
                  f"実時間比 {row['rtf_mean']:.2f}  最長 {row['seconds_max']}秒  （読込 {load_s:.0f}秒）")
            for d in details:
                if d["cer"] > 0:
                    print(f"    {d['file']} CER {d['cer']:.0%}\n      正: {d['ref']}\n      認: {d['hyp']}")
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"asr_bench_{datetime.now():%Y%m%d-%H%M%S}.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"保存: {out}")


def record(model: str, save: bool) -> None:
    import sounddevice as sd

    tr = Transcriber(model)
    print("Enterで録音開始、もう一度Enterで停止。空のまま q と入力すると終了。")
    n = 0
    while True:
        if input("\n[待機中] Enter=録音開始 / q=終了: ").strip().lower() == "q":
            break
        chunks: list[np.ndarray] = []
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                            callback=lambda data, *_: chunks.append(data.copy())):
            input("[● 録音中] Enterで停止 ")
        audio = np.concatenate(chunks)[:, 0] if chunks else np.zeros(0, dtype=np.float32)
        res = tr.transcribe(audio)
        print(f"認識結果: {res.text}\n（音声 {res.audio_seconds:.1f}秒 / 処理 {res.seconds:.1f}秒）")
        if save:
            n += 1
            path = PROJECT_ROOT / "data" / "asr_recordings" / f"{datetime.now():%Y%m%d-%H%M%S}_{n:02d}.wav"
            write_wav(path, audio)
            print(f"保存: {path}（正解文を同じ名前の .txt で作ると --bench に使えます）")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--bench", action="store_true")
    p.add_argument("--record", action="store_true")
    p.add_argument("--save", action="store_true")
    p.add_argument("--dir", type=Path, default=PROJECT_ROOT / "data" / "asr_test")
    p.add_argument("--models", default="large-v3-turbo")
    p.add_argument("--threads", type=int, default=0, help="CPUスレッド数（0=自動）")
    args = p.parse_args()
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    if args.bench:
        bench(args.dir, models, args.threads)
    elif args.record:
        record(models[0], args.save)
    else:
        p.print_help()
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
