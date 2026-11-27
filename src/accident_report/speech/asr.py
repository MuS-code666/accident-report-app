"""音声認識（faster-whisper / CTranslate2、CPU・int8）。

スマートアプリコントロールが PyAV の DLL をブロックするため、
faster-whisper の読み込み前に PyAV の代わりの空モジュールを登録する。
音声はファイルではなく numpy 配列（16kHz・モノラル・float32）で渡す。
"""

from __future__ import annotations

import sys
import time
import types
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from accident_report import CONFIG_DIR, PROJECT_ROOT

SAMPLE_RATE = 16000
MODELS_DIR = PROJECT_ROOT / "models" / "whisper"


def _import_faster_whisper():
    try:
        import av  # noqa: F401
    except Exception:  # ブロックされている場合は空モジュールで代用（ファイル読み込み機能だけ使えなくなる）
        sys.modules["av"] = types.ModuleType("av")
    import faster_whisper

    return faster_whisper


def load_vocabulary(path: Path | None = None) -> list[str]:
    path = path or CONFIG_DIR / "vocabulary" / "terms.txt"
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.startswith("#")]


def read_wav(path: Path) -> np.ndarray:
    """WAV を 16kHz・モノラル・float32 の配列にする。"""
    with wave.open(str(path), "rb") as w:
        sr, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    dtype = {1: np.int8, 2: np.int16, 4: np.int32}[width]
    audio = np.frombuffer(raw, dtype=dtype).astype(np.float32) / float(np.iinfo(dtype).max)
    if ch > 1:
        audio = audio.reshape(-1, ch).mean(axis=1)
    if sr != SAMPLE_RATE:
        n = int(len(audio) * SAMPLE_RATE / sr)
        audio = np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio).astype(np.float32)
    return audio


def write_wav(path: Path, audio: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm.tobytes())


@dataclass
class Transcript:
    text: str
    seconds: float  # 処理時間
    audio_seconds: float

    @property
    def rtf(self) -> float:
        """実時間比（1未満なら音声の長さより速く処理できている）。"""
        return self.seconds / self.audio_seconds if self.audio_seconds else 0.0


class Transcriber:
    def __init__(self, model_name: str = "large-v3-turbo", cpu_threads: int = 0,
                 compute_type: str = "int8", vocabulary: list[str] | None = None):
        fw = _import_faster_whisper()
        local = MODELS_DIR / model_name
        if not (local / "model.bin").exists():
            # 初期セットアップ時のみネットワークを使ってダウンロードする
            fw.download_model(model_name, output_dir=str(local))
        self.model = fw.WhisperModel(str(local), device="cpu", compute_type=compute_type,
                                     cpu_threads=cpu_threads)
        self.vocabulary = vocabulary if vocabulary is not None else load_vocabulary()

    def transcribe(self, audio: np.ndarray, use_vocabulary: bool = True) -> Transcript:
        prompt = "、".join(self.vocabulary) if use_vocabulary and self.vocabulary else None
        t = time.perf_counter()
        segments, _info = self.model.transcribe(
            audio, language="ja", beam_size=5, vad_filter=True,
            initial_prompt=prompt, condition_on_previous_text=False,
        )
        text = "".join(s.text for s in segments).strip()
        return Transcript(text=text, seconds=time.perf_counter() - t, audio_seconds=len(audio) / SAMPLE_RATE)


def cer(reference: str, hypothesis: str) -> float:
    """文字誤り率（句読点・空白を除いて比較）。"""
    strip = str.maketrans("", "", "、。，．,.!?！？ 　「」『』・")
    r, h = reference.translate(strip), hypothesis.translate(strip)
    if not r:
        return 0.0 if not h else 1.0
    prev = list(range(len(h) + 1))
    for i, rc in enumerate(r, 1):
        cur = [i]
        for j, hc in enumerate(h, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (rc != hc)))
        prev = cur
    return prev[-1] / len(r)
