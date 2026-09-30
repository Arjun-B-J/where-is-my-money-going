"""Evaluating chat: does the answer contain the right figure?

The categoriser eval (`runner.py`) asks whether a label is right. This one asks
the question chat exists for: given a question about the data, does the answer
state the exact figure? It has two arms, so the agent is measured against what
it replaced:

* ``agent``    the tool-calling agent the chat routes use (`app.agent.loop`)
* ``summary``  the chat before it: a fixed summary pasted into the prompt

Every run builds its own SQLite file in a temporary directory, from the
synthetic generator with a fixed seed and end date. The rows are categorised
deterministically (the seeded rules, then `KEYWORD_CATEGORIES` for what the rules
miss) and people are detected, so the questions have stable answers. The
user's own database is never opened.

Expected figures are computed while the eval runs, by `reference_value`: plain
SQL over the table, written separately from `app.agent.tools` and sharing no
code with it. If the eval graded the tools with the tools, a wrong date filter
would produce a wrong answer and a matching wrong expectation, and score 100%.

As in `runner.py`, a model that is unreachable, not pulled, or unable to call
tools stops the run before any question is asked. A 0% from a model that never
answered would read as the agent being bad at the job.
"""
from __future__ import annotations

import json
import tempfile
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.agent.grounding import extract_figures, numbers_in_text
from app.agent.loop import cannot_call_tools, run_agent, run_summary, supports_tools
from app.agent.tools import TOOL_SCHEMAS
from app.clock import utc_now
from app.db import Base
from app.demo.generator import generate_transactions
from app.evals.dataset import DATA_DIR, DatasetError, dataset_fingerprint
from app.evals.metrics import percentile
from app.evals.runner import EvalAbortedError, model_metadata
from app.ingest.loader import load_transactions
from app.llm.client import LLMClient
from app.llm.prompts import CATEGORY_NAMES
from app.models import TagSource, Transaction, TxnDirection, TxnSource
from app.rules.engine import RuleEngine
from app.seed import seed_all
from app.services.cross_source import CARD_PAYMENT_MARKERS
from app.services.friend_detector import detect_friends, link_detected_friends

CHAT_PATH = DATA_DIR / "chat_v1.jsonl"
CHAT_ARMS: tuple[str, ...] = ("agent", "summary")

# The eval's world. The questions say "last month" and "this year", so today is
# fixed too, and the data ends on it.
EVAL_TODAY = date(2026, 8, 31)
EVAL_END = datetime(2026, 8, 31)
EVAL_SEED = 42
EVAL_MONTHS = 12

KINDS = (
    "month_total", "payee_total", "count", "largest", "people",
    "relative_date", "in_and_out", "recurring", "coverage",
)
OPS = ("sum", "count", "max")
_SPEC_KEYS = frozenset({
    "op", "direction", "category", "payee_contains", "start", "end", "exclude_internal", "minus",
})

# What the seeded rules leave untagged in the synthetic data, matched on the
# narration. Rows that match nothing here stay uncategorised on purpose: the
# opaque payees exist so that "how much is uncategorised" has an answer.
KEYWORD_CATEGORIES: tuple[tuple[str, str], ...] = (
    ("LOAN TO FRIEND", "loan_given"),
    ("LOAN FROM FRIEND", "loan_taken"),
    ("REPAYMENT", "loan_repayment"),
    ("-RENT", "rent"),
    ("HOME SERVICES", "utilities"),
    ("MUSIC", "subscriptions"),
    ("STREAMING", "subscriptions"),
    ("FOOD DELIVERY", "food"),
    ("RESTAURANT", "food"),
    ("CABS", "transport"),
    ("BIKE TAXI", "transport"),
    ("SUPERMARKET", "groceries"),
    ("GROCERY", "groceries"),
    ("MARKETPLACE", "shopping"),
    ("FASHION", "shopping"),
    ("SPORTS", "shopping"),
)

_LOAN_CATEGORIES = frozenset({"loan_given", "loan_taken", "loan_repayment"})


