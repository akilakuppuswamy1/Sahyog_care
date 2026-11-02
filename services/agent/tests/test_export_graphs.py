"""The documentation exporter runs offline and preserves compiled topology."""
from pathlib import Path
import subprocess
import sys


def test_export_compiled_graphs(tmp_path):
    root = Path(__file__).resolve().parents[3]
    subprocess.run([sys.executable, str(root / "tools/export_graphs.py"),
                    "--output-dir", str(tmp_path)], cwd=tmp_path, check=True,
                   capture_output=True, text=True)
    expected = {
        "w1": {"intake", "detect_language", "red_flag_rules", "model_checks", "redirect_records",
               "er_handoff", "clarify", "front_desk", "shortlist", "present_and_pick", "book", "notify"},
        "w2": {"scope_guard", "doctor_check", "retrieve", "widen", "not_found", "answer", "verify"},
    }
    for name, nodes in expected.items():
        content = (tmp_path / f"{name}-graph.md").read_text(encoding="utf-8")
        assert content.startswith("# Generated from the compiled graph by tools/export_graphs.py — do not hand-edit")
        assert "```mermaid" in content
        assert all(node in content for node in nodes)
