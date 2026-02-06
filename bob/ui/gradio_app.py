from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, List, Dict, Tuple, Optional

from bob.config import load_config
from bob.memory.approval import ApprovalLedger, apply_approval_decisions
from bob.memory.schema import MemoryCandidate
from bob.memory.store import FileLTMStore
from bob.runtime.orchestrator import Orchestrator
from bob.turbotime.orchestrator import TurbotimeOrchestrator
from bob.practice import load_practice_candidates


def _normalize_history_to_messages(history: Any) -> List[Dict[str, Any]]:
    if not history:
        return []

    out: List[Dict[str, Any]] = []

    if isinstance(history, list):
        for item in history:
            # messages dicts
            if isinstance(item, dict) and "role" in item and "content" in item:
                role = str(item.get("role") or "")
                content = "" if item.get("content") is None else str(item.get("content"))
                out.append({"role": role, "content": content})
                continue

            # tuple-pair mode: (user, assistant)
            if isinstance(item, (list, tuple)) and len(item) == 2:
                user_msg, assistant_msg = item[0], item[1]
                if user_msg not in (None, ""):
                    out.append({"role": "user", "content": str(user_msg)})
                if assistant_msg not in (None, ""):
                    out.append({"role": "assistant", "content": str(assistant_msg)})
                continue

            # ChatMessage-like objects
            role = getattr(item, "role", None)
            content = getattr(item, "content", None)
            if role is not None or content is not None:
                out.append({"role": str(role or ""), "content": "" if content is None else str(content)})

    return out


def _read_last_turn(log_file: str) -> Dict[str, Any] | None:
    try:
        with open(log_file, "rb") as f:
            lines = f.readlines()
        if not lines:
            return None
        return json.loads(lines[-1].decode("utf-8"))
    except Exception:
        return None


def _format_stm_recall(turn: Dict[str, Any] | None) -> str:
    if not turn:
        return "(no turn log yet)"
    tools = turn.get("tools") or []
    for t in tools:
        if t.get("tool_name") != "STM_RECALL":
            continue
        query = t.get("query") or ""
        hits = t.get("hits") or []
        lines = [f"Query: {query}" if query else "Query: (empty)"]
        if not hits:
            lines.append("(no hits)")
            return "\n".join(lines)

        for h in hits:
            meta = h.get("metadata") or {}
            created = meta.get("created_at_utc") or ""
            text = str(h.get("text") or "").strip()
            if not text:
                continue
            prefix = f"- ({created}) " if created else "- "
            lines.append(prefix + text)
        return "\n".join(lines)

    return "(no STM recall this turn)"


def _format_stm_db(orch: Orchestrator, limit: int = 50) -> str:
    if not orch.stm:
        return "STM disabled or unavailable."
    try:
        rows = orch.stm.dump(limit=limit)
    except Exception as e:
        return f"STM dump error: {e}"
    if not rows:
        return "(STM empty)"

    lines: list[str] = []
    for r in rows:
        meta = r.get("metadata") or {}
        created = meta.get("created_at_utc") or ""
        expires = meta.get("expires_at_utc") or ""
        session_id = meta.get("session_id") or ""
        turn_no = meta.get("turn_number")
        text = str(r.get("text") or "").strip()
        if len(text) > 240:
            text = text[:237] + "..."
        line = f"- [{created}] (turn {turn_no}, session {session_id}) expires {expires}\n  {text}"
        lines.append(line)

    return "\n".join(lines)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _terminal_launch_command(cmd: str) -> Optional[List[str]]:
    terminals = [
        ("x-terminal-emulator", ["x-terminal-emulator", "-e", "bash", "-lc", cmd]),
        ("gnome-terminal", ["gnome-terminal", "--", "bash", "-lc", cmd]),
        ("konsole", ["konsole", "-e", "bash", "-lc", cmd]),
        ("xfce4-terminal", ["xfce4-terminal", "-e", "bash", "-lc", cmd]),
        ("xterm", ["xterm", "-e", "bash", "-lc", cmd]),
        ("alacritty", ["alacritty", "-e", "bash", "-lc", cmd]),
        ("kitty", ["kitty", "-e", "bash", "-lc", cmd]),
    ]
    for name, args in terminals:
        if shutil.which(name):
            return args
    return None


