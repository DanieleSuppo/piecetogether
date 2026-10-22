# Focused Jev adoption procedure

Use this procedure before enabling Jev for one deployment. The deterministic
reference remains the baseline. Captured-response CI tests validate the adapter
contract, not the model's semantic quality; no live comparison was performed in
the #16 delivery.

## Provider contract and verification limits

Primary sources: [HTTP API](https://docs.typesafe.ai/api),
[Models](https://docs.typesafe.ai/models), [State](https://docs.typesafe.ai/concepts/state),
[Confidence](https://docs.typesafe.ai/confidence),
[account rate limits](https://console.typesafe.ai/rate-limits),
[billing agreement](https://typesafe.ai/legal/mca), and
[data handling](https://help.typesafe.ai/articles/5974925716-how-is-submitted-data-used-or-retained).
Public documentation is not authenticated verification of this deployment's account.

- `POST https://api.typesafe.ai/v1/systemone`, Bearer API key, pinned `jev-1.13.0`.
  The provider also advertises `jev-1.14.0`; this is not permission to upgrade or
  use a moving alias. Preserve the existing pin and `context-relevance-v1` rubric.
- Text/JSON only; no raw Artifact or DB access. Provider sessions are not Core
  memory. This does not mean the provider retains no requests or telemetry;
  no-training statements do not establish zero logging or a deletion deadline.
  Verify applicable account/privacy terms before sharing deployment data.
- Noul returns probability 0–1. The adapter asks independent candidate-relevance
  questions on the same state, preserving multi-Context turns.
- Provider limits: 64,000 total input tokens; 32,000 for state plus longest
  question. The adapter uses UTF-8 byte guards before sending. Those local
  request-size guards are not a verified billable-token or cash ceiling.
- Choice allows at most 255 options; Score allows up to ten ordered levels.
  Context relevance does not use forced Choice. Future reference-resolution
  work must explicitly include none/new/ambiguous and charge composed dependent
  questions to its budget; that work belongs to the later owning tickets.
- Throughput limits are organization-specific; verify them in the account
  console. Historical indicative rates are not current account evidence, and
  rate limits are not spending limits. Failed calls use bounded local fallback,
  not unbounded HTTP retries. Socket operations and reads use the remaining
  deadline; elapsed-call exhaustion produces an explicit budget outcome.
- Advertised input price: $0.042/million tokens; output free. Applicable account
  rates, billable-input accounting, rounding/minimums and failed-call charges
  remain unverified. Public rate arithmetic is nominal, not a guaranteed invoice
  ceiling. Metadata does not guarantee calibrated correctness or latency.

## #32 live-validation boundary

These gates precede every live invocation in the comparison below. Credentials
alone are insufficient; do not run the paid steps while any gate is unresolved.

Typing and controlled-local retention/erasure were delivered separately; they do
not establish Jev adoption. No authenticated Jev request was dispatched by this
maintenance work. That is an execution-history statement, not an account-billing
reconciliation: account charges were not inspected and no experiment reservation
ledger exists. Semantic-quality, measured latency and actual account-charge
evidence remain absent. The default stays `deterministic`; synthetic replay
success is not deployment representativeness.

The authorized ceiling is **US$5 total**, not per run, process, case or provider.
Before any live invocation:

1. Configure an existing secret reference privately, such as the environment name
   `PT_TYPESAFE_API_KEY`. Never put its value in an issue, report or chat.
2. Obtain credible account/provider evidence for a conservative maximum charge
   per dispatched attempt, including errors, timeouts and unknown usage. Confirm
   applicable rates and top-up/overdraft behavior. No credit purchases, top-ups or
   account changes are authorized. If a credible bound cannot be established,
   do not dispatch or claim an enforceable US$5 cash ceiling.
3. Provide a reviewed, durable cumulative reservation/receipt around the narrow
   experiment, using exact decimal or integer money arithmetic. Before dispatch,
   require reconciled spend plus all outstanding reservations plus the new
   attempt's conservative bound to be at most US$5. Include every billable
   selector, generative and fallback attempt. Refuse dispatch if the remainder
   cannot cover the whole bound; a smaller leftover amount is not spendable.
   Retain the full reservation for an unknown charge and stop all further paid
   attempts until owner billing evidence reconciles it. Never assume a refund
   or release an unresolved reservation. Existing per-request calls/tokens/
   deadline guards do **not** implement this experiment-wide control; it is not
   currently provided by the adapter or capture/replay helpers. Never reset an
   unresolved budget by restarting or choosing another output directory.
4. Freeze independent required/irrelevant IDs and expected Core outcomes before
   scores; separate tuning and held-out cases. Begin with synthetic fixtures
   only. Record sample counts, missing-catalogue records, false accepts, fallback
   causes and all billable work. Do not label unmeasured metrics as zero or turn
   tiny-sample p95 into a production SLA.
5. Stop the experiment on authentication, quota, rate-limit, model or unresolved
   billing failure. Core's deterministic fallback does not authorize another paid
   attempt or a provider/model switch.

Existing selector validation, independent fixture assertions and request-bound
capture/replay are the reuse points; no separate benchmark or billing service is
needed. Close the narrow experiment-control gaps after account facts are known,
then review the actual billing/privacy boundary before a live command. No live
runner or billing controller is claimed implemented by this document.

Enable only a deployment-owned canary after all pre-dispatch gates and task-specific
quality, privacy, operational benefit and representative held-out evidence pass.
Roll back through static selector configuration and restart with `deterministic`,
without deleting trusted history or weakening validation. Issue #32 remains open
while live gates or representative adoption evidence are missing.

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
4. Only after every [live-validation gate](#32-live-validation-boundary) above is
   satisfied, run Jev with deployment-approved credentials, permitted text, the
   pinned model and `context-relevance-v1` rubric. Save responses, usage and
   fallback evidence. `piecetogether.evaluation.CapturedSelector` consumes ordered,
   request-bound selector evidence through the same validation and bounded fallback
   path. Use explicit `capture(fixture_core, scenario)` and `replay(case)` fixtures
   to recompute downstream Packs, Grounding, commits and events without inference.
   Recorded timing preserves budget exhaustion. Missing/redacted evidence or
   incompatible policy/question/revision evidence raises `ReplayUnavailable`;
   optional diagnostics are not required. See [operator replay](evaluation-replay.md)
   for sensitive-fixture handling and the concrete interface.
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
   existing evaluation notes. Enable Jev only after the live-validation gates
   above and deployment-specific quality, privacy and operational benefit pass.
   Keep the reference adapter otherwise. Re-evaluate
   when model, rubric, Contract, language mix or budget changes.

Missing live credentials do not block deterministic operation or CI. They leave
the deployment's adoption decision pending evidence.

## #31 audit decision

The [Core scenario coverage audit](core-scenario-coverage.md) adds deterministic
captured-decision proofs, not measured deployment results. There is still no
measured required-record recall, wrong-selection/false-accept rate, fallback
rate, end-to-end latency or total-cost evidence. Jev remains unenabled; the
deterministic selector remains the default. #17 uses captured optional decision
metadata through ordinary proposals, not a separate live per-item Jev adapter;
#27 does not adopt Jev for Artifact perception/classification. Enabling any path
still requires the independent task-specific evidence above.
