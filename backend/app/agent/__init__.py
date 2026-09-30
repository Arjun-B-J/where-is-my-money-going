"""Chat as a tool-calling agent over the transaction database.

DECISIONS.md §15 settled how chat should work: the questions people ask about
money are filters and aggregates, and those have exact answers, so the model
chooses the query and code computes the figure. Retrieval would return the most
similar rows, not all of them, and a total built from those is confidently wrong.

    tools.py      read-only queries with validated arguments; every figure comes from here
    loop.py       the conversation: tool rounds, a step cap, one corrective round
    grounding.py  finds rupee figures in an answer that no tool returned
    prompts.py    the system prompt and the two follow-up messages

The tools take `redact=True` so that a later MCP server, whose client may be a
cloud model, can reuse them without passing people's names on.
"""
