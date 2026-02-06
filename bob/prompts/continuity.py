from __future__ import annotations

CONTINUITY_UPDATE_PROMPT = """CONTEXT UPDATE (internal)

Goal:
- Update active_context and open_threads for conversation coherence.
- Use the prior active_context/open_threads plus the latest user + assistant turn.

Rules:
- active_context: 2–6 short bullet fragments (stable facts, goals, decisions).
- open_threads: 0–4 short bullet fragments (unresolved questions or tasks).
- Threads persist until explicitly resolved or superseded.
- resolved_threads: 0–4 items, must be exact matches from prior open_threads that are now resolved/superseded.
- stm_anchors (if provided) are non-authoritative hints for continuity; use sparingly.
- No transcripts, no tool chatter, no chain-of-thought.
- Keep each item under 140 characters.

Output format (STRICT):
{"active_context":["..."],"open_threads":["..."],"resolved_threads":["..."]}
"""
