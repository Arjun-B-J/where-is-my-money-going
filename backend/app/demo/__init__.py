"""Synthetic data used for demos, screenshots and tests.

Kept in its own package so it is obvious that nothing in here is real and that
production code paths do not depend on it.
"""
from app.demo.generator import generate_transactions

__all__ = ["generate_transactions"]
