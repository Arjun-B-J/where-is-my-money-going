"""Run one arm of the evaluation over the labelled rows.

Three arms match the three ways the pipeline can be configured:

* ``rules``       the seeded regex rules alone. Needs no model, so it is the
                  floor every model run is compared against.
* ``llm``         every row goes to the model (the default, `LLM_FIRST=true`).
* ``rules+llm``   rules first, the model for whatever they miss.

Two more measure learning from corrections (app.memory), and need the `history`
rows of the personal set:

* ``lookup``      only the memory's first tier: copy the label of an earlier row
                  with the same UPI handle (or, without one, the same merchant
                  string). No model. The simplest thing that could work, so it is
                  the baseline the full memory has to beat.
* ``llm+memory``  both tiers, as the pipeline runs them: the first tier decides
                  what it can, and the model classifies the rest with matching
                  past labels as examples.

A model that cannot be reached, or is not pulled, fails the run before any row
is scored. Scoring it anyway would record a 0% that reads as the model being
bad at the task, which is the same mistake as the fallback in DECISIONS.md §4.
The same goes for the embedding model in ``llm+memory``: without it the arm
would quietly become exact matching and report that as the memory's result.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import Base
from app.evals.dataset import GoldenRow, as_example
from app.evals.metrics import Prediction
from app.llm.client import LLMClient
from app.memory import CorrectionMemory, Match
from app.pipeline.nodes import classify_one
from app.rules.engine import RuleEngine
from app.seed import seed_rules

ARMS: tuple[str, ...] = ("rules", "llm", "rules+llm", "lookup", "llm+memory")
_MODEL_ARMS = ("llm", "rules+llm", "llm+memory")
_MEMORY_ARMS = ("lookup", "llm+memory")


class EvalAbortedError(RuntimeError):
    """The run could not start: no model, no daemon. Nothing was scored."""


@dataclass
class RunResult:
    arm: str
    model: str | None
    predictions: list[Prediction]
    wall_seconds: float
    concurrency: int
    warmup_seconds: float | None = None
    meta: dict = field(default_factory=dict)

    @property
    def rows_per_minute(self) -> float | None:
        return len(self.predictions) / self.wall_seconds * 60 if self.wall_seconds > 0 else None


def seeded_rule_engine() -> RuleEngine:
    """The default rules in a throwaway in-memory database.

    The eval must not read or write the user's own database: their custom rules
    would change the numbers, and their data has no business in a benchmark.
    """
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = Session(engine)
    seed_rules(session)
    return RuleEngine(session)


def _rule_prediction(row: GoldenRow, engine: RuleEngine) -> Prediction | None:
    match = engine.match(row.to_transaction())
    if match is None:
        return None
    return Prediction(row.id, row.slice, row.gold, match.category, match.confidence, 0, "rule")


async def _model_prediction(
    row: GoldenRow, llm: LLMClient, examples: Sequence[Match] | None = None
) -> Prediction:
    started = time.perf_counter()
    fields = await classify_one(row.to_transaction(), llm, examples)
    elapsed = int((time.perf_counter() - started) * 1000)
    if fields is None:
        return Prediction(row.id, row.slice, row.gold, None, None, elapsed, "none")
    return Prediction(
        row.id, row.slice, row.gold, fields["category"], fields["confidence"], elapsed, "llm"
    )


def _decide(memory: CorrectionMemory, row: GoldenRow) -> Prediction | None:
    txn = row.to_transaction()
    earlier = memory.decide(
        txn.direction.value, counterparty=txn.counterparty_id, merchant=txn.merchant_normalized,
    )
    if earlier is None:
        return None
    return Prediction(row.id, row.slice, row.gold, earlier.category, 1.0, 0, "memory")


def _lookup(memory: CorrectionMemory, row: GoldenRow) -> list[Match]:
    txn = row.to_transaction()
    return memory.lookup(
        txn.raw_description, txn.direction.value,
        counterparty=txn.counterparty_id, merchant=txn.merchant_normalized,
    )


async def model_metadata(llm: Any) -> dict:
    """What exactly was measured: Ollama version, model digest, size, quantisation.

    A model tag is a moving pointer. The digest is what makes a result
    reproducible, so it is recorded with every run.
    """
    host = getattr(llm, "host", None)
    model = getattr(llm, "model", None)
    meta: dict = {"model": model, "host": host}
    if not host or not model:
        # A stand-in client (the test fake) has nothing to report.
        return meta
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            version = await client.get(f"{host}/api/version")
            tags = await client.get(f"{host}/api/tags")
        meta["ollama_version"] = version.json().get("version")
        wanted = model if ":" in model else f"{model}:latest"
        for entry in tags.json().get("models", []):
            if entry.get("name") == wanted:
                details = entry.get("details") or {}
                meta.update({
                    "digest": (entry.get("digest") or "")[:12],
                    "size_gb": round((entry.get("size") or 0) / 1e9, 1),
                    "parameter_size": details.get("parameter_size"),
                    "quantization": details.get("quantization_level"),
                    "family": details.get("family"),
                })
                break
    except (httpx.HTTPError, ValueError):
        pass
    return meta


async def _warm_up(rows: Sequence[GoldenRow], llm: Any) -> float:
    """Fail fast if the model cannot answer; return the load time.

    The first call loads the weights into memory, which can take longer than the
    whole rest of the run, so it is excluded from latency on purpose.
    """
    name = getattr(llm, "model", "the model")
    health = await llm.health()
    if not health.get("ok"):
        raise EvalAbortedError(f"the model server is not reachable: {health.get('error')}")
    if not health.get("model_pulled"):
        raise EvalAbortedError(f"{name} is not pulled. Run: ollama pull {name}")
    started = time.perf_counter()
    probe = await _model_prediction(rows[0], llm)
    if not probe.answered:
        raise EvalAbortedError(
            f"{name} is reachable but returned no usable answer on the warm-up "
            "call. Check `ollama run` by hand before trusting any numbers."
        )
    return time.perf_counter() - started


async def _build_memory(
    arm: str, history: Sequence[GoldenRow], rows: Sequence[GoldenRow], llm: Any
) -> tuple[CorrectionMemory, dict]:
    examples = [as_example(row) for row in history]
    if arm == "lookup":
        # The first tier alone needs no embeddings.
        return CorrectionMemory(examples, None, None), {}

    settings = get_settings()
    memory = await CorrectionMemory.build(
        examples, llm, top_k=settings.memory_top_k,
        min_similarity=settings.memory_min_similarity,
        name_check=settings.memory_name_check,
    )
    embed_model = getattr(llm, "embed_model", "the embedding model")
    if not memory.similarity_available:
        raise EvalAbortedError(
            f"{embed_model} returned no embeddings. Run: ollama pull {embed_model}"
        )
    await memory.prepare((row.description, row.direction.value) for row in rows)
    return memory, {
        "embed_model": embed_model,
        "memory_top_k": settings.memory_top_k,
        "memory_min_similarity": settings.memory_min_similarity,
        "memory_name_check": settings.memory_name_check,
        "history_rows": len(history),
    }


async def run_arm(
    rows: Sequence[GoldenRow],
    *,
    arm: str,
    llm: Any | None = None,
    concurrency: int = 4,
    history: Sequence[GoldenRow] | None = None,
) -> RunResult:
    """Score one arm over `rows`. Order of predictions matches order of rows."""
    if arm not in ARMS:
        raise ValueError(f"unknown arm {arm!r}; choose from {ARMS}")
    needs_model = arm in _MODEL_ARMS
    if needs_model and llm is None:
        raise ValueError(f"arm {arm!r} needs a model client")
    if arm in _MEMORY_ARMS and not history:
        raise ValueError(f"arm {arm!r} needs history rows (the personal set)")

    engine = seeded_rule_engine() if arm in ("rules", "rules+llm") else None
    meta: dict = {}
    warmup: float | None = None
    if needs_model:
        warmup = await _warm_up(rows, llm)
        meta = await model_metadata(llm)

    memory: CorrectionMemory | None = None
    if arm in _MEMORY_ARMS:
        assert history is not None
        memory, memory_meta = await _build_memory(arm, history, rows, llm)
        meta.update(memory_meta)

    semaphore = asyncio.Semaphore(max(1, concurrency))
    decided = with_examples = 0

    async def predict(row: GoldenRow) -> Prediction:
        nonlocal decided, with_examples
        if engine is not None:
            ruled = _rule_prediction(row, engine)
            if ruled is not None:
                return ruled
            if arm == "rules":
                # Nothing matched. A rules-only pipeline leaves the row for
                # someone else, which for scoring is an abstention.
                return Prediction(row.id, row.slice, row.gold, "uncategorized", 0.0, 0, "none")

        examples: list[Match] = []
        if memory is not None:
            tier_one = _decide(memory, row)
            if tier_one is not None:
                decided += 1
                return tier_one
            if arm == "lookup":
                return Prediction(row.id, row.slice, row.gold, "uncategorized", 0.0, 0, "none")
            examples = _lookup(memory, row)
            with_examples += int(bool(examples))

        assert llm is not None
        async with semaphore:
            return await _model_prediction(row, llm, examples)

    started = time.perf_counter()
    predictions = list(await asyncio.gather(*(predict(row) for row in rows)))
    wall = time.perf_counter() - started
    if memory is not None:
        meta.update({"rows_decided": decided, "rows_with_examples": with_examples})

    return RunResult(
        arm=arm,
        model=getattr(llm, "model", "fake") if needs_model else None,
        predictions=predictions,
        wall_seconds=wall,
        concurrency=concurrency,
        warmup_seconds=warmup,
        meta=meta,
    )
