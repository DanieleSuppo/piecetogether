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
reconciliation: account charges were not inspected. The guarded synthetic smoke
below now supplies a create-once reservation, but implementation and offline tests
do not create the owner's reservation or establish any account charges.
Semantic-quality, measured latency and actual account-charge
evidence remain absent. The default stays `deterministic`; synthetic replay
success is not deployment representativeness.

The authorized ceiling is **US$5 total**, not per run, process, case or provider.
Before any live invocation:

1. Review the actual billing/privacy/trust-boundary diff before credential delivery
   or a separately approved paid invocation. Configure an existing secret reference
   privately, such as the environment name `PT_TYPESAFE_API_KEY`. Never put its
   value in an issue, report or chat.
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
   provided by the adapter or capture/replay helpers alone. The narrow wrapper
   below provides one full, permanent reservation, not a reusable budget. Never reset an
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
then review the actual billing/privacy boundary before a live command. The bounded
synthetic command below is not a general billing controller or benchmark framework.

Enable only a deployment-owned canary after all pre-dispatch gates and task-specific
quality, privacy, operational benefit and representative held-out evidence pass.
Roll back through static selector configuration and restart with `deterministic`,
without deleting trusted history or weakening validation. Issue #32 remains open
while live gates or representative adoption evidence are missing.

## Owner-confirmed, one-shot synthetic compatibility smoke

The owner **OWNER-CONFIRMED** that the existing prepaid account has at most US$5
remaining, no auto-refill, and service halts at credit exhaustion. This is not
independently verified provider/account evidence or a general invoice guarantee.
For that matching owner-limited account only, this supports one worst-case US$5
attempt. No purchase, top-up, account change or actual paid invocation belongs to
this implementation unit. A key for another account does not qualify.

Offline preflight (safe with or without credentials):

```sh
PYTHONPATH=tests python3 -m jev_guarded_smoke
```

Import, unittest discovery (collection/import, not execution of the collected
tests) and default preflight do not resolve secrets, load a real configuration or
`.env`, create a reservation or call HTTP. Ordinary Core operation still defaults
to the deterministic selector. Default preflight exits 0 to mean only that offline
preflight completed, **not that live compatibility passed**. Check its JSON
`status=offline preflight only` and `live_authorized=false`; a live result requires
the explicit live flags and its private receipt, not merely exit status 0.

After independent actual-diff review, secure credential delivery and **separate
approval of the paid invocation**, the command supports `--live --output
<new-private-directory> --ack-owner-prepaid-usd5`. The acknowledgement explicitly
confirms that `PT_TYPESAFE_API_KEY` belongs to the matching owner-limited account
and that the stated prepaid controls apply. Live mode resolves only the process
environment mapping `typesafe -> PT_TYPESAFE_API_KEY`; it never loads a deployment
configuration or env file. Do not invoke live mode as part of offline validation.

`piecetogether.jev_smoke.reserve_once() -> None` reserves **all 500 integer cents**
immediately before `GuardedJevSelector.select(request)` delegates once to the
existing pinned `JevSelector`. The OS account home from
`pwd.getpwuid(os.getuid()).pw_dir`, not `HOME`, XDG, cwd, output path or a run ID,
fixes the ledger identity at
`.local/state/piecetogether/jev-usd5-smoke/reservation.json`. No public ledger-path
override exists. Descriptor-relative no-follow traversal validates node ownership,
type, permissions and binding. Missing account-home ancestry, including `.local`
and `.local/state`, is created 0700; existing directories are validated, never
chmodded. Dedicated directories are 0700, the exclusively created record is 0600
with one hard link, and file plus ancestry are fsynced before delegation. Unsafe
existing output is refused before reservation. Failed output preparation can
leave an empty private output directory; that path still refuses reuse, with no
automatic deletion. Any
existing reservation, including a partial/corrupt one, refuses another dispatch.
A partial write or fsync failure never permits dispatch or automatic cleanup.

The reservation records `limit_cents=500`, `reserved_cents=500`,
`reconciled_cents=0` and `attempted_dispatches=1`. This counts conservative selector
delegation, including a local pre-HTTP rejection; it is **not an actual HTTP count**.
Core's `calls_used` can include deterministic fallback and is not an HTTP counter.
The adapter sends at most one HTTP request, without retries. Success, failure,
timeout, unknown usage, process death and artifact-write failure **all retain the
full hold**. Usage or public-price estimates never release it or settle an actual
charge. No reset, force flag, reconciliation or second-attempt path exists. No
further paid attempt is allowed until separately reviewed future owner billing
reconciliation. Owner deletion, filesystem rollback and another host can bypass
local state; this is not an adversarial-owner or distributed guarantee.

The command seeds only the existing offline cucina/bagno fixtures. It freezes both
Context IDs and unchanged trusted history/view/events before any scores, uses
`cucina e bagno` with `minimum_relevance=0.7`, `jev-1.13.0` and
`context-relevance-v1`, then captures one accept with deterministic zero-operation
generation and a local channel. It sends no Artifact bytes and uses no other
third-party model. A completed Core fallback is still a failed smoke. Success
requires exactly both independently required Contexts, no extra IDs/disclosure,
no new Grounding Items and unchanged trusted state. Existing capture/replay
recomputes the outcome without inference, with provider/model/network tripwires;
the same independent expectations must hold on `ReplayResult.actual`, not merely
`matched`.

The new private output directory holds 0600 `capture.json` (existing `ReplayCase`
serialization, including a synthetic initial-state snapshot) and `receipt.json`.
Raw error payloads/exception messages are unavailable: the guard passes fixed
sanitized failure classifications to Core before trace capture, rejecting
credential/header echoes. Check-only normalization casefolds text and performs two
fixed passes of percent and JSON-unicode escape decoding, with header-whitespace
checks; safe results retain their exact original values. This is bounded defense
in depth, not a demonstrated schema-valid echo exploit of pinned `jev-1.13.0` or
a general secret/DLP service. The strict Jev response schema remains unchanged.
Never publish captures as ordinary logs. Artifact-write
failure reports only a fixed error, keeps any reservation, and can leave missing
or partial artifacts; it does not authorize another attempt.

Receipts separate `cap_evidence=OWNER-CONFIRMED` and
`provider_account_independently_verified=false`, full reserved cents,
`actual_cost=UNRECONCILED`, observed validated usage and **nominal** public-rate
arithmetic (`$0.042/M` input). Unknown usage is null, not zero; no actual-charge
claim follows from a valid response. A refused reservation reports
`reserved_cents=null` and `selector_failure=reservation_refused`, never a zero hold
that might imply a prior/partial reservation was released. The additional closed
`reservation_refusal_reason` is `already_reserved` for any existing record (even
empty/corrupt), `unsafe_ancestry` for failed path/permission checks before record
creation, or `durability_failed` for uncertain creation/write/fsync/final-validation
failures. Unrecognized diagnostics become the fixed `reservation_refused` fallback.
`ReservationRefused` still prints only `reservation refused`; no raw error, path or
exception text enters a receipt. These reasons do not prove that no prior hold
exists, authorize a retry, or provide a reset/release path. They include fixture/policy/model versions,
required/selected/missing counts, fallback, Core calls, conservative delegation
count and timing. Scope is **sample_count=1 synthetic compatibility**, not a
held-out evaluation, p95/SLA, deployment quality result or production GO. Keep all
representative adoption and privacy/billing gates above and below unchanged.

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
