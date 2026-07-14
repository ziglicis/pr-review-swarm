import json
from types import SimpleNamespace

from src.agents.base import run_with_tools
from src.trace import Tracer, clip


def test_clip():
    assert clip(None) is None
    assert clip("short") == "short"
    long = "x" * 25_000
    clipped = clip(long)
    assert len(clipped) < 25_000
    assert "clipped, 25000 chars total" in clipped


def test_write_round_trips(tmp_path):
    tracer = Tracer()
    tracer.event("plan", classification="code")
    tracer.event("agent_start", agent="correctness")
    path = tracer.write(tmp_path / "sub" / "trace.json")  # parent dir auto-created
    data = json.loads(path.read_text())
    assert data["created"]
    assert [e["kind"] for e in data["events"]] == ["plan", "agent_start"]
    assert data["events"][1]["agent"] == "correctness"
    assert data["events"][0]["t"] <= data["events"][1]["t"]


class OneShotClient:
    def __init__(self):
        self.messages = SimpleNamespace(create=self._create)

    async def _create(self, **kwargs):
        block = SimpleNamespace(
            type="tool_use", name="report_findings", id="t1", input={"findings": []}
        )
        return SimpleNamespace(
            content=[block], usage=SimpleNamespace(input_tokens=100, output_tokens=10)
        )


async def test_agent_run_records_lifecycle_events():
    tracer = Tracer()
    await run_with_tools(
        "correctness", "sys prompt", "the context", {"a.py"},
        client=OneShotClient(), tracer=tracer,
    )
    kinds = [e["kind"] for e in tracer.events]
    assert kinds == ["agent_start", "model_call", "agent_end"]
    start = tracer.events[0]
    assert start["system"] == "sys prompt" and start["context"] == "the context"
    call = tracer.events[1]
    assert call["tokens_in"] == 100
    assert call["blocks"][0] == {"type": "tool_use", "name": "report_findings",
                                 "input": {"findings": []}}
    end = tracer.events[2]
    assert end["status"] == "ok" and end["findings"] == 0