def _launch_mtg_dpg() -> str:
    repo_root = _repo_root()
    py = sys.executable or "python"
    cmd = f'"{py}" run_mtg.py --ui dpg'
    term_cmd = _terminal_launch_command(cmd)
    try:
        if term_cmd:
            subprocess.Popen(term_cmd, cwd=str(repo_root), start_new_session=True)
            return "Launched MTG DPG UI in a new terminal."
        subprocess.Popen([py, "run_mtg.py", "--ui", "dpg"], cwd=str(repo_root), start_new_session=True)
        return "Launched MTG DPG UI (no terminal found; pregame prompts may be hidden)."
    except Exception as e:
        return f"Failed to launch MTG DPG UI: {e}"


def _extract_memory_candidates(turn: Dict[str, Any] | None) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    if not turn:
        return [], {}
    raw = turn.get("memory_candidates") or []
    candidates: List[Dict[str, Any]] = []
    promotion_map: Dict[str, str] = {}
    for obj in raw:
        if not isinstance(obj, dict):
            continue
        try:
            cand = MemoryCandidate.from_obj(obj)
        except Exception:
            continue
        cand_dict = cand.to_dict()
        stm_id = obj.get("promotion_stm_id")
        if isinstance(stm_id, str) and stm_id:
            cand_dict["promotion_stm_id"] = stm_id
            promotion_map[cand_dict["fingerprint"]] = stm_id
        candidates.append(cand_dict)
    return candidates, promotion_map


def _candidates_to_table(candidates: List[Dict[str, Any]]) -> List[List[Any]]:
    rows: List[List[Any]] = []
    for i, c in enumerate(candidates, start=1):
        tags = ", ".join(c.get("tags") or [])
        origin = "promotion" if c.get("promotion_stm_id") else "think"
        rows.append(
            [
                i,
                c.get("type"),
                c.get("text"),
                c.get("source"),
                c.get("ttl_days"),
                tags,
                origin,
                False,
            ]
        )
    return rows


