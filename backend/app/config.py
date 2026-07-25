"""Application settings.

Values come from environment variables, optionally via a `.env` file. Field
name `llm_model` maps to env var `LLM_MODEL`, and so on.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

APP_NAME = "Where Is My Money Going?"
APP_SLUG = "where-is-my-money-going"
APP_TAGLINE = "Local-first spending analysis. Your statements never leave your machine."
APP_VERSION = "0.5.0"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # ---- Local model (Ollama) ----
    llm_host: str = "http://localhost:11434"
    llm_model: str = "gemma4:26b"
    llm_vision_model: str = "gemma4:26b"
    llm_timeout_s: float = 180.0

    # The default model is a reasoning model. Its `thinking` channel corrupts
    # schema-constrained JSON (repeated-token loops mid-string) and makes prose
    # generation roughly 100x slower for no quality gain on these tasks, so it
    # is off. Set LLM_THINK=true if you swap in a model that needs it.
    llm_think: bool = False

    # How many transactions to classify in parallel. Each call is independent;
    # 4 keeps a single consumer GPU busy without thrashing its memory.
    llm_concurrency: int = 4

    # ---- Storage ----
    database_url: str = "sqlite:///./storage/wimmg.db"

    # ---- Web ----
    cors_origins: str = "http://localhost:3000"

    # ---- Tagging ----
    # Below this confidence a transaction is queued for human review rather
    # than trusted. 0.70 is deliberately strict: the model reports 0.3-0.6 for
    # payments to unrecognised individuals, and those are exactly the rows a
    # person needs to look at.
    confidence_threshold: float = 0.70

    # Every transaction goes through the model by default. A local model has no
    # per-call cost, and it handles merchant names no regex was written for.
    # Set LLM_FIRST=false to run the regex rule engine first instead, which is
    # much faster and used by the test suite.
    llm_first: bool = True

    # ---- Validator agent ----
    validator_max_per_run: int = 200

    # ---- Friend detection ----
    friend_min_confidence: float = 0.70

    # ---- Budget envelopes ----
    budget_warning_pct: float = 0.85
    budget_critical_pct: float = 1.00

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def storage_dir(self) -> Path:
        """Directory holding the SQLite file; created on first access."""
        if self.database_url.startswith("sqlite:///"):
            path = Path(self.database_url.removeprefix("sqlite:///"))
            path.parent.mkdir(parents=True, exist_ok=True)
            return path.parent
        return Path("storage")


@lru_cache
def get_settings() -> Settings:
    return Settings()
