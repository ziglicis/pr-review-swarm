# pr-review-swarm

A multi-agent system that reviews GitHub pull requests the way an engineering team would: specialized reviewers (correctness, security, style, test coverage) examine the change in parallel, and a synthesizer merges their findings into one prioritized review.

**Status:** under construction — Phase 1 (scaffolding + single agent).

```bash
uv sync
uv run python -m src.cli review <github-pr-url>   # requires ANTHROPIC_API_KEY
```
