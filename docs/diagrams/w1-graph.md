# Generated from the compiled graph by tools/export_graphs.py — do not hand-edit

```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
	__start__([<p>__start__</p>]):::first
	redirect_records(redirect_records)
	intake(intake)
	detect_language(detect_language)
	red_flag_rules(red_flag_rules)
	model_checks(model_checks)
	er_handoff(er_handoff)
	clarify(clarify)
	front_desk(front_desk)
	shortlist(shortlist)
	present_and_pick(present_and_pick)
	book(book)
	notify(notify)
	__end__([<p>__end__</p>]):::last
	__start__ --> intake;
	book -.-> front_desk;
	book -.-> notify;
	book -.-> shortlist;
	clarify --> intake;
	detect_language --> red_flag_rules;
	intake --> detect_language;
	model_checks -.-> clarify;
	model_checks -. &nbsp;handoff&nbsp; .-> er_handoff;
	model_checks -.-> front_desk;
	model_checks -. &nbsp;book&nbsp; .-> shortlist;
	present_and_pick --> book;
	red_flag_rules -. &nbsp;handoff&nbsp; .-> er_handoff;
	red_flag_rules -. &nbsp;continue&nbsp; .-> model_checks;
	red_flag_rules -.-> redirect_records;
	shortlist -.-> front_desk;
	shortlist -. &nbsp;present&nbsp; .-> present_and_pick;
	er_handoff --> __end__;
	front_desk --> __end__;
	notify --> __end__;
	redirect_records --> __end__;
	classDef default fill:#f2f0ff,line-height:1.2
	classDef first fill-opacity:0
	classDef last fill:#bfb6fc

```
