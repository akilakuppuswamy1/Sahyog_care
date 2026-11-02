# ADR-003: W1 is a LangGraph graph — deterministic red-flag rules first, an interrupt for the slot pick

```
title: Build W1 as a compiled LangGraph graph with a Postgres checkpointer, rules-before-model red flags, and interrupt() for the patient's choice.
context: The CMO's fear is AI advice harming a patient (p.7). Red-flag recall must be 100% with no exceptions (p.16).
  The patient's slot choice is a pause of unknown length, and the API must stay stateless behind a balancer
  (p.5, p.29, p.36). Confidence under 0.6 gets one clarifying question; two low turns go to the front desk (p.14).
options:
  1. A chain of calls: classify, then list doctors, then book. Simple, but it has no durable pause for the patient's
     pick, no replay, and the red-flag ordering is a convention rather than a structure.
  2. A free agent loop (ReAct) with tools. Flexible, but the model chooses the path, so it could reach booking
     without the red-flag check. Unacceptable for a 100% recall target.
  3. An explicit graph: rules node -> model second net (may only add flags) -> schema-constrained specialty
     match -> tool shortlist -> interrupt() -> book. A Postgres checkpointer holds state across the pause.
decision: Option 3. The graph is drawn in docs/architecture.md §3.
  - Red flags: deterministic rules in code, versioned in the repo, run before any model call. The model's net is
    merged by set union, so it can add flags and can never remove one.
  - Specialty output must be one of the 38 ids (pydantic); an invalid id counts as low confidence.
  - The patient pick is interrupt() and resume with Command(resume=slot_id). The checkpointer is keyed by
    thread_id, so any replica can resume (p.36 load balancing).
  - Tools take patient_id from graph state (set from the auth context), never from model output (p.36).
  Trigger to revisit: if the second net adds more than 10% false red flags on the eval set (patients sent to
  ED unnecessarily), the clinical safety lead reviews whether the net is removed, never whether rules are relaxed.
consequences:
  - Red-flag recall is a property of the rule set plus the edge order, testable without a model.
  - The rule list depends on D2 Q1 (the chest-pain-at-night answer). Until it is answered, rules are drafted from
    the eval set and marked unreviewed.
  - Checkpoints for every W1 run are retained 30 days (about 150-300 MB a day, docs/d5-sizing.md), so a purge job is required.
versions_pinned: langgraph==1.2.16, langgraph-checkpoint-postgres==3.2.0 (pack p.39, checked by the pack 2026-10-21; re-check before install)
checked_on: 2026-11-02
status: proposed
```
