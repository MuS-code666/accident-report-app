"""画面から使う共通の部品: AIモデル・音声認識・読み上げ・録音・バックグラウンド処理。"""

from __future__ import annotations

import json
import threading
import time
import urllib.request
from typing import Callable

import numpy as np
from PySide6.QtCore import QLocale, QObject, QRunnable, QThreadPool, Signal
from PySide6.QtTextToSpeech import QTextToSpeech

from accident_report.interview.extraction import extract
from accident_report.interview.questions import load_question_set
from accident_report.llm import OllamaClient
from accident_report.speech.asr import SAMPLE_RATE, load_vocabulary
from accident_report.storage import Store

OLLAMA_URL = "http://127.0.0.1:11434"


# ---- バックグラウンド処理（AI・音声認識は時間がかかるので画面を止めない） ----

class _Signals(QObject):
    done = Signal(object)
    failed = Signal(str)


class _Task(QRunnable):
    def __init__(self, fn: Callable[[], object]):
        super().__init__()
        self.setAutoDelete(False)
        self.fn = fn
        self.signals = _Signals()

    def run(self) -> None:
        try:
            try:
                result = self.fn()
            except Exception as e:  # noqa: BLE001 - 画面にそのまま知らせる
                self.signals.failed.emit(f"{type(e).__name__}: {e}")
                return
            self.signals.done.emit(result)
        except RuntimeError:
            pass  # アプリを閉じた後に処理が終わった（知らせる先がない）


_running: set[_Task] = set()


def run_task(fn: Callable[[], object], on_done: Callable[[object], None] | None = None,
             on_failed: Callable[[str], None] | None = None) -> None:
    task = _Task(fn)

    def finish(_=None):
        _running.discard(task)

    if on_done:
        task.signals.done.connect(on_done)
    if on_failed:
        task.signals.failed.connect(on_failed)
    task.signals.done.connect(finish)
    task.signals.failed.connect(finish)
    _running.add(task)
    QThreadPool.globalInstance().start(task)


# ---- 録音（押して話す） ----

class Recorder:
    MAX_SECONDS = 180

    def __init__(self) -> None:
        self.chunks: list[np.ndarray] = []
        self.stream = None
        self.level = 0.0
        self.started = 0.0

    @property
    def recording(self) -> bool:
        return self.stream is not None

    @property
    def seconds(self) -> float:
        return time.monotonic() - self.started if self.recording else 0.0

    def start(self) -> None:
        import sounddevice as sd

        self.chunks = []

        def callback(data, frames, time_info, status):  # noqa: ARG001
            self.chunks.append(data[:, 0].copy())
            self.level = float(np.sqrt(np.mean(np.square(data))))

        self.stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", callback=callback)
        self.stream.start()
        self.started = time.monotonic()

    def stop(self) -> np.ndarray:
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()
            self.stream = None
        self.level = 0.0
        return np.concatenate(self.chunks) if self.chunks else np.zeros(0, dtype=np.float32)


# ---- 読み上げ（Windows 標準の日本語音声） ----

class Speaker:
    def __init__(self) -> None:
        engines = QTextToSpeech.availableEngines()
        engine = "winrt" if "winrt" in engines else ("sapi" if "sapi" in engines else None)
        self.tts = QTextToSpeech(engine) if engine else QTextToSpeech()
        self.tts.setLocale(QLocale(QLocale.Language.Japanese, QLocale.Country.Japan))
        voices = [v for v in self.tts.availableVoices() if v.locale().language() == QLocale.Language.Japanese]
        self.available = bool(voices)
        if voices:
            self.tts.setVoice(voices[0])

    def say(self, text: str) -> None:
        if self.available:
            self.tts.stop()
            self.tts.say(text)

    def stop(self) -> None:
        self.tts.stop()


# ---- AIモデル・音声認識 ----

class Services:
    def __init__(self, store: Store):
        self.store = store
        self.qset = load_question_set()
        self._asr = None
        self._asr_name: str | None = None
        self._lock = threading.Lock()
        self._clients: dict[str, OllamaClient] = {}

    def vocabulary(self) -> list[str]:
        # Whisper は用語の後ろ側を優先して使うため、登録した用語（社員名など）を後ろに置く
        return list(dict.fromkeys(load_vocabulary() + [w for w, _ in self.store.terms()]))

    def asr(self):
        from accident_report.speech.asr import Transcriber

        with self._lock:
            name = self.store.setting("asr_model")
            if self._asr is None or self._asr_name != name:
                self._asr = Transcriber(name, vocabulary=self.vocabulary())
                self._asr_name = name
            else:
                self._asr.vocabulary = self.vocabulary()
            return self._asr

    def client(self, setting_key: str) -> OllamaClient:
        name = self.store.setting(setting_key)
        if name not in self._clients:
            self._clients[name] = OllamaClient(model=name)
        return self._clients[name]

    def extractor(self):
        def run(items, known, types, question, answer):
            return extract(self.client("extract_model"), items, known, types, question, answer)
        return run

    def check_ollama(self) -> str | None:
        """Ollama と必要なモデルがあるか。問題がなければ None、あれば説明文。"""
        try:
            with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=3) as r:
                names = {m["name"] for m in json.load(r).get("models", [])}
        except OSError:
            return "AI（Ollama）が起動していません。Ollama を起動してください。"
        missing = [self.store.setting(k) for k in ("extract_model", "writer_model")
                   if self.store.setting(k) not in names]
        if missing:
            return f"AIモデルがありません: {', '.join(missing)}（ollama pull で導入してください）"
        return None
