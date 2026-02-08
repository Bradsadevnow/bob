from __future__ import annotations

import os
from dataclasses import dataclass

from bob.tools.sandbox import parse_allowed_roots

@dataclass(frozen=True)
class ModelConfig:
    base_url: str
    api_key: str
    model: str


@dataclass(frozen=True)
class BobConfig:
    system_id: str = "bob"
    display_name: str = "Bob"

    # LM Studio / OpenAI-compatible endpoint for local inference
    local: ModelConfig = ModelConfig(
        base_url=os.getenv("BOB_LOCAL_BASE_URL", "http://localhost:1234/v1").rstrip("/"),
        api_key=os.getenv("BOB_LOCAL_API_KEY", "lm-studio"),
        model=os.getenv("BOB_LOCAL_MODEL", "openai/gpt-oss-20b"),
    )

    # Remote model for MTG calls by default (OpenAI/compatible)
    chat_remote: ModelConfig = ModelConfig(
        base_url=os.getenv("BOB_CHAT_BASE_URL", os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip("/"),
        api_key=os.getenv("BOB_CHAT_API_KEY", os.getenv("OPENAI_API_KEY", "")).strip(),
        model=os.getenv("BOB_CHAT_MODEL", "gpt-4o-mini"),
    )

    # Remote model for MTG calls by default (OpenAI/compatible)
    mtg_remote: ModelConfig = ModelConfig(
        base_url=os.getenv("BOB_MTG_BASE_URL", os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip("/"),
        api_key=os.getenv("BOB_MTG_API_KEY", os.getenv("OPENAI_API_KEY", "")).strip(),
        model=os.getenv("BOB_MTG_MODEL", os.getenv("BOB_CHAT_MODEL", "gpt-5")),
    )

    route_mtg_to_remote: bool = os.getenv("BOB_ROUTE_MTG_REMOTE", "true").lower() in {"1", "true", "yes"}

    runtime_dir: str = os.getenv("BOB_RUNTIME_DIR", "./runtime")
    log_file: str = os.getenv("BOB_TURN_LOG", "./runtime/turns.jsonl")
    state_file: str = os.getenv("BOB_STATE_FILE", "./runtime/state.json")

    approval_ledger_file: str = os.getenv("BOB_APPROVAL_LEDGER", "./runtime/memory_approvals.jsonl")
    ltm_file: str = os.getenv("BOB_LTM_FILE", "./runtime/ltm.jsonl")

    stm_enabled: bool = os.getenv("BOB_STM_ENABLED", "true").lower() in {"1", "true", "yes"}
    stm_dir: str = os.getenv("BOB_STM_DIR", "./runtime/stm_chroma")
    stm_collection: str = os.getenv("BOB_STM_COLLECTION", "stm")
    stm_ttl_hours: int = int(os.getenv("BOB_STM_TTL_HOURS", "72"))
    stm_inject_refresh_hours: int = int(os.getenv("BOB_STM_INJECT_REFRESH_HOURS", "24"))
    stm_top_k: int = int(os.getenv("BOB_STM_TOP_K", "6"))
    stm_embedding_dim: int = int(os.getenv("BOB_STM_EMBED_DIM", "256"))
    stm_max_entries: int = int(os.getenv("BOB_STM_MAX_ENTRIES", "200"))
    stm_max_entry_chars: int = int(os.getenv("BOB_STM_MAX_ENTRY_CHARS", "3072"))

    stm_promotion_access_min: int = int(os.getenv("BOB_STM_PROMOTION_ACCESS_MIN", "3"))
    stm_promotion_sessions_min: int = int(os.getenv("BOB_STM_PROMOTION_SESSIONS_MIN", "2"))
    stm_promotion_max_per_turn: int = int(os.getenv("BOB_STM_PROMOTION_MAX_PER_TURN", "2"))

    tool_sandbox_enabled: bool = os.getenv("BOB_TOOL_SANDBOX_ENABLED", "false").lower() in {"1", "true", "yes"}
    tool_roots: tuple[str, ...] = tuple(parse_allowed_roots(os.getenv("BOB_TOOL_ROOTS", "")))

    practice_candidates_file: str = os.getenv("BOB_PRACTICE_CANDIDATES", "./runtime/practice_candidates.jsonl")


def load_config() -> BobConfig:
    return BobConfig()
