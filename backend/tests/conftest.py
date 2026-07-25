"""Shared fixtures: an isolated database and a fake local model.

The fake model is the important one. Tests must be able to assert what happens
when the model **fails**, because the bug this project shipped was a failure that
looked like success. `FakeLLM(available=False)` makes that path testable.
"""
from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path

# Set before importing anything from `app`, so settings pick it up.
os.environ.setdefault("DATABASE_URL", "sqlite:///./storage/test.db")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base, get_db
from app.llm.client import LLMResult
from app.main import app


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path: Path):
    """Give every test its own SQLite file and wire the app to it."""
    url = f"sqlite:///{(tmp_path / 'test.db').as_posix()}"
    engine = create_engine(url, connect_args={"check_same_thread": False})
    session_factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    from app import db as db_module

    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(db_module, "SessionLocal", session_factory)
    Base.metadata.create_all(bind=engine)

    def override_get_db() -> Iterator[Session]:
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def db(isolated_db) -> Iterator[Session]:
    from app.db import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


class FakeLLM:
    """Stand-in for the local model.

    `available=False` simulates the daemon being down: every call returns
    `ok=False` with empty text, exactly as the real client does. No fabricated
    content, because production code must never receive any.
    """

    def __init__(self, *, available: bool = True, malformed: bool = False) -> None:
        self.available = available
        self.malformed = malformed
        self.calls: list[dict] = []

    async def health(self) -> dict:
        return {
            "ok": self.available,
            "host": "fake",
            "model": "fake",
            "model_pulled": self.available,
        }

    def _result(self, text: str) -> LLMResult:
        if not self.available:
            return LLMResult(text="", ok=False, error="fake: model unavailable")
        if self.malformed:
            return LLMResult(text="{not json at all", ok=True)
        return LLMResult(text=text, ok=True)

    async def complete(self, messages, *, model=None, temperature=0.3) -> LLMResult:
        self.calls.append({"kind": "complete", "messages": messages})
        return self._result(
            "You spent steadily through the period. Rent and instalments account "
            "for most of the fixed outflow. The rest is day-to-day spending "
            "spread across food and transport."
        )

    async def structured(self, messages, schema, *, model=None, temperature=0.1) -> LLMResult:
        self.calls.append({"kind": "structured", "messages": messages, "schema": schema})
        properties = schema.get("properties", {})

        if "insights" in properties:
            payload = {"insights": [
                {"title": "Fake insight", "body": "Body text.", "severity": "info"},
                {"title": "Second", "body": "More body text.", "severity": "warn"},
                {"title": "Third", "body": "Still more.", "severity": "good"},
            ]}
        elif "observations" in properties:
            payload = {"observations": [
                {"thesis": "You spend on food.", "evidence": "About Rs 40,000 this year."},
                {"thesis": "Rent dominates.", "evidence": "Rs 3,84,000 over the period."},
                {"thesis": "Subscriptions add up.", "evidence": "Roughly Rs 9,000 a year."},
            ]}
        elif "kind" in properties:
            payload = {"kind": "split_bills", "summary": "Regular small two-way transfers.",
                       "confidence": 0.8}
        elif "agree" in properties:
            # The validator only sees rows the first pass was unsure about, and in
            # the demo dataset those are genuinely opaque payees. Confirming
            # "uncategorized" while staying unsure is the honest answer, and it
            # keeps the row in the review queue where a human belongs.
            payload = {"agree": True, "category": "uncategorized", "subcategory": None,
                       "confidence": 0.45, "reason": "Payee still unrecognised."}
        elif "is_receipt" in properties:
            payload = {"is_receipt": True, "merchant": "FAKE CAFE", "amount": 240.0,
                       "date": "2026-04-01", "category": "food", "confidence": 0.9,
                       "items": ["coffee"]}
        else:
            payload = self._classify(messages)

        return self._result(json.dumps(payload))

    async def vision(self, prompt, image_b64, schema=None, *, temperature=0.1) -> LLMResult:
        self.calls.append({"kind": "vision"})
        return self._result(json.dumps({
            "is_receipt": True, "merchant": "FAKE CAFE", "amount": 240.0,
            "date": "2026-04-01", "category": "food", "confidence": 0.9,
            "items": ["coffee"],
        }))

    async def stream(self, messages, *, model=None, temperature=0.4):
        from app.llm.client import LLMUnavailableError

        if not self.available:
            raise LLMUnavailableError("fake: model unavailable")
        self.calls.append({"kind": "stream"})
        for chunk in ("You ", "spent ", "a ", "lot."):
            yield chunk

    @staticmethod
    def _classify(messages) -> dict:
        """Keyword classifier standing in for the tagging pass.

        Reads only the user message. Matching against the system prompt too would
        hit the category taxonomy printed there and classify everything as the
        first category listed.
        """
        text = next(
            (m["content"] for m in reversed(messages) if m.get("role") == "user"), ""
        ).lower()
        table = {
            "food": ("food delivery", "restaurant", "cafeteria", "coffee"),
            "transport": ("cabs", "bike taxi", "metro", "fuel"),
            "groceries": ("supermarket", "grocery"),
            "shopping": ("marketplace", "fashion", "sports"),
            "health": ("pharmacy",),
            "entertainment": ("cinema",),
            "subscriptions": ("streaming", "music", "fitness"),
            "salary": ("salary",),
            "rent": ("rent",),
            "utilities": ("electricity", "fibernet", "broadband"),
            "investments": ("index fund", "broking"),
            "cash": ("atm",),
            "loan_repayment": ("amortization", "card auto pay", "credit card"),
        }
        for category, keywords in table.items():
            if any(keyword in text for keyword in keywords):
                return {"category": category, "subcategory": None,
                        "confidence": 0.9, "reason": f"matched {category}"}
        # Unrecognised payee: genuinely uncertain, which is the honest answer.
        return {"category": "uncategorized", "subcategory": None,
                "confidence": 0.35, "reason": "payee not recognised"}


@pytest.fixture
def llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def offline_llm() -> FakeLLM:
    return FakeLLM(available=False)


@pytest.fixture
def client(isolated_db, llm, monkeypatch) -> TestClient:
    """Test client with the fake model injected everywhere it is fetched."""
    import app.llm.client as client_module

    monkeypatch.setattr(client_module, "_client", llm)
    monkeypatch.setattr(client_module, "get_llm", lambda: llm)
    for module in (
        "app.routes.chat", "app.routes.receipt", "app.routes.system",
        "app.routes.ingest", "app.routes.agents", "app.services.insights",
        "app.pipeline.graph", "app.reports.spend_analysis",
    ):
        import importlib

        mod = importlib.import_module(module)
        if hasattr(mod, "get_llm"):
            monkeypatch.setattr(mod, "get_llm", lambda: llm)
    return TestClient(app)
