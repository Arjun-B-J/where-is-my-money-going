"""The individual steps of the pipeline.

Each node takes the shared state and returns a partial update. LangGraph merges
updates by replacing top-level keys, which is why `_with_timing` rebuilds the
whole timings dict rather than mutating it in place.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Literal, TypedDict

from sqlalchemy.orm import Session

from app.clock import utc_now
from app.config import get_settings
from app.demo.generator import generate_transactions
from app.ingest.loader import load_directory, load_transactions
from app.ingest.records import ParsedTxn
from app.llm.client import LLMClient
from app.llm.prompts import (
    TAGGING_MEMORY,
    TAGGING_MEMORY_LINE,
    TAGGING_SCHEMA,
    TAGGING_SYSTEM,
    TAGGING_USER,
)
from app.memory import CorrectionMemory, Match
from app.models import PipelineRun, TagSource, Transaction
from app.rules.engine import RuleEngine
from app.seed import seed_all

logger = logging.getLogger(__name__)


class PipelineState(TypedDict, total=False):
    """State carried between nodes."""

    # ---- inputs ----
    mode: Literal["demo", "files"]
    seed: int
    months: int
    directory: str          # only for mode="files"

    # ---- accumulated ----
    records: list[ParsedTxn]
    inserted_ids: list[int]
    duplicates: int
    rule_tagged: int
    llm_tagged: int
    needs_review: int
    # Rows that took the user's own earlier label for the same UPI handle
    # directly, and rows classified with such labels as examples (app.memory).
    memory_decided: int
    memory_assisted: int

    # Set False when any classification call failed. The run is then explicit
    # that some rows are untagged rather than pretending they were classified.
    llm_available: bool
    llm_failed: int

    friends_detected: int
    friends_linked: int
    validator_checked: int
    validator_overridden: int

    timings_ms: dict[str, int]
    errors: list[str]


def _with_timing(state: PipelineState, node: str, started: float) -> dict[str, int]:
    timings = dict(state.get("timings_ms", {}))
    timings[node] = int((time.perf_counter() - started) * 1000)
    return timings


# ---------------------------------------------------------------------------
# Setup and extraction
# ---------------------------------------------------------------------------


def node_seed(state: PipelineState, db: Session) -> PipelineState:
    """Create default categories, rules and payee notes if missing."""
    started = time.perf_counter()
    counts = seed_all(db)
    logger.info("Seeded defaults: %s", counts)
    return {"timings_ms": _with_timing(state, "seed", started)}


def node_generate(state: PipelineState) -> PipelineState:
    """Produce synthetic transactions (mode="demo")."""
    started = time.perf_counter()
    records = generate_transactions(
        seed=state.get("seed", 42), months=state.get("months", 12)
    )
    logger.info("Generated %d synthetic transactions", len(records))
    return {"records": records, "timings_ms": _with_timing(state, "generate", started)}


def node_load_files(state: PipelineState, db: Session) -> PipelineState:
    """Parse every statement in `directory` and insert it (mode="files").

    File loading inserts as it goes, because parsing a directory of PDFs is the
    slow part and committing per file means an interrupted run keeps its
    progress.
    """
    started = time.perf_counter()
    directory = Path(state.get("directory", "."))
    results = load_directory(db, directory)

    inserted_ids: list[int] = []
    duplicates = 0
    parsed = 0
    for result in results:
        inserted_ids.extend(result.inserted_ids)
        duplicates += result.duplicates
        parsed += result.parsed
        logger.info(
            "  %-42s %3d new, %3d duplicate (%s)",
            result.file, result.inserted, result.duplicates, result.parser,
        )

    if not results:
        logger.warning("No readable statements found in %s", directory)

    return {
        "records": [],
        "inserted_ids": inserted_ids,
        "duplicates": duplicates,
        "timings_ms": _with_timing(state, "load_files", started),
    }


def node_store(state: PipelineState, db: Session) -> PipelineState:
    """Insert generated records, skipping duplicates.

    A no-op for mode="files", where `node_load_files` already stored its rows.
    """
    started = time.perf_counter()
    records = state.get("records") or []
    if not records:
        return {"timings_ms": _with_timing(state, "store", started)}

    result = load_transactions(db, records)
    return {
        "inserted_ids": result.inserted_ids,
        "duplicates": result.duplicates,
        "timings_ms": _with_timing(state, "store", started),
    }


# ---------------------------------------------------------------------------
# Categorisation
# ---------------------------------------------------------------------------


def node_rule_tag(state: PipelineState, db: Session) -> PipelineState:
    """Apply regex rules. Skipped entirely when running LLM-first."""
    started = time.perf_counter()
    settings = get_settings()
    if settings.llm_first:
        # Plain ASCII in log messages: Windows consoles default to a codepage
        # that mangles em dashes and arrows.
        logger.info("llm_first is on, skipping the rule engine")
        return {"rule_tagged": 0, "timings_ms": _with_timing(state, "rule_tag", started)}

    engine = RuleEngine(db)
    tagged = 0
    for txn_id in state.get("inserted_ids", []):
        txn = db.get(Transaction, txn_id)
        if txn is not None and txn.category is None and engine.apply(txn):
            tagged += 1
    db.commit()
    logger.info("Rule engine tagged %d of %d", tagged, len(state.get("inserted_ids", [])))
    return {"rule_tagged": tagged, "timings_ms": _with_timing(state, "rule_tag", started)}


def _memory_block(examples: Sequence[Match]) -> str:
    lines = [
        TAGGING_MEMORY_LINE.format(
            description=match.example.description[:120],
            direction=match.example.direction,
            amount=f"{match.example.amount:,.0f}",
            same={"handle": ", same UPI handle", "name": ", same name"}.get(match.via, ""),
            category=match.example.category,
        )
        for match in examples
    ]
    return TAGGING_MEMORY.format(examples="\n".join(lines))


async def classify_one(
    txn: Transaction, llm: LLMClient, examples: Sequence[Match] | None = None
) -> dict | None:
    """Classify one transaction.

    Returns the parsed fields, or **None** when the model could not be reached
    or returned something unusable. None means "no answer" and callers must not
    write a tag — that distinction is the whole point. See `app.llm.client`.

    `examples` are the user's own earlier decisions for this or a similar payee
    (app.memory). The model is told to use them only for the same payee.
    """
    prompt = TAGGING_USER.format(
        description=txn.raw_description[:300],
        merchant=txn.merchant_normalized or "(unknown)",
        amount=f"{txn.amount:,.2f}",
        direction=txn.direction.value,
        source=txn.source.value,
        counterparty=txn.counterparty_id or "(none)",
    )
    if examples:
        prompt += _memory_block(examples)
    result = await llm.structured(
        [
            {"role": "system", "content": TAGGING_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        schema=TAGGING_SCHEMA,
    )
    parsed = result.json()
    if parsed is None:
        logger.debug("No classification for txn %s: %s", txn.id, result.error)
        return None

    # Schema-constrained decoding guarantees the shape, but a defensive clamp
    # costs nothing and keeps a swapped-in model from writing confidence=7.
    try:
        confidence = min(1.0, max(0.0, float(parsed.get("confidence", 0.0))))
    except (TypeError, ValueError):
        return None

    category = parsed.get("category")
    if not category:
        return None

    return {
        "category": category,
        "subcategory": (parsed.get("subcategory") or None),
        "confidence": confidence,
        "reason": (parsed.get("reason") or "").strip()[:200],
    }


async def node_llm_tag(state: PipelineState, db: Session, llm: LLMClient) -> PipelineState:
    """Classify every untagged transaction with the local model.

    Rows the model could not classify are left with `category=NULL` and
    `needs_review=True`. They are not written as zero-confidence model output,
    because that is indistinguishable from a real uncertain answer and it is
    how this project once reported 285 unclassified rows as LLM-tagged.
    """
    started = time.perf_counter()
    settings = get_settings()

    pending = [
        txn for txn in (
            db.get(Transaction, txn_id) for txn_id in state.get("inserted_ids", [])
        )
        if txn is not None and txn.category is None
    ]
    if not pending:
        return {
            "llm_tagged": 0, "needs_review": 0, "llm_failed": 0, "llm_available": True,
            "timings_ms": _with_timing(state, "llm_tag", started),
        }

    memory: CorrectionMemory | None = None
    if settings.memory_enabled:
        memory = await CorrectionMemory.from_db(
            db, llm, top_k=settings.memory_top_k,
            min_similarity=settings.memory_min_similarity,
            name_check=settings.memory_name_check,
        )
        if not memory.examples:
            memory = None

    # Tier 1: a payee you have already categorised, under the same UPI handle,
    # gets your label directly. There is nothing for a model to judge, and on
    # the personal-payee eval a small model ignored such examples half the time.
    decided = 0
    if memory is not None:
        undecided = []
        for txn in pending:
            earlier = memory.decide(
                txn.direction.value,
                counterparty=txn.counterparty_id, merchant=txn.merchant_normalized,
            )
            if earlier is None:
                undecided.append(txn)
                continue
            txn.category = earlier.category
            txn.subcategory = None
            txn.tag_source = TagSource.MEMORY
            txn.tag_confidence = 1.0
            txn.tag_reason = "your earlier label for this payee"
            txn.needs_review = False
            decided += 1
        db.commit()
        pending = undecided
        await memory.prepare((t.raw_description, t.direction.value) for t in pending)
        if decided:
            logger.info("%d rows took your earlier label for the same payee", decided)
    if not pending:
        return {
            "llm_tagged": 0, "needs_review": 0, "llm_failed": 0, "llm_available": True,
            "memory_decided": decided, "memory_assisted": 0,
            "timings_ms": _with_timing(state, "llm_tag", started),
        }

    semaphore = asyncio.Semaphore(settings.llm_concurrency)
    assisted = 0

    async def classify(txn: Transaction) -> tuple[Transaction, dict | None, bool]:
        examples = memory.lookup(
            txn.raw_description, txn.direction.value,
            counterparty=txn.counterparty_id, merchant=txn.merchant_normalized,
        ) if memory else []
        async with semaphore:
            return txn, await classify_one(txn, llm, examples), bool(examples)

    tagged = flagged = failed = 0
    # Commit in chunks so a long run over a real statement history keeps its
    # progress if it is interrupted.
    chunk_size = 50
    for start in range(0, len(pending), chunk_size):
        chunk = pending[start:start + chunk_size]
        for txn, fields, used_memory in await asyncio.gather(*(classify(t) for t in chunk)):
            if fields is None:
                failed += 1
                txn.needs_review = True
                txn.tag_reason = "not classified — model unavailable"
                continue

            txn.category = fields["category"]
            txn.subcategory = fields["subcategory"]
            txn.tag_source = TagSource.LLM
            txn.tag_confidence = fields["confidence"]
            # Provenance: say when your own earlier labels were in the prompt,
            # so a tag that copied one is never mistaken for independent judgement.
            txn.tag_reason = (
                f"{fields['reason']} · used your earlier labels" if used_memory
                else fields["reason"]
            )
            txn.needs_review = fields["confidence"] < settings.confidence_threshold
            tagged += 1
            assisted += int(used_memory)
            flagged += int(txn.needs_review)
        db.commit()
        logger.info("Classified %d/%d", min(start + chunk_size, len(pending)), len(pending))

    if failed:
        logger.warning(
            "%d of %d transactions were left untagged because the model was "
            "unavailable. Re-run once it is back; already-tagged rows are skipped.",
            failed, len(pending),
        )

    if assisted:
        logger.info("%d rows were classified with your earlier labels as examples", assisted)

    return {
        "llm_tagged": tagged,
        "needs_review": flagged + failed,
        "llm_failed": failed,
        "llm_available": failed == 0,
        "memory_decided": decided,
        "memory_assisted": assisted,
        "timings_ms": _with_timing(state, "llm_tag", started),
    }


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------


def node_friend_discover(state: PipelineState, db: Session) -> PipelineState:
    """Find counterparties that look like people rather than merchants."""
    started = time.perf_counter()
    from app.services.friend_detector import detect_friends, link_detected_friends

    settings = get_settings()
    detected = detect_friends(db)
    stats = link_detected_friends(
        db, detected, min_confidence=settings.friend_min_confidence
    )
    logger.info(
        "Friend detection: %d candidates, %d people created, %d transactions linked",
        len(detected), stats["people_created"], stats["transactions_linked"],
    )
    return {
        "friends_detected": len(detected),
        "friends_linked": stats["transactions_linked"],
        "timings_ms": _with_timing(state, "friend_discover", started),
    }


async def node_validator(state: PipelineState, db: Session, llm: LLMClient) -> PipelineState:
    """Second opinion on low-confidence tags, plus relationship classification."""
    started = time.perf_counter()
    from app.pipeline.validator import (
        classify_relationships,
        revalidate_low_confidence,
    )

    settings = get_settings()
    decisions = await revalidate_low_confidence(
        db, llm=llm, threshold=settings.confidence_threshold,
        limit=settings.validator_max_per_run,
    )
    relationships = await classify_relationships(db, llm=llm)
    overridden = sum(1 for d in decisions if not d.agreed)
    logger.info(
        "Validator reviewed %d tags (%d overridden) and classified %d relationships",
        len(decisions), overridden, len(relationships),
    )
    return {
        "validator_checked": len(decisions),
        "validator_overridden": overridden,
        "timings_ms": _with_timing(state, "validator", started),
    }


def node_finalize(state: PipelineState, db: Session, run: PipelineRun) -> PipelineState:
    """Write the audit record for this run."""
    started = time.perf_counter()
    timings = _with_timing(state, "finalize", started)

    run.finished_at = utc_now()
    run.status = "ok"
    run.transactions_processed = len(state.get("inserted_ids", []))
    run.rule_tagged = state.get("rule_tagged", 0)
    run.llm_tagged = state.get("llm_tagged", 0)
    run.needs_review = state.get("needs_review", 0)
    run.llm_available = state.get("llm_available", True)
    run.llm_failed = state.get("llm_failed", 0)
    run.node_timings_ms = timings
    db.commit()

    return {"timings_ms": timings}