def build_app():
    import gradio as gr

    cfg = load_config()

    orch = Orchestrator(cfg)
    session = orch.new_session()
    turbo_orch = TurbotimeOrchestrator(cfg)
    turbo_session = turbo_orch.new_session()
    ledger = ApprovalLedger(cfg.approval_ledger_file)
    ltm = FileLTMStore(cfg.ltm_file)

    with gr.Blocks(title=f"{cfg.display_name} Runtime") as demo:
        gr.Markdown(f"# {cfg.display_name}\nLocal runtime (v0).")

        with gr.Row():
            use_openai = gr.Checkbox(label="Use OpenAI API (remote)", value=False)
            use_stm = gr.Checkbox(label="Enable STM recall", value=True)
            turbotime_tool = gr.Dropdown(
                label="TURBOTIME Tool",
                choices=["OFF"] + turbo_orch.tools.public_tools,
                value="OFF",
                allow_custom_value=False,
            )
        turb_status = gr.Markdown("TURBOTIME: OFF")
        active_tool_state = gr.State("OFF")

        with gr.Tabs():
            with gr.TabItem("Chat"):
                chatbot = gr.Chatbot()
                msg = gr.Textbox(placeholder="Say something…", scale=4)
                temp = gr.Slider(0.0, 1.0, value=0.7, step=0.05, label="Temperature")

            with gr.TabItem("STM Recall"):
                stm_recall = gr.Textbox(label="Last STM Recall", lines=16, interactive=False)

            with gr.TabItem("STM DB"):
                stm_db = gr.Textbox(label="STM Database Snapshot", lines=20, interactive=False)
                refresh_btn = gr.Button("Refresh STM DB")

            with gr.TabItem("Memory Approval"):
                mem_state = gr.State([])
                mem_table = gr.Dataframe(
                    headers=["idx", "type", "text", "source", "ttl_days", "tags", "origin", "approve"],
                    datatype=["number", "str", "str", "str", "number", "str", "str", "bool"],
                    row_count=(0, "dynamic"),
                    interactive=True,
                    label="Memory Candidates",
                )
                reject_unchecked = gr.Checkbox(label="Reject unchecked (default reject)", value=True)
                commit_btn = gr.Button("Commit approvals")
                mem_status = gr.Textbox(label="Memory Status", lines=3, interactive=False)
                mem_refresh = gr.Button("Refresh candidates")
                mem_practice = gr.Button("Load practice candidates")
                gr.Markdown(
                    "Edit schema keys: `text`, `type`, `tags`, `ttl_days`, `source`, `why_store`.\n"
                    "Types: `preference|fact|procedure|project_decision|mtg_profile|mtg_lesson`.\n"
                    "Sources: `user_said|assistant_inferred|tool_output`."
                )
                edit_idx = gr.Number(label="Edit candidate idx", value=1, precision=0)
                edit_json = gr.Textbox(label="Edited candidate JSON", lines=6)
                load_edit_btn = gr.Button("Load JSON for idx")
                apply_edit_btn = gr.Button("Apply edit")
                edit_status = gr.Textbox(label="Edit Status", lines=2, interactive=False)

            with gr.TabItem("MTG Playtest"):
                gr.Markdown("Launch the Dear PyGui playtest UI in a separate terminal window.")
                launch_mtg_btn = gr.Button("Launch DPG Playtest UI")
                mtg_status = gr.Textbox(label="Launch Status", lines=2, interactive=False)

        def refresh_db():
            return _format_stm_db(orch)

        refresh_btn.click(fn=refresh_db, outputs=[stm_db])

        def refresh_candidates():
            turn = _read_last_turn(cfg.log_file)
            cands, _ = _extract_memory_candidates(turn)
            table = _candidates_to_table(cands)
            status = f"{len(cands)} candidate(s) loaded." if cands else "No memory candidates."
            return table, cands, status

        mem_refresh.click(fn=refresh_candidates, outputs=[mem_table, mem_state, mem_status])

        def load_practice():
            rows = load_practice_candidates(cfg.practice_candidates_file)
            cands = []
            for obj in rows:
                if not isinstance(obj, dict):
                    continue
                try:
                    MemoryCandidate.from_obj(obj)
                except Exception:
                    continue
                cands.append(obj)
            table = _candidates_to_table(cands)
            status = f"{len(cands)} practice candidate(s) loaded." if cands else "No practice candidates."
            return table, cands, status

        mem_practice.click(fn=load_practice, outputs=[mem_table, mem_state, mem_status])

        launch_mtg_btn.click(fn=_launch_mtg_dpg, outputs=[mtg_status])

        def load_candidate_json(idx, candidates_state):
            if not candidates_state:
                return "", "No candidates loaded."
            try:
                i = int(idx)
            except Exception:
                return "", "Invalid index."
            if i < 1 or i > len(candidates_state):
                return "", "Index out of range."
            cand = candidates_state[i - 1]
            if not isinstance(cand, dict):
                return "", "Invalid candidate."
            base = {
                "text": cand.get("text"),
                "type": cand.get("type"),
                "tags": cand.get("tags"),
                "ttl_days": cand.get("ttl_days"),
                "source": cand.get("source"),
                "why_store": cand.get("why_store"),
            }
            return json.dumps(base, ensure_ascii=False), f"Loaded candidate {i}."

        load_edit_btn.click(
            fn=load_candidate_json,
            inputs=[edit_idx, mem_state],
            outputs=[edit_json, edit_status],
        )

        def apply_edit(idx, json_text, candidates_state, table_rows):
            if not candidates_state:
                return table_rows, candidates_state, "No candidates loaded."
            try:
                i = int(idx)
            except Exception:
                return table_rows, candidates_state, "Invalid index."
            if i < 1 or i > len(candidates_state):
                return table_rows, candidates_state, "Index out of range."
            if not json_text or not str(json_text).strip():
                return table_rows, candidates_state, "Edit JSON is empty."
            try:
                obj = json.loads(json_text or "")
            except Exception as e:
                return table_rows, candidates_state, f"Invalid JSON: {e}"
            required = {"text", "type", "tags", "ttl_days", "source", "why_store"}
            missing = [k for k in sorted(required) if k not in obj]
            if missing:
                return table_rows, candidates_state, f"Missing keys: {', '.join(missing)}"
            try:
                cand = MemoryCandidate.from_obj(obj)
            except Exception as e:
                return table_rows, candidates_state, f"Invalid candidate: {e}"

            updated = cand.to_dict()
            prior = candidates_state[i - 1]
            if isinstance(prior, dict):
                stm_id = prior.get("promotion_stm_id")
                if isinstance(stm_id, str) and stm_id:
                    updated["promotion_stm_id"] = stm_id
                original_fp = prior.get("_edited_from_fingerprint") or prior.get("fingerprint")
                if isinstance(original_fp, str) and original_fp and original_fp != updated.get("fingerprint"):
                    updated["_edited_from_fingerprint"] = original_fp
                    updated["_edited"] = True

            candidates_state[i - 1] = updated

            if table_rows and i - 1 < len(table_rows):
                row = table_rows[i - 1]
                approve = False
                if isinstance(row, list) and row:
                    approve = bool(row[-1])
                tags = ", ".join(updated.get("tags") or [])
                origin = "promotion" if updated.get("promotion_stm_id") else "think"
                table_rows[i - 1] = [
                    i,
                    updated.get("type"),
                    updated.get("text"),
                    updated.get("source"),
                    updated.get("ttl_days"),
                    tags,
                    origin,
                    approve,
                ]

            return table_rows, candidates_state, f"Updated candidate {i}."

        apply_edit_btn.click(
            fn=apply_edit,
            inputs=[edit_idx, edit_json, mem_state, mem_table],
            outputs=[mem_table, mem_state, edit_status],
        )

        def commit_approvals(table_rows, candidates_state, reject_unchecked_flag):
            if not candidates_state:
                return table_rows, candidates_state, "No candidates to approve."

            decisions: List[Dict[str, Any]] = []
            promotion_map = {
                str(c.get("fingerprint")): c.get("promotion_stm_id")
                for c in candidates_state
                if isinstance(c, dict)
            }
            for c in candidates_state:
                if not isinstance(c, dict):
                    continue
                orig = c.get("_edited_from_fingerprint")
                if isinstance(orig, str) and orig:
                    promotion_map[orig] = c.get("promotion_stm_id")

            for row in table_rows or []:
                if not row:
                    continue
                try:
                    idx = int(row[0])
                except Exception:
                    continue
                if idx < 1 or idx > len(candidates_state):
                    continue
                approved = bool(row[-1])
                if not approved and not reject_unchecked_flag:
                    continue
                cand = candidates_state[idx - 1]
                orig_fp = cand.get("_edited_from_fingerprint") if isinstance(cand, dict) else None
                fp = str(orig_fp or cand.get("fingerprint") or "").strip()
                if not fp:
                    continue
                decision: Dict[str, Any] = {"candidate_fingerprint": fp, "approved": approved}
                if isinstance(cand, dict) and cand.get("_edited"):
                    edited_obj = {
                        "text": cand.get("text"),
                        "type": cand.get("type"),
                        "tags": cand.get("tags"),
                        "ttl_days": cand.get("ttl_days"),
                        "source": cand.get("source"),
                        "why_store": cand.get("why_store"),
                    }
                    decision["edited"] = edited_obj
                    decision["note"] = "edited_in_gradio"
                decisions.append(decision)

            if not decisions:
                return table_rows, candidates_state, "No approval decisions recorded."

            candidates: List[MemoryCandidate] = []
            for c in candidates_state:
                if not isinstance(c, dict):
                    continue
                try:
                    candidates.append(MemoryCandidate.from_obj(c))
                except Exception:
                    continue

            approved = apply_approval_decisions(
                candidates=candidates,
                decisions=decisions,
                reviewer="gradio",
                ledger=ledger,
            )

            if orch.stm:
                for d in decisions:
                    fp = str(d.get("candidate_fingerprint") or "").strip()
                    if not fp:
                        continue
                    stm_id = promotion_map.get(fp)
                    if not stm_id:
                        continue
                    ok = bool(d.get("approved"))
                    orch.stm.mark_promotion_result(
                        stm_id=stm_id,
                        approved=ok,
                        reviewer="gradio",
                        note=None,
                    )

            for c in approved:
                ltm.upsert(candidate=c, extra_payload={"session_id": session.session_id})

            status = f"Committed {len(approved)} approved item(s); {len(decisions) - len(approved)} rejected."
            return [], [], status

        commit_btn.click(
            fn=commit_approvals,
            inputs=[mem_table, mem_state, reject_unchecked],
            outputs=[mem_table, mem_state, mem_status],
        )

        def _format_turbo_status(pending_tool: str, active_tool: str) -> str:
            active = active_tool if active_tool and active_tool != "OFF" else "OFF"
            pending = pending_tool if pending_tool and pending_tool != "OFF" else "OFF"
            if pending != active:
                return f"TURBOTIME: {active} (pending: {pending})"
            return "TURBOTIME: OFF" if active == "OFF" else f"TURBOTIME: {active}"

        def respond(
            history,
            user_text,
            temperature,
            use_openai_flag,
            use_stm_flag,
            turbotime_tool_name,
            active_tool_name,
        ):
            status_label = _format_turbo_status(turbotime_tool_name, active_tool_name)
            active_tool = active_tool_name or "OFF"
            pending_tool = turbotime_tool_name or "OFF"
            active_on = bool(active_tool and active_tool != "OFF")
            pending_on = bool(pending_tool and pending_tool != "OFF")
            use_turbotime_flag = active_on or pending_on
            enabled_tools = [active_tool] if active_on else []
            pending_tools = [pending_tool] if pending_tool != active_tool else []
            if not user_text:
                return history, "", "", "", [], [], "", "", status_label, active_tool_name

            history_msgs = _normalize_history_to_messages(history)
            history_msgs.append({"role": "user", "content": user_text})

            prev_turn = _read_last_turn(cfg.log_file)
            prev_recall = _format_stm_recall(prev_turn)
            prev_db = _format_stm_db(orch)
            prev_cands, _ = _extract_memory_candidates(prev_turn)
            prev_table = _candidates_to_table(prev_cands)

            active_orch = turbo_orch if use_turbotime_flag else orch
            active_session = turbo_session if use_turbotime_flag else session

            if use_openai_flag:
                if not cfg.chat_remote.api_key:
                    assistant_text = (
                        "Config error: OpenAI API key is missing. "
                        "Set `BOB_CHAT_API_KEY` (or `OPENAI_API_KEY`) and restart."
                    )
                    yield (
                        history_msgs + [{"role": "assistant", "content": assistant_text}],
                        "",
                        prev_recall,
                        prev_db,
                        prev_table,
                        prev_cands,
                        "",
                        "",
                        status_label,
                        active_tool_name,
                    )
                    return
                if "api.openai.com" in cfg.chat_remote.base_url and cfg.chat_remote.model.startswith("mistralai/"):
                    assistant_text = (
                        "Config error: `BOB_CHAT_MODEL` is set to a local model string. "
                        "For OpenAI, set it to a valid OpenAI model (e.g., `gpt-4o-mini`) and restart."
                    )
                    yield (
                        history_msgs + [{"role": "assistant", "content": assistant_text}],
                        "",
                        prev_recall,
                        prev_db,
                        prev_table,
                        prev_cands,
                        "",
                        "",
                        status_label,
                        active_tool_name,
                    )
                    return

            assistant_text = ""
            if use_turbotime_flag:
                stream = active_orch.run_turn_stream(
                    session=active_session,
                    user_input=user_text,
                    temperature=float(temperature),
                    use_remote=bool(use_openai_flag),
                    use_stm=bool(use_stm_flag),
                    enabled_tools=enabled_tools,
                    pending_tools=pending_tools,
                )
            else:
                stream = active_orch.run_turn_stream(
                    session=active_session,
                    user_input=user_text,
                    temperature=float(temperature),
                    use_remote=bool(use_openai_flag),
                    use_stm=bool(use_stm_flag),
                )
            try:
                for tok in stream:
                    assistant_text += tok
                    yield (
                        history_msgs + [{"role": "assistant", "content": assistant_text}],
                        "",
                        prev_recall,
                        prev_db,
                        prev_table,
                        prev_cands,
                        "",
                        "",
                        status_label,
                        active_tool_name,
                    )
            except Exception as e:
                assistant_text = f"Error: {e}"
                yield (
                    history_msgs + [{"role": "assistant", "content": assistant_text}],
                    "",
                    prev_recall,
                    prev_db,
                    prev_table,
                    prev_cands,
                    "",
                    "",
                    status_label,
                    active_tool_name,
                )
                return

            # after turn completes, update STM displays
            turn = _read_last_turn(cfg.log_file)
            recall_text = _format_stm_recall(turn)
            db_text = _format_stm_db(orch)
            cands, _ = _extract_memory_candidates(turn)
            table = _candidates_to_table(cands)
            status = f"{len(cands)} candidate(s) loaded." if cands else "No memory candidates."
            new_active = pending_tool if use_turbotime_flag else "OFF"
            status_label = _format_turbo_status(pending_tool, new_active)
            yield (
                history_msgs + [{"role": "assistant", "content": assistant_text}],
                "",
                recall_text,
                db_text,
                table,
                cands,
                status,
                "",
                status_label,
                new_active,
            )

        msg.submit(
            respond,
            inputs=[chatbot, msg, temp, use_openai, use_stm, turbotime_tool, active_tool_state],
            outputs=[
                chatbot,
                msg,
                stm_recall,
                stm_db,
                mem_table,
                mem_state,
                mem_status,
                edit_status,
                turb_status,
                active_tool_state,
            ],
        )
        turbotime_tool.change(
            fn=_format_turbo_status,
            inputs=[turbotime_tool, active_tool_state],
            outputs=[turb_status],
        )

    return demo


def main() -> None:
    try:
        app = build_app()
    except ImportError as e:
        missing = str(e)
        raise SystemExit(
            "Gradio is not installed.\n"
            "Install dependencies, then rerun:\n"
            "  pip install -e .\n"
            f"ImportError: {missing}"
        )
    app.launch()


if __name__ == "__main__":
    main()
