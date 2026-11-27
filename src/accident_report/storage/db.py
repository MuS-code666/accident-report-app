"""案件データ・利用者・用語・設定の保存（SQLite）。

保存先は data/ の下（Git 対象外）。端末外には送らない。
聞き取りは1回答ごとに自動保存し、アプリを閉じても途中から再開できる。
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from accident_report import PROJECT_ROOT

DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_DB = DATA_DIR / "app.db"

STATUS_INTERVIEW = "聞き取り中"
STATUS_REVIEW = "文章確認中"
STATUS_EXPORTED = "出力済み"

TERM_CATEGORIES = ["社員", "重機・車両", "設備", "場所", "その他"]

DEFAULT_SETTINGS = {
    "delete_audio_after_export": "1",  # 報告書を出力したら録音を削除する
    "tts_enabled": "1",  # 質問を読み上げる
    "extract_model": "qwen3:4b",
    "writer_model": "qwen3:8b",
    "asr_model": "large-v3-turbo",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    role TEXT NOT NULL DEFAULT '作業員'
);
CREATE TABLE IF NOT EXISTS terms (
    id INTEGER PRIMARY KEY,
    word TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL DEFAULT 'その他'
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cases (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    reporter TEXT NOT NULL,
    status TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    engine_json TEXT,
    draft_json TEXT,
    output_path TEXT
);
"""


@dataclass
class CaseRow:
    id: int
    created_at: datetime
    updated_at: datetime
    reporter: str
    status: str
    title: str
    engine: dict | None
    draft: dict | None
    output_path: str | None


def _locked(fn):
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return fn(self, *args, **kwargs)
    wrapper.__name__, wrapper.__doc__ = fn.__name__, fn.__doc__
    return wrapper


class Store:
    def __init__(self, path: Path = DEFAULT_DB):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # 画面とバックグラウンド処理の両方から使うため、ロックで1つずつ実行する
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self._lock = threading.RLock()
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        for k, v in DEFAULT_SETTINGS.items():
            self.conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (k, v))
        self.conn.commit()

    @_locked
    def close(self) -> None:
        self.conn.close()

    # ---- 利用者 ----
    @_locked
    def users(self) -> list[tuple[str, str]]:
        return [(r["name"], r["role"]) for r in self.conn.execute("SELECT name, role FROM users ORDER BY id")]

    @_locked
    def add_user(self, name: str, role: str = "作業員") -> None:
        name = name.strip()
        if name:
            self.conn.execute("INSERT OR REPLACE INTO users(name, role) VALUES (?, ?)", (name, role))
            self.conn.commit()

    @_locked
    def remove_user(self, name: str) -> None:
        self.conn.execute("DELETE FROM users WHERE name = ?", (name,))
        self.conn.commit()

    # ---- 用語（社員・重機・設備などのマスターデータ） ----
    @_locked
    def terms(self) -> list[tuple[str, str]]:
        return [(r["word"], r["category"]) for r in self.conn.execute("SELECT word, category FROM terms ORDER BY category, word")]

    @_locked
    def add_term(self, word: str, category: str = "その他") -> None:
        word = word.strip()
        if word:
            self.conn.execute("INSERT OR REPLACE INTO terms(word, category) VALUES (?, ?)", (word, category))
            self.conn.commit()

    @_locked
    def remove_term(self, word: str) -> None:
        self.conn.execute("DELETE FROM terms WHERE word = ?", (word,))
        self.conn.commit()

    # ---- 設定 ----
    @_locked
    def setting(self, key: str) -> str:
        row = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else DEFAULT_SETTINGS.get(key, "")

    @_locked
    def set_setting(self, key: str, value: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)", (key, value))
        self.conn.commit()

    # ---- 案件 ----
    @_locked
    def create_case(self, reporter: str) -> int:
        now = datetime.now().isoformat(timespec="seconds")
        cur = self.conn.execute(
            "INSERT INTO cases(created_at, updated_at, reporter, status) VALUES (?, ?, ?, ?)",
            (now, now, reporter, STATUS_INTERVIEW))
        self.conn.commit()
        return int(cur.lastrowid)

    @_locked
    def save_case(self, case_id: int, *, engine: dict | None = None, draft: dict | None = None,
                  status: str | None = None, title: str | None = None, output_path: str | None = None) -> None:
        fields, values = ["updated_at = ?"], [datetime.now().isoformat(timespec="seconds")]
        if engine is not None:
            fields.append("engine_json = ?")
            values.append(json.dumps(engine, ensure_ascii=False))
        if draft is not None:
            fields.append("draft_json = ?")
            values.append(json.dumps(draft, ensure_ascii=False))
        for name, v in (("status", status), ("title", title), ("output_path", output_path)):
            if v is not None:
                fields.append(f"{name} = ?")
                values.append(v)
        values.append(case_id)
        self.conn.execute(f"UPDATE cases SET {', '.join(fields)} WHERE id = ?", values)
        self.conn.commit()

    @_locked
    def load_case(self, case_id: int) -> CaseRow:
        r = self.conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
        if r is None:
            raise KeyError(case_id)
        return self._row(r)

    @_locked
    def list_cases(self, include_exported: bool = True) -> list[CaseRow]:
        sql = "SELECT * FROM cases"
        if not include_exported:
            sql += f" WHERE status != '{STATUS_EXPORTED}'"
        return [self._row(r) for r in self.conn.execute(sql + " ORDER BY updated_at DESC")]

    @_locked
    def delete_case(self, case_id: int) -> None:
        self.conn.execute("DELETE FROM cases WHERE id = ?", (case_id,))
        self.conn.commit()
        delete_audio(case_id, self.path.parent)

    @staticmethod
    def _row(r: sqlite3.Row) -> CaseRow:
        return CaseRow(
            id=r["id"], created_at=datetime.fromisoformat(r["created_at"]),
            updated_at=datetime.fromisoformat(r["updated_at"]), reporter=r["reporter"],
            status=r["status"], title=r["title"],
            engine=json.loads(r["engine_json"]) if r["engine_json"] else None,
            draft=json.loads(r["draft_json"]) if r["draft_json"] else None,
            output_path=r["output_path"])


# ---- 録音ファイル ----

def audio_dir(case_id: int, data_dir: Path = DATA_DIR) -> Path:
    return data_dir / "audio" / f"case{case_id:05d}"


def delete_audio(case_id: int, data_dir: Path = DATA_DIR) -> bool:
    d = audio_dir(case_id, data_dir)
    if d.exists():
        shutil.rmtree(d)
        return True
    return False
