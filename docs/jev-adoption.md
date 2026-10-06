# Focused Jev adoption procedure

Use this procedure before enabling Jev for one deployment. The deterministic
reference remains the baseline. Captured-response CI tests validate the adapter
contract, not the model's semantic quality; no live comparison was performed in
the #16 delivery.

## Verified provider contract (2026-10-06)

Primary sources: [HTTP API](https://docs.typesafe.ai/api),
[Models](https://docs.typesafe.ai/models), [State](https://docs.typesafe.ai/concepts/state),
and [Confidence](https://docs.typesafe.ai/confidence).

- `POST https://api.typesafe.ai/v1/systemone`, Bearer API key, pinned `jev-1.13.0`.
- Text/JSON only; no raw Artifact, local inference, provider memory or DB access.
- Noul returns probability 0–1. The adapter asks independent candidate-relevance
  questions on the same state, preserving multi-Context turns.
- Provider limits: 64,000 total input tokens; 32,000 for state plus longest
  question. The adapter uses conservative UTF-8 byte upper bounds before sending.
- Choice allows at most 255 options; Score allows up to ten ordered levels.
  Context relevance does not use forced Choice. Future reference-resolution
  work must explicitly include none/new/ambiguous and charge composed dependent
  questions to its budget; that work belongs to the later owning tickets.
- Published indicative limits are 100k tokens/sec and 80 requests/sec; they may
  change. 429/529 and other failed calls trigger bounded local fallback rather
  than unbounded HTTP retries. Socket operations and reads use the remaining
  deadline; elapsed-call exhaustion produces an explicit budget outcome.
- Published input price: $0.042/million tokens; output free. Reverify prices and
  account limits before a live comparison. Metadata is evidence, not a guarantee
  of calibrated correctness or latency.

## Comparison on existing domain scenarios

1. Use the consumer's existing domain fixtures and approved expected state
   transitions. Start each adapter run from the same initial ledger, Contract,
   Communications, Actor mappings and budget. Identify necessary record IDs and
   allowed/forbidden disclosure independently of model scores.
2. Include Italian examples (`cucina e bagno`, `opzione favorita`), ambiguous
   references (`quello di prima`), multiple subjects in one turn, older pending
   Grounding, and synonymous concepts (`preferenza` / `opzione favorita`). Include
   unrelated records, same-Actor private notes, other Actors' records, and a
   confident but wrong candidate. Extend the consumer's existing fixture set;
   no separate benchmark runner is required.
3. Run the deterministic baseline and inspect each `Core.inspect(id)` trace.
   Preserve selected/catalogue IDs, captured content and scope revisions,
   mandatory items, truncation, retrieval steps and final semantic outcomes.
4. With deployment-approved credentials and permitted text, run Jev with the
   pinned model and `context-relevance-v1` rubric. Save responses, usage and
   fallback evidence. Captured responses can implement `SemanticSelector` for
   subsequent offline replay; replay must preserve IDs/revisions and never make
   another inference call.
5. Evaluate candidate operating points on a tuning subset, then validate the
   chosen point on separate scenarios. No universal confidence threshold is
   assumed. Measure:
   - **required-record recall:** required selected / required available, with
     missing-from-catalogue records reported separately;
   - **erroneous selection:** irrelevant selected / all selected, and counts;
   - **fallback rate:** fallback turns / selector turns, broken down by cause;
   - **end-to-end latency:** ingress-to-outcome median and p95, including selector,
     generation, retrieval, retries and fallback;
   - **total cost:** all billable input across calls/retries and generative work,
     including fallback overhead. Failed calls with unknown usage are explicitly
     reported as unknown, never counted as zero.
6. Verify disclosure, revision and trusted-state invariants remain satisfied at
   every operating point. Neither a high probability nor adoption metrics may
   override them. The default policy withholds internal-only content from the
   generative model; record that policy when assessing useful context recall.
7. Record the deployment, fixture revision, model/rubric version, budgets, chosen
   operating point, measurements and adoption decision in the deployment's
   existing evaluation notes. Enable Jev only if domain quality and operational
   benefit are demonstrated. Keep the reference adapter otherwise. Re-evaluate
   when model, rubric, Contract, language mix or budget changes.

Missing live credentials do not block deterministic operation or CI. They leave
the deployment's adoption decision pending evidence.
