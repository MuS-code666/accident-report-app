"""日時の読み取りと書式（報告書の書き方: 2026年10月23日 14時ごろ）。"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, timedelta


_KANJI = {"〇": 0, "零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _kanji_to_int(s: str) -> int | None:
    if s.isdigit():
        return int(s)
    if not s or any(ch not in _KANJI and ch != "十" for ch in s):
        return None
    if "十" in s:
        a, _, b = s.partition("十")
        return (_KANJI[a] if a else 1) * 10 + (_KANJI[b] if b else 0)
    return int("".join(str(_KANJI[ch]) for ch in s))


_NUM = r"([0-9]+|[〇零一二三四五六七八九十]+)"


def normalize_datetime(value: str, today: date | None = None) -> str | None:
    """聞き取った日時を「2026年10月23日 14時ごろ」の形にする。日付が読めなければ None。

    年が言われていなければ、今日より後にならない年にする。「昨日」「今日」「おととい」も扱う。
    時刻がなければ日付だけを返す（利用者が確認画面で補う）。
    """
    today = today or date.today()
    s = unicodedata.normalize("NFKC", value)

    d: date | None = None
    m = re.search(rf"(?:{_NUM}年)?\s*{_NUM}月\s*{_NUM}日", s)
    m2 = re.search(r"(?:(\d{4})[/-])?(\d{1,2})/(\d{1,2})", s)
    rel = {"一昨日": 2, "おととい": 2, "昨日": 1, "きのう": 1, "今日": 0, "本日": 0, "きょう": 0}
    try:
        if m or m2:
            y, mo, da = (m.groups() if m else m2.groups())
            mo_i, da_i = _kanji_to_int(mo), _kanji_to_int(da)
            y_i = _kanji_to_int(y) if y else None
            if y_i is not None and y_i < 100:
                y_i += 2000
            if y_i is None:
                y_i = today.year
                if date(y_i, mo_i, da_i) > today:
                    y_i -= 1
            d = date(y_i, mo_i, da_i)
        else:
            for word, back in rel.items():
                if word in s:
                    d = today - timedelta(days=back)
                    break
    except (TypeError, ValueError):
        d = None
    if d is None:
        return None

    hour: int | None = None
    hm = re.search(rf"{_NUM}\s*時", s) or re.search(r"(\d{1,2}):\d{2}", s)
    if hm:
        hour = _kanji_to_int(hm.group(1))
        pm = re.search(r"午後|夕方|夜", s) and hour is not None and hour < 12
        if pm:
            hour += 12
    text = f"{d.year}年{d.month}月{d.day}日"
    if hour is not None and 0 <= hour <= 24:
        text += f" {hour}時ごろ"
    return text


def has_date_and_hour(value: str, today: date | None = None) -> bool:
    """日付と時刻の両方が読み取れるか（聞き取りの「日時」項目が足りているかの判定）。"""
    text = normalize_datetime(value, today)
    return bool(text and "時ごろ" in text)
