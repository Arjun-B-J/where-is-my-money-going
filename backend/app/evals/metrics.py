"""Scoring a set of predictions against the labelled rows.

Accuracy alone would hide the two things this app depends on most:

* **What the review threshold buys.** Rows at or above `confidence_threshold`
  are trusted without a human looking. The number that matters is how often
  those rows are right (auto-accept precision) and how many rows clear the bar
  (coverage). A model can raise accuracy while making this worse.
* **Abstention.** "uncategorized" is a correct answer for an opaque payee and a
  wrong one for Swiggy. Both directions are measured, because a model that
  never abstains and one that always does can post the same accuracy.

Calibration (ECE, Brier) says whether a stated 0.9 means right nine times in
ten. The review queue exists on the assumption that it does.
"""
from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

_NO_ANSWER = "(no answer)"


@dataclass(frozen=True)
class Prediction:
    row_id: str
    slice: str
    gold: tuple[str, ...]
    # None means no answer: the call failed or its reply was unusable. That is
    # scored as wrong, never quietly dropped from the denominator.
    category: str | None
    confidence: float | None
    latency_ms: int = 0
    # "rule", "llm", or "none" when nothing produced an answer.
    source: str = "llm"

    @property
    def answered(self) -> bool:
        return self.category is not None

    @property
    def correct(self) -> bool:
        return self.category is not None and self.category in self.gold


def percentile(values: Sequence[float], q: float) -> float | None:
    """Nearest-rank percentile. No interpolation, so it is always a real value."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(q * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def expected_calibration_error(pairs: Sequence[tuple[float, bool]], bins: int = 10) -> float | None:
    """Weighted gap between stated confidence and observed accuracy, per bin."""
    if not pairs:
        return None
    total = len(pairs)
    error = 0.0
    for b in range(bins):
        low, high = b / bins, (b + 1) / bins
        # The last bin is closed so a confidence of exactly 1.0 is counted.
        members = [
            (conf, ok) for conf, ok in pairs
            if low <= conf < high or (b == bins - 1 and conf == high)
        ]
        if not members:
            continue
        accuracy = sum(ok for _, ok in members) / len(members)
        mean_conf = sum(conf for conf, _ in members) / len(members)
        error += abs(accuracy - mean_conf) * len(members) / total
    return error


def macro_f1(predictions: Sequence[Prediction]) -> float:
    """Macro-averaged F1 over every category that appears as truth or guess.

    With several acceptable labels, the "true" label for a row is the one the
    prediction picked when it was acceptable, otherwise the first listed. That
    avoids penalising a right answer for not being the preferred right answer.
    """
    truths: list[str] = []
    guesses: list[str] = []
    for p in predictions:
        truths.append(p.category if p.correct and p.category else p.gold[0])
        guesses.append(p.category or _NO_ANSWER)

    labels = sorted((set(truths) | set(guesses)) - {_NO_ANSWER})
    f1s: list[float] = []
    for label in labels:
        tp = sum(t == label and g == label for t, g in zip(truths, guesses, strict=True))
        fp = sum(t != label and g == label for t, g in zip(truths, guesses, strict=True))
        fn = sum(t == label and g != label for t, g in zip(truths, guesses, strict=True))
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1s.append(2 * precision * recall / (precision + recall) if precision + recall else 0.0)
    return sum(f1s) / len(f1s) if f1s else 0.0


def _share(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def score(predictions: Sequence[Prediction], *, threshold: float = 0.70) -> dict:
    """Every metric the report shows, from one list of predictions."""
    n = len(predictions)
    if n == 0:
        return {"n": 0}

    answered = [p for p in predictions if p.answered]
    accepted = [p for p in answered if (p.confidence or 0.0) >= threshold]
    must_abstain = [p for p in predictions if p.gold == ("uncategorized",)]
    must_not_abstain = [p for p in predictions if "uncategorized" not in p.gold]
    calibrated = [(p.confidence, p.correct) for p in answered if p.confidence is not None]
    model_latency = [p.latency_ms for p in answered if p.source == "llm"]

    by_slice: dict[str, dict] = {}
    for name in sorted({p.slice for p in predictions}):
        members = [p for p in predictions if p.slice == name]
        by_slice[name] = {
            "n": len(members),
            "accuracy": sum(p.correct for p in members) / len(members),
        }

    confusions = Counter(
        (p.gold[0], p.category or _NO_ANSWER) for p in predictions if not p.correct
    )

    return {
        "n": n,
        "accuracy": sum(p.correct for p in predictions) / n,
        "macro_f1": macro_f1(predictions),
        "answered_rate": len(answered) / n,
        "threshold": threshold,
        "auto_accept_coverage": len(accepted) / n,
        "auto_accept_precision": _share(sum(p.correct for p in accepted), len(accepted)),
        "abstain_recall": _share(
            sum(p.category == "uncategorized" for p in must_abstain), len(must_abstain)
        ),
        "false_abstain_rate": _share(
            sum(p.category == "uncategorized" for p in must_not_abstain), len(must_not_abstain)
        ),
        "ece": expected_calibration_error(calibrated),
        "brier": (
            sum((conf - ok) ** 2 for conf, ok in calibrated) / len(calibrated)
            if calibrated else None
        ),
        "latency_ms_p50": percentile(model_latency, 0.50),
        "latency_ms_p95": percentile(model_latency, 0.95),
        "by_slice": by_slice,
        "top_confusions": [
            {"gold": gold, "predicted": guess, "count": count}
            for (gold, guess), count in confusions.most_common(12)
        ],
    }
