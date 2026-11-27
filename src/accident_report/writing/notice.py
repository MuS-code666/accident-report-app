"""通知用報告書の文章生成と根拠チェック。"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

from accident_report.interview.engine import InterviewEngine
from accident_report.llm import OllamaClient
from accident_report.models.interview import ItemStatus

WorkCategory = Literal["運搬（車輌・フォーク）", "積降し", "選別", "破砕", "その他"]

SYSTEM_PROMPT = """あなたは工場の事故報告書（通知用）の文章を作ります。
聞き取りの記録だけを材料に、書面にふさわしい文章へ整理してJSONで返してください。

守ること:
- 聞き取っていない事実を補ったり創作したりしない。
- 人名・重機名・日時・数量は記録の表記をそのまま使う。
- 口語を書き言葉にし、主語・対象物・動作をはっきり書く。冗長な表現や重複を除く。
- 文末はすべて体言止めにする（例:「後退中、シャッターの柱に接触。」「直ちに作業を中止し、班長に報告。」「けが人なし。」）。
  「です・ます」や「〜した。」で終えない。
- overview（事故概要）は時系列の箇条書き。事故前の作業、作業指示や作業開始の経緯、事故直前の動作、事故発生時の状況、被害、事故後の対応の順に、記録にある分だけ書く。
  見出しや項目名は書かず、各文には具体的な内容（誰が・何を・どうした）を書く。
- 伝聞は「〜とのこと。」、推測は「〜と推測（報告者談）。」のように区別して書く。
- 事故当事者の責任や過失を断定しない。
- 分からない事項は無理に文章にしない。
- 各文の evidence には、根拠にした回答ID（A001 など）を必ず入れる。
- cause と countermeasure は報告者が述べた内容だけを書く。整理済みの項目に「発生原因」「対策案」があれば必ず書き、
  述べていなければ空にする。
- involved_persons は事故当事者（事故を起こした人・けがをした人）の氏名だけを並べる。記録に出てくる表記のまま書き、
  役職や説明は付けない。「私」が当事者なら報告者の氏名を書く。氏名が記録になければ空にする。
- work_category は作業内容に最も近いものを選ぶ。どれにも当たらなければ「その他」とし、work_category_other に作業名を書く。

書き換えの例（回答をそのまま写さず、このように報告書の文に直す）:
- 回答「ローダーでバックしてたら、後ろのコンベヤーの架台にぶつかっちゃいました」
  → 「ホイールローダーで後退中、後方のコンベヤー架台に接触。」
