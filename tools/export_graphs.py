"""Export compiled workflow topology offline; never construct production dependencies."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from langgraph.checkpoint.memory import InMemorySaver
from services.agent.llm import ScriptedLLM
from services.agent.store import InMemoryStore
from services.agent.w1.graph import compile_w1
from services.agent.w2.graph import compile_w2
from services.agent.w2.tools import InMemoryRecordsTools
from services.records.embed import HashEmbedder
from services.records.retrieve import InMemoryRetriever

HEADER = "Generated from the compiled graph by tools/export_graphs.py — do not hand-edit"


def export(output_dir: Path) -> None:
    graphs = {
        "w1": compile_w1(ScriptedLLM({}), InMemoryStore.demo(), InMemorySaver()),
        "w2": compile_w2(ScriptedLLM({}), InMemoryRecordsTools(
            InMemoryRetriever([], HashEmbedder())), InMemorySaver()),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, graph in graphs.items():
        mermaid = graph.get_graph().draw_mermaid()
        (output_dir / f"{name}-graph.md").write_text(
            f"# {HEADER}\n\n```mermaid\n{mermaid}\n```\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "docs" / "diagrams")
    export(parser.parse_args().output_dir)
