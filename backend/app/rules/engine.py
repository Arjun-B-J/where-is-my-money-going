"""Deterministic rule engine — fast first-pass tagger.

Goal: tag the obvious 60-70% of transactions cheaply, without involving the
LLM. Rules are loaded from the DB so users can extend without code changes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import Person, Rule, Transaction


@dataclass
class RuleMatch:
    rule_name: str
    category: str
    subcategory: str | None
    person_name: str | None
    confidence: float = 1.0
    reason: str = ""


class RuleEngine:
    """In-memory rule evaluator. Rebuild via reload() if rules change."""

    def __init__(self, db: Session):
        self._db = db
        self._compiled: list[tuple[Rule, re.Pattern]] = []
        self.reload()

    def reload(self) -> None:
        rules = (
            self._db.query(Rule)
            .filter(Rule.enabled.is_(True))
            .order_by(Rule.priority.asc())
            .all()
        )
        self._compiled = []
        for r in rules:
            try:
                pat = re.compile(r.pattern)
            except re.error:
                # Skip malformed rule rather than crash the pipeline
                continue
            self._compiled.append((r, pat))

    def match(self, txn: Transaction) -> RuleMatch | None:
        haystack = " ".join(filter(None, [
            txn.raw_description or "",
            txn.merchant_normalized or "",
            txn.counterparty_id or "",
        ]))

        for rule, pat in self._compiled:
            if rule.direction is not None and rule.direction != txn.direction:
                continue
            if rule.source is not None and rule.source != txn.source:
                continue
            if rule.amount_min is not None and txn.amount < rule.amount_min:
                continue
            if rule.amount_max is not None and txn.amount > rule.amount_max:
                continue
            if not pat.search(haystack):
                continue
            return RuleMatch(
                rule_name=rule.name,
                category=rule.category,
                subcategory=rule.subcategory,
                person_name=rule.person_name,
                confidence=1.0,
                reason=f"matched rule '{rule.name}' (priority {rule.priority})",
            )
        return None

    def apply(self, txn: Transaction) -> bool:
        """Apply a matching rule to a transaction in place. Returns True if matched."""
        from app.models import TagSource  # local import to avoid cycle

        match = self.match(txn)
        if not match:
            return False

        txn.category = match.category
        txn.subcategory = match.subcategory
        txn.tag_source = TagSource.RULE
        txn.tag_confidence = match.confidence
        txn.tag_reason = match.reason

        if match.category in {"loan_given", "loan_taken", "loan_repayment"}:
            txn.is_loan = True

        if match.person_name:
            person = (
                self._db.query(Person)
                .filter(Person.name == match.person_name)
                .first()
            )
            if person:
                txn.person_id = person.id
            else:
                # Auto-create person if rule names someone unknown
                p = Person(name=match.person_name)
                self._db.add(p)
                self._db.flush()
                txn.person_id = p.id
        return True


def needs_llm_tagging(txn: Transaction) -> bool:
    """A transaction needs LLM tagging if rules didn't tag it."""
    return txn.category is None
