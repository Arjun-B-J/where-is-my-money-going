"""Local LLM access layer.

Everything that talks to the language model lives here. The rest of the app
imports `get_llm()` and the prompt constants; nothing else needs to know that
the model is served by Ollama.
"""
from app.llm.client import LLMClient, LLMResult, get_llm

__all__ = ["LLMClient", "LLMResult", "get_llm"]
