# ADR-001: Hybrid deployment — identified data in-region, only de-identified text crosses the boundary

```
title: Hybrid deployment. Identified data is stored and processed in an India region; only scrubbed text reaches a public LLM API.
context: Two constraints pull against each other. Identified health data never leaves India (deck p.4, RFP addendum p.5).
  The client also wants current models and a swappable vendor (p.4). Peak load is 9,000 appointments a day and
  1,400 requests an hour. DPDP consent-manager rules apply from 12 Nov 2026 and substantive obligations from
  12 May 2027 (p.55; confirm with counsel).
options:
  1. On-premise only, with local models. Strongest residency and audit. Weakest model quality, the slowest start
     (procurement), and the hospital runs every GPU. Cost: high fixed.
  2. Public cloud + public LLM, sending everything. Fastest to build and best models, but identified data reaches
     the vendor, which breaks the addendum on day one. Rejected outright.
  3. Hybrid. All stores, services, speech-to-text and the token map run in an India-region private cloud
     (or on-premise). Model calls go through one egress adapter that scrubs identifiers (Presidio 2.2.364 plus
     our own recognisers for Indian phone numbers and ABHA ids), generalises quasi-identifiers (age band,
     district), and re-identifies the answer in-region. Cost: two estates to run; variable cost on LLM calls only.
decision: Option 3, hybrid.
  Trigger to revisit:
  (a) A06 fails, meaning de-identified transcripts give clearly worse SOAP drafts in the 30-transcript
      comparison. Then W3 drafting moves to an in-region hosted model and W1/W2 stay hybrid.
  (b) The vendor offers an India-region endpoint with zero-retention terms (D3 A07). Then the scrubber stays
      as defence in depth, but the residency risk is retired.
  (c) The egress replay of 200 payloads finds any identifier. Then egress stops until the recogniser is fixed.
consequences:
  - The scrubber is now safety-critical code with its own eval: 200-payload replay, zero hits (D7, O5).
  - Every model call pays scrub and re-identify latency (budget about 50 ms per call; measured on day 7).
  - Two places to operate. Mitigated by keeping the LLM side stateless: no data stored outside.
  - The vendor swap is one adapter change, so the CIO's lock-in fear (p.7) is answered by design.
versions_pinned: presidio-analyzer==2.2.364 (pack p.39, checked by the pack on 2026-10-21; re-check on install)
checked_on: 2026-11-02
status: proposed
```
