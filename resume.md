Bradley R Bates
357 Lindsay Farm Dr.
Stony Point, NC 28678
916-718-7171

AI Stack Engineer · Game Systems · Applied ML
United States
GitHub: https://github.com/Bradsadevnow/bob

SUMMARY

AI systems engineer specializing in authoritative game engines, deterministic rules enforcement, and AI control surfaces. Built a shippable Magic: The Gathering rules engine and AI runtime designed to eliminate model hallucinations by constraining play through legal action schemas and engine-owned state. Focused on player-trust, debuggability, and human-auditable AI behavior in complex rules-driven games.

TECHNICAL PROJECTS
mtg_core — Principal Architect

Authoritative Magic: The Gathering Rules Engine (Phase-1, Shippable)

Designed and implemented a deterministic MTG rules engine in Python acting as the single source of truth for all game state mutation, independent of UI or AI control surfaces.

Enforced 15+ trigger types (ETB, dies, combat damage, etc.) with deterministic ordering and resolution

Implemented engine-owned action validation and resolution, ensuring all player and AI actions are legality-checked against current game state to prevent invalid plays and rules drift.

Built a player-scoped visibility system (VisibleState) that enforces hidden information boundaries while enabling safe legal action enumeration for AI and UI clients.

Engineered a schema-driven action surface that enumerates all legal moves per priority window, enabling AI, CLI, and GUI clients to operate without embedded rules logic.

Implemented full Phase-1 MTG gameplay loop, including:

Priority passing and stack resolution

Sorcery/instant timing rules

Mana production and cost payment (including X, alternate, and additional costs)

Combat (attackers, blockers, keywords, damage resolution)

State-based actions and win checks

Developed a data-driven card rules system using Scryfall-derived schemas, parsing oracle text into keywords, effects, triggered abilities, and activated abilities only as required by the active card pool.

Enforced complex mechanics including equipment, auras, flashback, goad, menace, lifelink, trample, deathtouch, and first/double strike, with deterministic resolution.

Designed the engine explicitly to support AI players by eliminating ambiguity, undefined behavior, and model inference from rules text.

Bob Runtime — Principal Architect

Human-Auditable AI Runtime for Rules-Driven Games

Architected a clean-slate AI runtime with authoritative state management, append-only telemetry, and explicit separation between game truth and model reasoning.

Designed a dual-layer control model:

Deterministic game engines (e.g., mtg_core) as ground truth

AI layers restricted to legal action schemas and tool-mediated queries

Implemented an approval-gated memory system:

Short-Term Memory (STM) for local continuity with bounded size and TTL

Long-Term Memory (LTM) writes requiring explicit human approval with a permanent audit ledger

Built a tool sandbox and gating model to safely expose external systems (game databases, filesystem, metadata services) without uncontrolled agent behavior.

Designed the runtime to support human-in-the-loop review, debugging, and replay—prioritizing player trust and developer observability over opaque autonomy.

Integrated MTG-specific memory boundaries to prevent leakage of hidden information while allowing durable learning of player preferences, strategic lessons, and tutoring style.

TECHNICAL SKILLS

Game & AI Systems
Rules Engines · Deterministic Simulation · Action Schema Design · State Machines · Turn-Based Systems · AI Control Surfaces

AI / ML
PyTorch · Transformer Architectures · LLM Orchestration · Agentic Workflows · Mechanistic Interpretability

Languages & Tools
Python (3.10+) · GDScript (Godot 4) · JSON/JSON-LD · Async Programming · Secure Tooling & Sandboxing

UI & Debugging
Dear PyGui · Gradio · Textual · Godot · High-Dimensional Data Visualization