# ---------------------------------------------------------------------------
# Questions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChatQuestion:
    id: str
    kind: str
    question: str
    # One query spec per figure the answer must contain.
    expect: tuple[dict[str, Any], ...]
    # Words the answer must contain. Each entry lists acceptable alternatives.
    expect_text: tuple[tuple[str, ...], ...] = ()
    # For a question the data cannot answer: any rupee figure in the answer is
    # wrong, including ₹0, because "nothing recorded" is not "nothing spent".
    expect_no_figures: bool = False
    note: str = ""


def _check_spec(spec: Any, where: str) -> dict[str, Any]:
    if not isinstance(spec, dict):
        raise DatasetError(f"{where}: a query spec must be an object")
    unknown = set(spec) - _SPEC_KEYS
    if unknown:
        raise DatasetError(f"{where}: unknown spec keys {sorted(unknown)}")
    if spec.get("op") not in OPS:
        raise DatasetError(f"{where}: op must be one of {OPS}")
    if spec.get("direction") not in (None, "debit", "credit"):
        raise DatasetError(f"{where}: direction must be debit, credit or absent")
    if spec.get("category") is not None and spec["category"] not in CATEGORY_NAMES:
        raise DatasetError(f"{where}: category {spec['category']!r} is not in the taxonomy")
    for key in ("start", "end"):
        if spec.get(key) is not None:
            try:
                date.fromisoformat(spec[key])
            except (TypeError, ValueError) as e:
                raise DatasetError(f"{where}: {key} must be YYYY-MM-DD") from e
    if "minus" in spec:
        minus = _check_spec(spec["minus"], f"{where} (minus)")
        if minus["op"] != spec["op"]:
            raise DatasetError(f"{where}: both sides of a difference need the same op")
    return spec


def load_questions(path: Path = CHAT_PATH) -> list[ChatQuestion]:
    """Load and validate the questions.

    Strict for the same reason `load_golden` is: a malformed spec would score as
    a wrong answer and read as a weakness of the agent.
    """
    questions: list[ChatQuestion] = []
    seen: set[str] = set()
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        where = f"{path.name}:{number}"
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as e:
            raise DatasetError(f"{where}: not JSON ({e})") from e

        question_id = raw.get("id", "")
        if not question_id or question_id in seen:
            raise DatasetError(f"{where}: missing or duplicate id {question_id!r}")
        seen.add(question_id)
        if raw.get("kind") not in KINDS:
            raise DatasetError(f"{question_id}: unknown kind {raw.get('kind')!r}")
        if not str(raw.get("question", "")).strip():
            raise DatasetError(f"{question_id}: empty question")

        expect = tuple(_check_spec(spec, question_id) for spec in raw.get("expect") or ())
        expect_text = tuple(
            (entry,) if isinstance(entry, str) else tuple(entry)
            for entry in raw.get("expect_text") or ()
        )
        no_figures = bool(raw.get("expect_no_figures", False))
        if not expect and not expect_text:
            raise DatasetError(f"{question_id}: nothing to check the answer against")
        if no_figures and expect:
            raise DatasetError(f"{question_id}: expects figures and no figures at once")

        questions.append(ChatQuestion(
            id=question_id, kind=raw["kind"], question=raw["question"],
            expect=expect, expect_text=expect_text, expect_no_figures=no_figures,
            note=raw.get("note", ""),
        ))
    return questions


# ---------------------------------------------------------------------------
# The eval database
# ---------------------------------------------------------------------------


def _tag_by_keyword(txn: Transaction) -> None:
    description = (txn.raw_description or "").upper()
    for keyword, category in KEYWORD_CATEGORIES:
        if keyword in description:
            txn.category = category
            txn.tag_source = TagSource.RULE
            txn.tag_confidence = 1.0
            txn.tag_reason = f"chat eval keyword {keyword!r}"
            # As RuleEngine.apply does, so the people ledger treats these alike.
            txn.is_loan = category in _LOAN_CATEGORIES
            return


def populate(
    db: Session,
    *,
    seed: int = EVAL_SEED,
    months: int = EVAL_MONTHS,
    end_date: datetime = EVAL_END,
) -> int:
    """Fill an empty database with the eval's synthetic year. Returns the row count."""
    seed_all(db)
    records = generate_transactions(seed=seed, months=months, end_date=end_date)
    load_transactions(db, records)
    rules = RuleEngine(db)
    for txn in db.query(Transaction).all():
        if not rules.apply(txn):
            _tag_by_keyword(txn)
    db.commit()
    # The function's own default threshold rather than the user's setting, so a
    # .env file cannot change who counts as a person in a benchmark.
    link_detected_friends(db, detect_friends(db))
    return len(records)


