"""One-off, idempotent: add step-4 lines to files you already have, without overwriting your edits.
Run from the repo root:  python tools/apply_step4.py
"""
from pathlib import Path


def add_lines(path: str, marker: str, lines: str) -> None:
    p = Path(path)
    s = p.read_text(encoding="utf-8")
    if marker in s:
        print(f"{path}: already up to date")
        return
    p.write_text(s.rstrip("\n") + "\n" + lines.lstrip("\n"), encoding="utf-8")
    print(f"{path}: updated")


add_lines("requirements.txt", "langgraph", """
langgraph>=1.0,<2                      # pack p.39 pins 1.2.16; we pin after our own install (record the version)
langgraph-checkpoint-postgres>=3.0,<4  # pack p.39 pins 3.2.0
python-dotenv>=1.0                     # reads .env
""")

ini = Path("pytest.ini")
s = ini.read_text(encoding="utf-8")
if "services/agent/tests" not in s:
    ini.write_text(s.replace("testpaths = ", "testpaths = services/agent/tests "), encoding="utf-8")
    print("pytest.ini: updated")
else:
    print("pytest.ini: already up to date")

add_lines("README.md", "## W1 Find my doctor", """
## W1 Find my doctor (step 4)
```powershell
copy .env.example .env        # then fill OPENROUTER_API_KEY, OPENROUTER_MODEL, DATABASE_URL (your port)
pip install -r requirements.txt
pytest                                    # node tests + graph tests (scripted model, no network, no cost)
python -m evals.run_w1 --rules-only       # red-flag recall, free
python -m evals.run_w1                    # full eval through OpenRouter -> evals/runs/w1-*.md
python -m services.agent.w1.cli           # talk to it; pauses for the slot pick; --resume <thread> after a restart
```
""")

add_lines("AI-COLLABORATION.md", "PR #4", """
### PR #4 — W1 graph (days 6-7) — 2026-11-02
agent: Claude (Opus 5.5), chat session (no access to the pod machine; delivered as a zip)
proposed:
- Red-flag rules in code with English + Hinglish patterns; the model net may only add flags
- Egress guard (regex scrub, refuse-to-send) and an output guard against diagnosis or dose wording
- OpenRouter client behind one interface (vendor swap = one file)
- W1 nodes as plain functions, wired into a LangGraph graph with two interrupts (clarify, pick)
- Postgres and in-memory tools; a CLI demo; the eval runner
accepted: _pod to fill_
rejected: _pod to fill_
why: _pod to fill_
notes:
- **Overfitting caught:** the first rule set scored 30/30 on the eval set, then missed 3 of 12 held-out paraphrases
  ("tightness in my chest", "cannot breathe", "face suddenly drooped"). Fixed; the held-out set is now its own test.
- **Framework trap avoided:** LangGraph injects its own object into node parameters named `store`, so the tools
  parameter was renamed `tools`. Worth confirming on the installed version.
- **Not run by the agent:** the LangGraph graph tests and the OpenRouter eval. PyPI was blocked in the agent's
  sandbox, so only the framework-free logic (rules, guards, nodes, scoring) was tested there. First run on the pod machine is the real test.
- **Withheld from the reply:** the pack's "a nurse will call within two minutes" (p.14), until D2 Q1 / D3 A09 confirm staffing.
- OpenRouter model chosen: _fill in model id + date_
""")
