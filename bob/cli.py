from __future__ import annotations

import argparse

from bob.config import load_config
from bob.memory.approval import ApprovalLedger, apply_approval_decisions
from bob.memory.schema import MemoryCandidate
from bob.memory.store import FileLTMStore
from bob.practice import run_practice
from bob.runtime.orchestrator import Orchestrator
from bob.turbotime.orchestrator import TurbotimeOrchestrator


def _print_candidates(candidates: list[MemoryCandidate]) -> None:
    for i, c in enumerate(candidates, start=1):
        print(f"[{i}] type={c.type} source={c.source} ttl_days={c.ttl_days} tags={c.tags}")
        print(f"    text: {c.text}")
        print(f"    why:  {c.why_store}")


def _prompt_inline_approval(candidates: list[MemoryCandidate]) -> list[dict]:
    """
    CLI approval protocol:
    - default is review one-by-one (i)
    - allow approve-all / reject-all / edit JSON
    Returns decision dicts compatible with apply_approval_decisions().
    """
    if not candidates:
        return []

    print("\n--- Memory candidates proposed (requires approval) ---")
    _print_candidates(candidates)
    print("Actions: [i] review one-by-one (recommended), [a] approve all, [n] reject all")
    print("         [e] edit candidate JSON by index, [s] skip for now")

    while True:
        choice = input("Memory action (i/a/n/e/s): ").strip().lower()
        if choice in {"i", "a", "n", "e", "s"}:
            break

    decisions: list[dict] = []
    if choice == "s":
        return decisions

    if choice == "a":
        for c in candidates:
            decisions.append({"candidate_fingerprint": c.fingerprint(), "approved": True})
        return decisions

    if choice == "n":
        for c in candidates:
            decisions.append({"candidate_fingerprint": c.fingerprint(), "approved": False})
        return decisions

    if choice == "e":
        import json

        idx_raw = input("Edit which index (1..N): ").strip()
        try:
            idx = int(idx_raw)
        except Exception:
            return decisions
        if idx < 1 or idx > len(candidates):
            return decisions

        target = candidates[idx - 1]
        print("Paste edited JSON for this candidate (single line).")
        print("Schema keys: text,type,tags,ttl_days,source,why_store")
        edited_raw = input("> ").strip()
        try:
            edited_obj = json.loads(edited_raw)
            # Validate it immediately; if invalid, treat as reject
            MemoryCandidate.from_obj(edited_obj)
        except Exception as e:
            print(f"[edit] Invalid JSON/candidate: {e}")
            decisions.append({"candidate_fingerprint": target.fingerprint(), "approved": False, "note": "edit_invalid"})
            return decisions

        decisions.append(
            {
                "candidate_fingerprint": target.fingerprint(),
                "approved": True,
                "edited": edited_obj,
                "note": "edited_in_cli",
            }
        )
        return decisions

    # choice == "i"
    for c in candidates:
        while True:
            ans = input(f"Approve memory [{c.type}] '{c.text[:50]}'? (y/n/e): ").strip().lower()
            if ans in {"y", "n", "e"}:
                break
        if ans == "y":
            decisions.append({"candidate_fingerprint": c.fingerprint(), "approved": True})
        elif ans == "n":
            decisions.append({"candidate_fingerprint": c.fingerprint(), "approved": False})
        else:
            import json

            print("Paste edited JSON for this candidate (single line).")
            edited_raw = input("> ").strip()
            try:
                edited_obj = json.loads(edited_raw)
                MemoryCandidate.from_obj(edited_obj)
                decisions.append(
                    {
                        "candidate_fingerprint": c.fingerprint(),
                        "approved": True,
                        "edited": edited_obj,
                        "note": "edited_in_cli",
                    }
                )
            except Exception as e:
                print(f"[edit] Invalid JSON/candidate: {e}")
                decisions.append({"candidate_fingerprint": c.fingerprint(), "approved": False, "note": "edit_invalid"})

    return decisions