@contextmanager
def eval_database() -> Iterator[Session]:
    """A populated throwaway database, deleted afterwards."""
    with tempfile.TemporaryDirectory(prefix="wimmg-chat-eval-", ignore_cleanup_errors=True) as tmp:
        engine = create_engine(f"sqlite:///{(Path(tmp) / 'chat_eval.db').as_posix()}")
        try:
            Base.metadata.create_all(engine)
            with Session(engine) as db:
                populate(db)
                yield db
        finally:
            # Windows will not delete an SQLite file that still has a connection.
            engine.dispose()


# ---------------------------------------------------------------------------
# Expected figures, computed independently of the tools
# ---------------------------------------------------------------------------


def _like(fragment: str) -> str:
    escaped = fragment.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def reference_value(db: Session, spec: Mapping[str, Any]) -> float:
    """Compute one query spec in SQL.

    `category: "uncategorized"` includes rows nothing has categorised, as the
    dashboard does. `exclude_internal` drops card-bill payments made from a bank
    account, the definition the dashboard's "spend" uses. Dates are calendar
    dates, both ends inclusive.
    """
    clauses = ["is_duplicate = 0"]
    params: dict[str, Any] = {}
    # SQLAlchemy stores an enum column as the member's name, not its value.
    if spec.get("direction"):
        clauses.append("direction = :direction")
        params["direction"] = TxnDirection(spec["direction"]).name
    if spec.get("category"):
        clauses.append("COALESCE(category, 'uncategorized') = :category")
        params["category"] = spec["category"]
    if spec.get("payee_contains"):
        clauses.append(
            "(LOWER(raw_description) LIKE :payee ESCAPE '\\' "
            "OR LOWER(COALESCE(merchant_normalized, '')) LIKE :payee ESCAPE '\\')"
        )
        params["payee"] = _like(spec["payee_contains"])
    if spec.get("start"):
        clauses.append("substr(posted_at, 1, 10) >= :start")
        params["start"] = spec["start"]
    if spec.get("end"):
        clauses.append("substr(posted_at, 1, 10) <= :end")
        params["end"] = spec["end"]
    if spec.get("exclude_internal"):
        markers = []
        for index, marker in enumerate(CARD_PAYMENT_MARKERS):
            markers.append(f"LOWER(raw_description) LIKE :marker{index} ESCAPE '\\'")
            params[f"marker{index}"] = _like(marker)
        clauses.append(
            "NOT (direction = :debit AND source IN (:bank, :bank_2) "
            f"AND ({' OR '.join(markers)}))"
        )
        params.update(
            debit=TxnDirection.DEBIT.name,
            bank=TxnSource.BANK.name,
            bank_2=TxnSource.BANK_SECONDARY.name,
        )

    aggregate = {
        "sum": "COALESCE(SUM(amount), 0)",
        "count": "COUNT(*)",
        "max": "COALESCE(MAX(amount), 0)",
    }[spec["op"]]
    value = float(
        db.execute(
            text(f"SELECT {aggregate} FROM transactions WHERE {' AND '.join(clauses)}"), params
        ).scalar() or 0
    )
    if "minus" in spec:
        value -= reference_value(db, spec["minus"])
    return round(value, 2)


@dataclass(frozen=True)
class Expected:
    op: str
    value: float

    def found_in(self, numbers: Sequence[float]) -> bool:
        """Whether any number in the answer is this figure.

        A count must be exact. An amount may be within ₹1 or 0.5%, which allows
        rounding to the rupee and a lakh figure written to two decimals. A net
        balance is compared by size, because an answer states who owes whom in
        words and the amount without a sign.
        """
        target = abs(self.value)
        if self.op == "count":
            return any(number == target for number in numbers)
        tolerance = max(1.0, 0.005 * target)
        return any(abs(number - target) <= tolerance for number in numbers)


def expected_figures(db: Session, question: ChatQuestion) -> list[Expected]:
    return [Expected(spec["op"], reference_value(db, spec)) for spec in question.expect]


