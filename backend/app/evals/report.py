"""Result records and the tables printed at the end of a run.

Each run is written as one JSON file holding the scores, the settings, the
model digest and every individual prediction, so an error can be traced back to
the exact row and reply that caused it. The markdown tables are derived from
those files and never edited by hand.
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from app.clock import utc_now
from app.evals.dataset import SLICES
from app.evals.runner import RunResult


def build_record(result: RunResult, scores: dict, *, dataset: str, fingerprint: str) -> dict:
    return {
        "run_at": utc_now().isoformat(timespec="seconds") + "Z",
        "dataset": dataset,
        "dataset_sha1": fingerprint,
        "arm": result.arm,
        "model": result.model,
        "concurrency": result.concurrency,
        "wall_seconds": round(result.wall_seconds, 2),
        "warmup_seconds": round(result.warmup_seconds, 2) if result.warmup_seconds else None,
        "rows_per_minute": round(result.rows_per_minute, 1) if result.rows_per_minute else None,
        "meta": result.meta,
        "scores": scores,
        "predictions": [
            {
                "id": p.row_id, "slice": p.slice, "gold": list(p.gold),
                "predicted": p.category, "confidence": p.confidence,
                "correct": p.correct, "latency_ms": p.latency_ms, "source": p.source,
            }
            for p in result.predictions
        ],
    }


def save_record(record: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = record["run_at"].replace(":", "").replace("-", "")[:15]
    label = (record["model"] or "no-model").replace(":", "-").replace("/", "-")
    path = out_dir / f"{stamp}_{record['arm'].replace('+', '-')}_{label}.json"
    path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    return path


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _num(value: float | None, digits: int = 3) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def _ms(value: float | None) -> str:
    return "n/a" if value is None else f"{value / 1000:.2f}s"


def comparison_table(records: Sequence[dict]) -> str:
    """One row per run, headline metrics only."""
    header = (
        "| Arm | Model | Accuracy | Macro-F1 | Auto-accept precision | Coverage "
        "| Abstain recall | False abstain | ECE | Valid | p50 | p95 | Rows/min |\n"
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|"
    )
    lines = [header]
    for r in records:
        s = r["scores"]
        lines.append(
            f"| {r['arm']} | {r['model'] or 'none'} | {_pct(s['accuracy'])} "
            f"| {_num(s['macro_f1'])} | {_pct(s['auto_accept_precision'])} "
            f"| {_pct(s['auto_accept_coverage'])} | {_pct(s['abstain_recall'])} "
            f"| {_pct(s['false_abstain_rate'])} | {_num(s['ece'])} | {_pct(s['answered_rate'])} "
            f"| {_ms(s['latency_ms_p50'])} | {_ms(s['latency_ms_p95'])} "
            # Throughput only means something when a model did the work.
            f"| {r['rows_per_minute'] if r['model'] else 'n/a'} |"
        )
    return "\n".join(lines)


def slice_table(records: Sequence[dict], slices: Sequence[str] = SLICES) -> str:
    """Accuracy per slice, one column per run. Where the runs actually differ."""
    columns = [f"{r['arm']} / {r['model'] or 'none'}" for r in records]
    lines = [
        "| Slice | n | " + " | ".join(columns) + " |",
        "|---|---|" + "---|" * len(columns),
    ]
    for name in slices:
        cells = []
        n = 0
        for r in records:
            entry = r["scores"]["by_slice"].get(name)
            n = entry["n"] if entry else n
            cells.append(_pct(entry["accuracy"]) if entry else "n/a")
        if n:
            lines.append(f"| {name} | {n} | " + " | ".join(cells) + " |")
    return "\n".join(lines)