def main() -> None:
    parser = argparse.ArgumentParser(description="Bob CLI (streaming)")
    parser.add_argument("--temp", type=float, default=0.7, help="Temperature (default: 0.7)")
    parser.add_argument("--show-think", action="store_true", help="Print THINK output after each turn (debug)")
    args = parser.parse_args()

    cfg = load_config()
    orch = Orchestrator(cfg)
    session = orch.new_session()
    turbo_orch = TurbotimeOrchestrator(cfg)
    turbo_session = turbo_orch.new_session()
    turbotime_enabled = False
    turbotime_tool: str | None = None
    pending_tool_enablement: str | None = None
    available_tools = turbo_orch.tools.public_tools

    ledger = ApprovalLedger(cfg.approval_ledger_file)
    ltm = FileLTMStore(cfg.ltm_file)

    print(f"{cfg.display_name} CLI — type 'exit' to quit")
    print("Commands: /mtg play [tui|plain|dpg]  (interactive MTG match, default: dpg)")
    print("          /turbotime <tool|off>  (select TURBOTIME tool)")
    print("          /turbotime tools       (list available tools)")
    print("          /practice             (practice/learn candidate scan)")
    while True:
        try:
            user = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye")
            return

        if not user:
            continue
        if user.lower() in {"exit", "quit", ":q"}:
            print("Bye")
            return

        if user.strip().lower().startswith("/mtg"):
            parts = user.strip().split()
            sub = parts[1].lower() if len(parts) > 1 else "help"
            if sub in {"help", "-h", "--help"}:
                print("MTG commands:")
                print("  /mtg play [tui|plain|dpg]   Start an interactive match (default: dpg)")
                continue
            if sub == "play":
                from run_mtg import run_interactive

                ui = (parts[2].lower() if len(parts) > 2 else "dpg").strip()
                if ui not in {"tui", "plain", "dpg"}:
                    ui = "dpg"
                run_interactive(ui=ui)
                continue

            print(f"Unknown /mtg subcommand: {sub}")
            continue

        if user.strip().lower().startswith("/turbotime"):
            parts = user.strip().split()
            sub = parts[1].lower() if len(parts) > 1 else "status"
            if sub in {"tools", "list"}:
                tool_line = ", ".join(available_tools) if available_tools else "(none)"
                print(f"[turbotime] available tools: {tool_line}")
            elif sub in {"off", "disable"}:
                turbotime_enabled = False
                turbotime_tool = None
                pending_tool_enablement = "OFF"
                print("[turbotime] disabled")
            elif sub in {"on", "enable"}:
                if not turbotime_tool and available_tools:
                    turbotime_tool = available_tools[0]
                turbotime_enabled = bool(turbotime_tool)
                if turbotime_tool:
                    pending_tool_enablement = turbotime_tool
                    print(f"[turbotime] enabled with {turbotime_tool}")
                else:
                    print("[turbotime] no tools available")
            elif sub in {"status"}:
                if turbotime_enabled and turbotime_tool:
                    print(f"[turbotime] on ({turbotime_tool})")
                else:
                    print("[turbotime] off (use /turbotime <tool>)")
            else:
                resolved = turbo_orch.tools.resolve_public_name(parts[1])
                if not resolved:
                    tool_line = ", ".join(available_tools) if available_tools else "(none)"
                    print(f"[turbotime] unknown tool '{parts[1]}'. Available: {tool_line}")
                else:
                    turbotime_tool = resolved
                    turbotime_enabled = True
                    pending_tool_enablement = turbotime_tool
                    print(f"[turbotime] enabled with {turbotime_tool}")
            continue

        if user.strip().lower().startswith("/practice"):
            res = run_practice(cfg)
            if not res.candidates:
                print("[practice] no candidates found.")
                continue
            print(f"[practice] {len(res.candidates)} candidate(s) ready for approval.")
            decisions = _prompt_inline_approval(res.candidates)
            approved = apply_approval_decisions(
                candidates=res.candidates,
                decisions=decisions,
                reviewer="brad",
                ledger=ledger,
            )
            for c in approved:
                ltm.upsert(candidate=c, extra_payload={"session_id": session.session_id})
            if approved:
                print(f"[memory] committed {len(approved)} item(s) to LTM.")
            else:
                print("[memory] no items committed.")
            continue

        print(f"{cfg.display_name}: ", end="", flush=True)
        active_orch = turbo_orch if turbotime_enabled else orch
        active_session = turbo_session if turbotime_enabled else session
        out = []
        if turbotime_enabled:
            enabled_tools = [turbotime_tool] if turbotime_tool else []
            stream = active_orch.run_turn_stream(
                session=active_session,
                user_input=user,
                temperature=float(args.temp),
                enabled_tools=enabled_tools,
                pending_tools=[pending_tool_enablement] if pending_tool_enablement else None,
            )
            pending_tool_enablement = None
        else:
            stream = active_orch.run_turn_stream(
                session=active_session,
                user_input=user,
                temperature=float(args.temp),
            )
            pending_tool_enablement = None
        for tok in stream:
            out.append(tok)
            print(tok, end="", flush=True)
        print("\n")

        # Read last log line for think + memory candidates (append-only).
        last = None
        try:
            import json

            with open(cfg.log_file, "rb") as f:
                lines = f.readlines()
            if lines:
                last = json.loads(lines[-1].decode("utf-8"))
        except Exception:
            last = None

        if last:
            think = (last.get("think") or "").strip()
            if args.show_think and think:
                print("----- THINK (debug) -----")
                print(think)
                print("-------------------------\n")

            # Inline memory approval (approval-gated LTM)
            raw_cands = last.get("memory_candidates") or []
            candidates: list[MemoryCandidate] = []
            promotion_map: dict[str, str] = {}
            for obj in raw_cands:
                if not isinstance(obj, dict):
                    continue
                try:
                    cand = MemoryCandidate.from_obj(obj)
                    candidates.append(cand)
                    stm_id = obj.get("promotion_stm_id")
                    if isinstance(stm_id, str) and stm_id:
                        promotion_map[cand.fingerprint()] = stm_id
                except Exception:
                    continue

            if candidates:
                decisions = _prompt_inline_approval(candidates)
                approved = apply_approval_decisions(
                    candidates=candidates,
                    decisions=decisions,
                    reviewer="brad",
                    ledger=ledger,
                )
                if active_orch.stm and decisions:
                    for d in decisions:
                        fp = str(d.get("candidate_fingerprint") or "").strip()
                        if not fp:
                            continue
                        stm_id = promotion_map.get(fp)
                        if not stm_id:
                            continue
                        ok = bool(d.get("approved"))
                        note = d.get("note")
                        if not isinstance(note, str):
                            note = None
                        active_orch.stm.mark_promotion_result(
                            stm_id=stm_id,
                            approved=ok,
                            reviewer="brad",
                            note=note,
                        )
                for c in approved:
                    ltm.upsert(candidate=c, extra_payload={"session_id": session.session_id})
                if approved:
                    print(f"[memory] committed {len(approved)} item(s) to LTM.")
                else:
                    print("[memory] no items committed.")


if __name__ == "__main__":
    main()