def score_answer(
    answer: str,
    expected: Sequence[Expected],
    expect_text: Sequence[Sequence[str]],
    *,
    no_figures: bool = False,
) -> bool:
    """Every expected figure appears, and every expected word (or an alternative) does.

    With `no_figures`, the answer must also state no rupee figure at all.
    """
    if no_figures and extract_figures(answer):
        return False
    numbers = numbers_in_text(answer)
    if not all(figure.found_in(numbers) for figure in expected):
        return False
    lowered = answer.lower()
    return all(any(word.lower() in lowered for word in words) for words in expect_text)


# ---------------------------------------------------------------------------
# Running an arm
# ---------------------------------------------------------------------------


@dataclass
class ChatAnswer:
    id: str
    kind: str
    question: str
    expected: list[dict[str, Any]]
    expect_text: list[list[str]]
    expect_no_figures: bool
    answer: str
    ok: bool
    error: str | None
    correct: bool
    grounded: bool
    ungrounded: list[str]
    corrective_round: bool
    tool_calls: int
    latency_ms: int
    trace: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ChatRunResult:
    arm: str
    model: str | None
    answers: list[ChatAnswer]
    transactions: int
    wall_seconds: float
    warmup_seconds: float | None = None
    meta: dict = field(default_factory=dict)
    today: date = EVAL_TODAY


async def _preflight(llm: LLMClient, arm: str) -> float:
    """Health, then one warm-up call. Returns the warm-up time in seconds.

    The first call loads the weights, which can take longer than every question
    after it, so it is timed separately and kept out of the latency figures.
    """
    name = getattr(llm, "model", "the model")
    health = await llm.health()
    if not health.get("ok"):
        raise EvalAbortedError(f"the model server is not reachable: {health.get('error')}")
    if not health.get("model_pulled"):
        raise EvalAbortedError(f"{name} is not pulled. Run: ollama pull {name}")

    started = time.perf_counter()
    probe = [{"role": "user", "content": "Reply with the single word: ready."}]
    if arm == "agent":
        if not supports_tools(llm):
            raise EvalAbortedError("this client cannot call tools, which the agent arm needs")
        turn = await llm.with_tools(probe, TOOL_SCHEMAS)
        failed, error = turn.failed, turn.error
    else:
        result = await llm.complete(probe, temperature=0.0)
        failed, error = result.failed, result.error
    if failed:
        if cannot_call_tools(error):
            raise EvalAbortedError(
                f"{name} cannot call tools, so the agent arm cannot run on it. "
                "Choose a model with tool support."
            )
        raise EvalAbortedError(
            f"{name} is reachable but returned no usable answer on the warm-up call "
            f"({error}). Check `ollama run {name}` by hand before trusting any numbers."
        )
    return time.perf_counter() - started


async def run_chat_eval(
    questions: Sequence[ChatQuestion],
    *,
    arm: str,
    llm: LLMClient,
    today: date = EVAL_TODAY,
) -> ChatRunResult:
    """Ask every question through one arm, in order, one at a time.

    Sequential on purpose: an agent answer is several dependent model calls, and
    running questions side by side would make the latency figures measure the
    queue rather than the answer.
    """
    if arm not in CHAT_ARMS:
        raise ValueError(f"unknown arm {arm!r}; choose from {CHAT_ARMS}")
    warmup = await _preflight(llm, arm)
    meta = await model_metadata(llm)

    answers: list[ChatAnswer] = []
    started = time.perf_counter()
    with eval_database() as db:
        transactions = db.query(Transaction).count()
        for question in questions:
            expected = expected_figures(db, question)
            history = [{"role": "user", "content": question.question}]
            asked = time.perf_counter()
            if arm == "agent":
                result = await run_agent(llm, db, history, today=today)
            else:
                result = await run_summary(llm, db, history)
            latency = int((time.perf_counter() - asked) * 1000)
            answers.append(ChatAnswer(
                id=question.id,
                kind=question.kind,
                question=question.question,
                expected=[asdict(figure) for figure in expected],
                expect_text=[list(words) for words in question.expect_text],
                expect_no_figures=question.expect_no_figures,
                answer=result.text,
                ok=result.ok,
                error=result.error,
                correct=result.ok and score_answer(
                    result.text, expected, question.expect_text,
                    no_figures=question.expect_no_figures,
                ),
                grounded=result.grounded,
                ungrounded=result.ungrounded,
                corrective_round=result.corrective_round,
                tool_calls=len(result.trace),
                latency_ms=latency,
                trace=result.trace,
            ))
    return ChatRunResult(
        arm=arm,
        model=getattr(llm, "model", None),
        answers=answers,
        transactions=transactions,
        wall_seconds=time.perf_counter() - started,
        warmup_seconds=warmup,
        meta=meta,
        today=today,
    )