- 回答「すぐ機械を止めて、職長の木村さんに電話しました」→ 「直ちに機械を停止し、職長の木村氏に電話で報告。」
- 回答「バケットの先が少し欠けました」→ 「バケット先端の一部が欠損。」
- 回答「足元をよく見てなかったからだと思います」→ 「足元の確認不足と推測（報告者談）。」
- 回答「通路に物を置かないようにすればいいと思います」→ 「通路への物品の仮置き禁止（報告者案）。」"""


class Sentence(BaseModel):
    text: str = Field(min_length=1)
    evidence: list[str] = Field(min_length=1)


class NoticeDraft(BaseModel):
    involved_persons: list[str] = Field(default_factory=list)  # 事故当事者の氏名（通知用の「氏名」欄）
    work_category: WorkCategory
    work_category_other: str | None = None
    overview: list[Sentence]
    cause: list[Sentence] = Field(default_factory=list)
    countermeasure: list[Sentence] = Field(default_factory=list)


class CheckIssue(BaseModel):
    section: str
    text: str
    problem: str


def build_prompt(engine: InterviewEngine, reporter: str = "") -> str:
    lines = [f"# 報告者の氏名: {reporter}"] if reporter else []
    lines.append("# 聞き取りの記録（回答ID: 質問 → 回答）")
    for a in engine.answers:
        lines.append(f"- {a.answer_id}: {a.question_text} → {a.text}")
    lines.append("\n# 整理済みの項目（項目: 値 [情報の種類]）")
    for item_id, st in engine.states.items():
        if st.value and st.status in (ItemStatus.ANSWERED, ItemStatus.INSUFFICIENT):
            label = engine.qset.item(item_id).label
            lines.append(f"- {label}: {st.value} [{st.source_type.value if st.source_type else ''}]")
    unresolved = engine.unresolved()
    if unresolved:
        lines.append("\n# 未確認の事項（文章にしない）")
        lines += [f"- {label}（{status.value}）" for label, status in unresolved]
    return "\n".join(lines)


def generate_notice(client: OllamaClient, engine: InterviewEngine, reporter: str = "") -> NoticeDraft:
    draft = client.generate_json(SYSTEM_PROMPT, build_prompt(engine, reporter), NoticeDraft, max_tokens=1500)
    for section in ("overview", "cause", "countermeasure"):
        for sent in getattr(draft, section):
            sent.text = to_taigen(sent.text)
    polish_taigen(client, draft)
    return draft


POLISH_PROMPT = """事故報告書の文の「文末」だけを直してください。文末の「〜した」「〜た」「〜です」「〜ます」を取り、
名詞で終わる形（体言止め）にします。文末以外は変えません。
- 文に書かれていない事実を足さない。人名・数量・部位はそのまま使う。
- 入力と同じ数・同じ順番で返す。各文は「。」で終える。
例:
- 「扉の柱が少し曲がった。」→「扉の柱が少し変形。」
- 「使えることを確かめた。」→「使用可能であることを確認。」
- 「班長に事故の報告をした。」→「班長に事故の報告。」
- 「フォークリフトの右後ろに擦り傷が生じた。」→「フォークリフトの右後ろに擦り傷が発生。」
- 「通路に荷物が置かれていた。」→「通路に荷物が置かれていた状態。」
- 「左右両方を確認することを報告者案。」→「左右両方の確認（報告者案）。」"""


class _Polished(BaseModel):
    sentences: list[str]


def _parts(text: str) -> list[str]:
    return [p + "。" for p in text.split("。") if p.strip()]


def _is_taigen(part: str) -> bool:
    core = re.sub(r"[（(][^）)]*[）)]。?$", "", part.strip()).rstrip("。")
    return not (NOT_TAIGEN.search(core + "。") and not core.endswith("こと"))


def polish_taigen(client: OllamaClient, draft: NoticeDraft) -> None:
    """体言止めになっていない文だけを、AIにもう一度短く書き直させる。

    書き直しは、文の数が同じで、元にない数字が増えておらず、体言止めになっている場合だけ採用する。
    """
    targets = [(sec, i, j, p) for sec in ("overview", "cause", "countermeasure")
               for i, sent in enumerate(getattr(draft, sec))
               for j, p in enumerate(_parts(sent.text)) if not _is_taigen(p)]
    if not targets:
        return
    prompt = "\n".join(f"{k + 1}. {p}" for k, (_, _, _, p) in enumerate(targets))
    try:
        out = client.generate_json(POLISH_PROMPT, prompt, _Polished, max_tokens=600)
    except Exception:  # noqa: BLE001 - 書き直せなくても下書きは使える（確認画面で知らせる）
        return
    if len(out.sentences) != len(targets):
        return
    new_parts: dict[tuple[str, int], list[str]] = {}
    for (sec, i, j, old), new in zip(targets, out.sentences):
        new = to_taigen(re.sub(r"^\d+\.\s*", "", new.strip()))
        if not _is_taigen(new) or set(_NUM.findall(new)) - set(_NUM.findall(old)):
            continue
        parts = new_parts.setdefault((sec, i), _parts(getattr(draft, sec)[i].text))
        parts[j] = new
    for (sec, i), parts in new_parts.items():
        getattr(draft, sec)[i].text = "".join(parts)


# ---- 体言止め --------------------------------------------------------------

# 「接触しました。」→「接触。」、「運転していました。」→「運転中。」のように、
# 「名詞＋する」の文末だけを機械的に直す。直せない文は check_draft で知らせる。
_SURU_ONGOING = re.compile(r"(?<=[一-鿿])(?:を)?して(?:い|おり)(?:ました|ます|た|る)。?$")
_SURU_DONE = re.compile(r"(?<=[一-鿿])(?:いた)?(?:しました|します|した|する)。?$")
# 意味を変えずに名詞で終えられる、よく出る文末（上から順に試す）
_ENDINGS = [
    (r"(?:の)?(?:報告|連絡|指示|確認|点検|作業|運転)をし(?:ました|た)$", None),  # 「報告をした」→「報告」
    (r"が生じ(?:ました|た)$", "が発生"),
    (r"(?:を)?確かめ(?:ました|た)$", "を確認"),
    (r"(?:に)?(?:当た|ぶつか)(?:りました|った|ってしまいました|ってしまった)$", "に接触"),
    (r"(?:を)?止め(?:ました|た)$", "を停止"),
    (r"(?:が)?曲が(?:りました|った)$", "が変形"),
    (r"(?:が)?へこ(?:みました|んだ)$", "がへこみ変形"),
    (r"(?:が)?(?:壊れ|割れ)(?:ました|た)$", "が破損"),
    (r"(?:が)?欠け(?:ました|た)$", "が欠損"),
    (r"傷が(?:つ|付)(?:きました|いた)$", "傷"),
    (r"は(?:い|あり)ませんでした$", "なし"),
    (r"は(?:い|あり)ません$", "なし"),
    (r"指示を受け(?:ました|た|ていた|ていました)$", "指示あり"),
]
_ENDINGS = [(re.compile(p), r) for p, r in _ENDINGS]

_STATE = re.compile(r"([てで])(?:い|おり)(?:ました|た)。?$")  # 「置かれていた」→「置かれていた状態」（名詞＋するで直せないとき）

NOT_TAIGEN = re.compile(r"(?:です|ます|ました|でした|ません|た|だ|る)。?$")


def _ending(t: str) -> str:
    core = t.rstrip("。")
    for pat, repl in _ENDINGS:
        m = pat.search(core)
        if m:
            if repl is None:  # 「事故の報告をした」→「事故の報告」
                return core[: m.start()] + re.sub(r"をし(?:ました|た)$", "", m.group(0)) + "。"
            # 置き換え先の助詞と、直前の助詞が重ならないようにする
            head = core[: m.start()]
            if head.endswith(repl[0]) and repl[0] in "がをに":
                head = head[:-1]
            return head + repl + "。"
    return t


def to_taigen(text: str) -> str:
    t = text.strip()
    if not t:
        return t
    # 複数の文がつながっている場合は、文ごとに直す
    if t.rstrip("。").count("。"):
        return "".join(to_taigen(p + "。") for p in t.split("。") if p.strip())
    new = _ending(t)
    if new != t:
        return new
    for pat, repl in ((_SURU_ONGOING, "中。"), (_SURU_DONE, "。"), (_STATE, r"\1いた状態。")):
        new = pat.sub(repl, t)
        if new != t:
            return new
    return t if t.endswith(("。", "）", ")")) else t + "。"


SELF_WORDS = ("私", "わたし", "自分", "本人", "僕", "俺")
_SELF = r"(?:私|わたし|自分|僕|俺)"
SELF_PARTY = re.compile(
    rf"(?:運転|操作|作業|操縦)(?:を)?(?:して)?(?:い)?た(?:の)?は{_SELF}|{_SELF}が(?:運転|操作|操縦|挟まれ|けが|怪我|転倒|当たっ|ぶつけ)"
    rf"|{_SELF}(?:は|が)?(?:事故の)?当事者")


def reporter_is_party(engine: InterviewEngine) -> bool:
    """報告者自身が事故当事者か（「報告者の立場」の回答や「運転していたのは私」などの言い回しから判定）。"""
    role = engine.states.get("reporter_role")
    if role and role.value and "当事者" in role.value and not re.search(r"目撃|見た|聞いた", role.value):
        return True
    return any(SELF_PARTY.search(a.text) for a in engine.answers)


def verified_persons(draft: NoticeDraft, engine: InterviewEngine, reporter: str = "") -> tuple[list[str], list[str]]:
    """AIが挙げた当事者の氏名を確かめる。(記録で確認できた氏名, 確認できなかった氏名) を返す。

    「私」などは報告者の氏名に置き換える。聞き取りの記録にも報告者名にも出てこない氏名は採用しない。
    """
    text = " ".join(a.text for a in engine.answers)
    ok: list[str] = []
    ng: list[str] = []
    for raw in draft.involved_persons:
        name = raw.strip().removesuffix("さん").strip()
        if not name:
            continue
        if name in SELF_WORDS:
            name = reporter
        if name and (name in text or name == reporter):
            if name not in ok:
                ok.append(name)
        elif name:
            ng.append(raw)
    if reporter and reporter not in ok and reporter_is_party(engine):
        ok.append(reporter)  # AIが「私」を拾わなかったときの補い
    return ok, ng


_NUM = re.compile(r"\d+(?:[.,]\d+)?")


def check_draft(draft: NoticeDraft, engine: InterviewEngine, reporter: str = "") -> list[CheckIssue]:
    """創作の疑いを機械的に検出する。

    - 根拠の回答IDが存在しない
    - 文中の数字が、根拠の回答にもどの回答にも出てこない
    """
    by_id = {a.answer_id: a.text for a in engine.answers}
    all_text = " ".join(by_id.values())
    issues: list[CheckIssue] = []
    _, unknown = verified_persons(draft, engine, reporter)
    for name in unknown:
        issues.append(CheckIssue(section="involved_persons", text=name, problem="聞き取りの記録にない氏名のため、氏名欄に入れていない"))
    for section in ("overview", "cause", "countermeasure"):
        for s in getattr(draft, section):
            if not all(_is_taigen(p) for p in _parts(s.text)):
                issues.append(CheckIssue(section=section, text=s.text, problem="体言止めになっていない"))
            missing = [e for e in s.evidence if e not in by_id]
            if missing:
                issues.append(CheckIssue(section=section, text=s.text,
                                         problem=f"存在しない回答IDを根拠にしている: {missing}"))
            cited = " ".join(by_id.get(e, "") for e in s.evidence)
            for num in _NUM.findall(s.text):
                if num not in cited and num not in all_text and _to_full(num) not in all_text:
                    issues.append(CheckIssue(section=section, text=s.text,
                                             problem=f"回答にない数字「{num}」がある"))
    return issues


def draft_to_form(engine: InterviewEngine, draft: NoticeDraft, reporter: str = ""):
    """AIの下書きと聞き取り結果から、確認画面・Excel 転記用のフォームを作る。"""
    from accident_report.export.notice_excel import NoticeForm, normalize_datetime

    def value(item_id: str) -> str:
        # 「分からない」になった項目でも、聞き取った内容があれば使う（確認画面で利用者が直せる）
        st = engine.states.get(item_id)
        return st.value if st and st.value and st.status != ItemStatus.NOT_APPLICABLE else ""

    asked_on = engine.answers[0].created_at.date() if engine.answers else None
    raw_dt = value("datetime")
    return NoticeForm(
        datetime_text=(normalize_datetime(raw_dt, asked_on) or raw_dt) if raw_dt else "",
        name="、".join(verified_persons(draft, engine, reporter)[0]),  # 氏名欄は事故当事者
        location=value("location"),
        work_category=draft.work_category,
        work_category_other=draft.work_category_other or "",
        overview=[s.text for s in draft.overview],
        cause=[s.text for s in draft.cause],
        countermeasure=[s.text for s in draft.countermeasure],
        unresolved=[f"{label}（{status.value}）" for label, status in engine.unresolved()],
    )


def _to_full(s: str) -> str:
    return s.translate(str.maketrans("0123456789.", "０１２３４５６７８９．"))
