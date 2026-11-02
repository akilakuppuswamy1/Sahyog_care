# Generated from the compiled graph by tools/export_graphs.py — do not hand-edit

```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
	__start__([<p>__start__</p>]):::first
	scope_guard(scope_guard)
	doctor_check(doctor_check)
	retrieve(retrieve)
	widen(widen)
	not_found(not_found)
	answer(answer)
	verify(verify)
	__end__([<p>__end__</p>]):::last
	__start__ --> scope_guard;
	answer -. &nbsp;end&nbsp; .-> __end__;
	answer -.-> verify;
	doctor_check -. &nbsp;stop&nbsp; .-> __end__;
	doctor_check -. &nbsp;go&nbsp; .-> retrieve;
	retrieve -.-> answer;
	retrieve -.-> not_found;
	retrieve -.-> widen;
	scope_guard -. &nbsp;stop&nbsp; .-> __end__;
	scope_guard -. &nbsp;go&nbsp; .-> doctor_check;
	widen --> retrieve;
	not_found --> __end__;
	verify --> __end__;
	classDef default fill:#f2f0ff,line-height:1.2
	classDef first fill-opacity:0
	classDef last fill:#bfb6fc

```
