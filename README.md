# PR Review Swarm

A multi-agent system that reviews GitHub pull requests the way an engineering team would: specialized reviewers (correctness, security, style, test coverage) examine the change in parallel, each with its own scoped context and tool access, and a synthesizer merges their findings into one prioritized review.

**Status:** core system complete. Next up: the eval harness —> recall/precision against real human reviews, plus a single-agent-baseline ablation.

## How it works

```
PR URL → GitHub client → diff parser → orchestrator ─┬→ Correctness (tool loop) ─┐
         (cached, read-only)  (rule-based routing)   ├→ Security   (tool loop)   ├→ Synthesizer → Markdown review
                                                     ├→ Style      (single-pass) │  (LLM merge,     + JSON trace
                                                     └→ Test Coverage (1-pass) ──┘   dedupe, rank)
```

- **Orchestrator** (deterministic, no LLM): classifies the PR from file kinds, selects only the relevant agents; a docs-only PR runs zero agents and costs $0, and runs them concurrently with per-agent timeouts and failure isolation. Every skip is recorded with its reason.
- **Correctness & Security** get a bounded investigation loop (max 3 read-only tool calls: `read_file`, `expand_context`, `list_files`) to check code outside the diff before claiming an issue. **Style & Test Coverage** are single-pass; their jobs don't benefit from investigation. Style is capped at minor/nit severity by design.
- **Structured output by construction:** agents report via a forced tool call against a strict JSON schema; semantic validation gets one corrective retry.
- **Synthesizer** merges duplicate findings across reviewers (with source tracking), marks genuine disagreements, filters below a 0.4 confidence threshold, and drafts the final review. If it fails, the raw findings are returned — synthesis never sinks a run.
- **Every run writes a replayable JSON trace** to `traces/`: routing decisions, each agent's full context, every model and tool call with token counts and cost.

## Usage

```bash
uv sync
cp .env.example .env   # then paste your ANTHROPIC_API_KEY
uv run python -m src.cli review https://github.com/psf/requests/pull/6710
```

Configuration (via `.env` or environment):

| Variable | Required | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | yes | Model access (default model: Claude Sonnet 4.6) |
| `GITHUB_TOKEN` | no | Raises GitHub rate limit from 60 to 5,000 req/hr |
| `PR_REVIEW_MODEL` | no | Override the review model |

Typical run on a ~100-line PR: all four agents in parallel, ~$0.20, ~70s wall clock. PRs over ~1,500 changed lines are rejected by design. Public repos only; read-only; nothing is posted to GitHub.

## Development

```bash
uv run pytest       # 77 tests, no network or API calls (fixtures + stubs)
uv run ruff check .
```
