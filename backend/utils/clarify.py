"""
Clarification + extraction pipeline.

Thin re-export of `routers.intents._run_pipeline` under a descriptive name.
Returns a 4-tuple:

    (clarification, final_text, extracted, embedding)

- If `clarification` is not None, the intent is ambiguous and the caller
  should return it to the user without persisting anything.
- If `clarification` is None, the caller has everything needed to persist
  a new intent document.
"""
from routers.intents import _run_pipeline as clarify_and_extract

__all__ = ["clarify_and_extract"]