# ---------------------------------------------------------------------------
# Scores and records
# ---------------------------------------------------------------------------


def chat_scores(answers: Sequence[ChatAnswer]) -> dict[str, Any]:
    """Headline numbers for one run.

    A question the model failed to answer is wrong, not skipped, and is counted
    in `failures` as well. The grounded rate is over answered questions: it
    says how often an answer that was given stated only figures its data held.
    """
    n = len(answers)
    if n == 0:
        return {"n": 0}
    answered = [a for a in answers if a.ok]
    latencies = [float(a.latency_ms) for a in answered]
    by_kind: dict[str, dict[str, Any]] = {}
    for kind in sorted({a.kind for a in answers}):
        members = [a for a in answers if a.kind == kind]
        by_kind[kind] = {"n": len(members), "accuracy": sum(a.correct for a in members) / len(members)}
    return {
        "n": n,
        "accuracy": sum(a.correct for a in answers) / n,
        "answered_rate": len(answered) / n,
        "failures": n - len(answered),
        "grounded_rate": sum(a.grounded for a in answered) / len(answered) if answered else None,
        "corrective_rounds": sum(a.corrective_round for a in answers),
        "mean_tool_calls": sum(a.tool_calls for a in answers) / n,
        "latency_ms_p50": percentile(latencies, 0.50),
        "latency_ms_p95": percentile(latencies, 0.95),
        "by_kind": by_kind,
    }


def build_chat_record(result: ChatRunResult, scores: dict[str, Any]) -> dict[str, Any]:
    """Everything needed to trace a score back to the answer behind it."""
    return {
        "suite": "chat",
        "run_at": utc_now().isoformat(timespec="seconds") + "Z",
        "dataset": CHAT_PATH.name,
        "dataset_sha1": dataset_fingerprint(CHAT_PATH),
        "arm": result.arm,
        "model": result.model,
        "today": result.today.isoformat(),
        "data": {
            "generator_seed": EVAL_SEED,
            "months": EVAL_MONTHS,
            "end_date": EVAL_END.date().isoformat(),
            "transactions": result.transactions,
        },
        "wall_seconds": round(result.wall_seconds, 2),
        "warmup_seconds": round(result.warmup_seconds, 2) if result.warmup_seconds else None,
        "meta": result.meta,
        "scores": scores,
        "answers": [asdict(answer) for answer in result.answers],
    }


def save_chat_record(record: dict[str, Any], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = record["run_at"].replace(":", "").replace("-", "")[:15]
    label = (record["model"] or "no-model").replace(":", "-").replace("/", "-")
    path = out_dir / f"{stamp}_chat-{record['arm']}_{label}.json"
    # Unescaped, because the answers are full of ₹ and the file is read by people.
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _seconds(value: float | None) -> str:
    return "n/a" if value is None else f"{value / 1000:.1f}s"


def chat_table(records: Sequence[dict[str, Any]]) -> str:
    """One row per run, headline metrics only."""
    lines = [
        "| Arm | Model | Accuracy | Grounded | Answered | Tool calls | Corrected | p50 | p95 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for record in records:
        s = record["scores"]
        lines.append(
            f"| {record['arm']} | {record['model'] or 'none'} | {_pct(s['accuracy'])} "
            f"| {_pct(s['grounded_rate'])} | {_pct(s['answered_rate'])} "
            f"| {s['mean_tool_calls']:.1f} | {s['corrective_rounds']} "
            f"| {_seconds(s['latency_ms_p50'])} | {_seconds(s['latency_ms_p95'])} |"
        )
    return "\n".join(lines)
