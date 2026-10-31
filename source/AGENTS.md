# A-leggo — Agent-Native Architecture

## Overview

A-leggo is an **agent-native** intelligent learning companion organized
around a two-layer plugin model — single-shot **Tools** invoked by the
LLM, and multi-stage **Capabilities** that take over a turn — exposed
through three entry points: CLI, WebSocket API, and Python SDK.

## Architecture

```
Entry Points:  CLI (Typer)  |  WebSocket /ws  |  Python SDK
                    ↓                   ↓                   ↓
              ┌─────────────────────────────────────────────────┐
              │              ChatOrchestrator                    │
              │   routes UnifiedContext → selected Capability    │
              │   (defaults to `chat`)                           │
              └──────────┬──────────────┬───────────────────────┘
                         │              │
              ┌──────────▼──┐  ┌────────▼──────────┐
              │ ToolRegistry │  │ CapabilityRegistry │
              │  (Level 1)   │  │   (Level 2)        │
              └──────────────┘  └────────────────────┘
```

All capabilities emit on a shared `StreamBus`; the orchestrator fans
events out to consumers. Runtime settings live in
`data/user/settings/*.json` — project-root `.env` files are intentionally
ignored.

### Level 1 — Tools

Single-function tools the LLM picks on demand. Four user-toggleable tools
surface in `/settings/tools`:

| Tool           | Description                                   |
| -------------- | --------------------------------------------- |
| `brainstorm`   | Breadth-first idea exploration with rationale |
| `web_search`   | Web search with citations                     |
| `paper_search` | arXiv preprint search                         |
| `reason`       | Dedicated deep-reasoning LLM call             |

The rest are **context-gated**: the chat capability auto-mounts them from
`ToolMountFlags` (presence of a KB, attachments, sandbox availability, …), and
any of them can also be force-enabled via `--tool`. Auto-mounted set: `rag`,
`read_source`, `read_memory`, `write_memory`, `read_skill`, `load_tools`,
`exec`, `code_execution` (sandboxed Python: NL intent → code → run),
`list_notebook`, `write_note`, `web_fetch`, `github`, `cron`,
`ask_user` (pauses the turn and resumes with the user's reply), plus the
mastery-path tools. `geogebra_analysis` is parked under
`COMING_SOON_TOOL_TYPES`.

### Level 2 — Capabilities

Multi-stage pipelines that own the turn:

| Capability       | Stages                                                |
| ---------------- | ----------------------------------------------------- |
| `chat`           | exploring → responding (single agentic loop, default) |
| `mastery_path`   | responding (Guided Learning — chat loop + mastery tools, gated per topic type) |
| `deep_solve`     | planning → reasoning → writing                        |
| `deep_question`  | ideation → generation                                 |
| `deep_research`  | rephrasing → decomposing → researching → reporting    |
| `visualize`      | analyzing → generating → reviewing (SVG / Chart.js / Mermaid / HTML; or routes to Manim sub-stages via `render_type`) |
| `math_animator`  | concept_analysis → concept_design → code_generation → code_retry → summary → render_output |

All capabilities converge on `emit_capability_result()` in
`aleggo/capabilities/_shared.py` so every turn emits the same envelope
(response payload + `cost_summary` from `UsageTracker`). Status copy and
prompts are i18n'd via `capabilities/prompts/{en,zh}/<name>.yaml`.

## CLI Usage

```bash
# Install
pip install aleggo-next      # Full app (CLI + Web/API + packaged Web assets)
pip install aleggo-next-cli  # CLI-only

# Run any capability
aleggo run chat "Explain Fourier transform"
aleggo run deep_solve "Solve x^2=4" -t rag --kb my-kb
aleggo run visualize "Animate sine wave" --config render_mode=manim_video

# Interactive REPL
aleggo chat
# (inside the REPL: /regenerate or /retry re-runs the last user message)

# Partners (IM-connected companions)
aleggo partner list

# Knowledge bases, memory, server
aleggo kb list
aleggo kb create my-kb --doc textbook.pdf
aleggo memory show
aleggo serve --port 8001       # API server only
aleggo start                   # backend + frontend together
```

## Key Files

| Path                                       | Purpose                              |
| ------------------------------------------ | ------------------------------------ |
| `aleggo/runtime/orchestrator.py`        | `ChatOrchestrator` — unified entry   |
| `aleggo/runtime/launcher.py`            | Backend + frontend lifecycle / port discovery |
| `aleggo/runtime/registry/`              | Tool + Capability registries         |
| `aleggo/runtime/bootstrap/builtin_capabilities.py` | Built-in capability class paths |
| `aleggo/services/config/runtime_settings.py` | JSON settings + process-env overrides |
| `aleggo/core/stream.py`, `stream_bus.py` | StreamEvent protocol + async fan-out |
| `aleggo/core/tool_protocol.py`          | `BaseTool` + `ToolDefinition`         |
| `aleggo/core/capability_protocol.py`    | `BaseCapability` + `CapabilityManifest` |
| `aleggo/core/context.py`                | `UnifiedContext` dataclass            |
| `aleggo/tools/builtin/__init__.py`      | All built-in tool wrappers           |
| `aleggo/capabilities/`                  | Built-in capability implementations  |
| `aleggo/app/facade.py`                  | `AleggoApp` — Python SDK facade    |
| `aleggo_cli/main.py`                    | Typer CLI entry point                |
| `aleggo/api/routers/unified_ws.py`      | Unified WebSocket endpoint           |

## Dependency Layers

Public install paths and source extras are defined in `pyproject.toml`.
Requirements files mirror the same dependency groups for Docker/CI installs.

```
pip install aleggo-next      — Full app (CLI + Web/API + packaged Web assets)
pip install aleggo-next-cli  — CLI-only (LLM + RAG + providers + document parsing)
pip install -e .           — Source install for development

Source extras (.[ extra ], defined in pyproject.toml):
.[cli]            — CLI-only dependency set
.[server]         — Web/API server dependencies
.[partners]       — Partner channel SDKs  (legacy alias: .[tutorbot])
.[matrix]         — Matrix channel for Partners (matrix-nio; needs libolm)
.[matrix-e2e]     — Matrix with end-to-end encryption (matrix-nio[e2e])
.[math-animator]  — Manim addon (powers `visualize` Manim renders + `aleggo run math_animator`)
.[dev]            — Test / lint tooling
.[all]            — Everything above
```
