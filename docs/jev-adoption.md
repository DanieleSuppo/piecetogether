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
  use a moving alias. Preserve the existing pin and default `context-relevance-v1`
  rubric. The explicit `context-relevance-v2` diagnostic opt-in below does not
  change deployment defaults or the shared Core selection policy.
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

## Issue #32 closeout: delivered Core work, optional rollout deferred

The operator requested completing #32 after separating optional deployment adoption
from Core MVP delivery. Typing, non-terminal retention, controlled-local erasure,
versioned Jev integration, bounded diagnostics, reviews and verified delivery are
complete. The still-current source proof is 542 tests passing and strict mypy
clean on 20 files; no unchanged gate is rerun for this documentation-only closeout.

Production Jev adoption is **not completed or enabled**. Its outstanding
consumer/deployment-specific acceptance and actual-billing reconciliation are
tracked in **#33**, an optional `needs-info` follow-up, not a blocker for #12.
The missing measurements below remain missing; splitting the issue does not turn
four synthetic exact selections and one deferred case into production acceptance.

Default remains deterministic. No new paid request, deployment change, credit,
ledger reset/release/deletion or retry is authorized by this closeout. Preserve the
original local 500-cent hold and all eleven recorded attempts. Historical
experiment results and their limitations below remain unchanged.

## #32 live-validation boundary

These gates precede every live invocation in the comparison below. Credentials
alone are insufficient; do not run the paid steps while any gate is unresolved.

Typing and controlled-local retention/erasure were delivered separately; they do
not establish Jev adoption. The owner-run synthetic smoke received a valid
`jev-1.13.0` response, then failed its independent selection expectations:
The adapter returned application `abstained` at 0.7 and Core used deterministic fallback.
This was not a native Jev abstention: the provider returned Noul P(yes) values. The two required
Contexts scored 0.39 and 0.44; unrelated records scored up to 0.45. An offline
sweep found no threshold selecting both Contexts without at least three extra
records. Matching replay reproduces that failure; it does not certify quality.
Observed usage was 4529 input / 759 output tokens. Public-rate arithmetic gives
nominal US$0.000190218, not a reconciled charge. Actual account charges remain
unverified, and the full local 500-cent reservation stays intact. The default stays `deterministic`; synthetic replay
success is not deployment representativeness.

The authorized ceiling is **US$5 total**, not per run, process, case or provider.
Before any live invocation, apply the gates below. The explicitly authorized
[prepaid catalogue diagnostic exception](#prepaid-catalogue-diagnostic-exception)
substitutes an aggregate wallet ceiling for gates 2–3 only within that fixed
experiment. It does not relax production-adoption requirements.

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
without deleting trusted history or weakening validation. Optional deployment
adoption remains open in #33 while these gates or adoption evidence are missing;
Core maintenance delivery under #32 is a separate closeout decision.

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

An additional, optional live-only `--ack-trusted-primary-group` explicitly trusts
this OS account's primary group like the owner for nonprivate ancestry. Strict
mode remains the default: group-writable ancestry, including 0775, still refuses
without this option. The opt-in permits group-write only when the node belongs to
the OS uid and its gid matches `pwd.getpwuid(os.getuid()).pw_gid`, not an effective
or supplementary group or an environment value. Other-write and foreign ownership
or group-write by another gid remain refused. The existing root-owned sticky
shared-ancestry exception is unchanged. Dedicated ledger/output directories still
require owned 0700, and reservation/artifact files still require owned, regular,
single-link 0600 nodes. Existing directories are never chmodded or chowned.

Opted-in primary-group members are **trusted like the owner**: they can rename or
delete writable ancestry and thereby invalidate local reservation state. No
resistance to malicious or accidental changes by those trusted members is claimed.
The option is a local trust acknowledgement, not payment approval, an account
verification or permission to reset the reservation. It does not replace
`--ack-owner-prepaid-usd5`, and cannot be supplied to default offline preflight.
Receipts record the chosen boolean `trust_primary_group` even on a refused live
attempt that produces a receipt. Both modes use exactly the same ledger and
experiment identity; changing the flag cannot mint a second attempt.

The Python APIs expose keyword-only `trust_primary_group: bool = False` on
`reserve_once`, `GuardedJevSelector` construction and private ancestry/output
helpers. Only literal `True` opts in; malformed truthy values retain strict
behavior and cannot grant trust. No Bootstrap, SelectionConfig or Proposal
permission is added.

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
further paid attempt through this one-shot command is allowed until separately
reviewed future owner billing reconciliation. The separately authorized prepaid
catalogue diagnostic exception below consumes the same unreleased envelope;
it neither resets this record nor authorizes another one-shot invocation. Owner deletion, filesystem rollback and another host can bypass
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

## Prepaid catalogue diagnostic exception

The operator explicitly authorized further tests using the local `.env` and
confirmed no off-credit, postpaid or card charges: service stops when the same
prepaid wallet is exhausted. Together with no auto-refill and remaining credit
at most US$5, this is an **OWNER-CONFIRMED aggregate cash bound** across the first
smoke and this one follow-up. It is not independent account/provider verification.
The local ledger limits attempts; it is not itself a provider cash ceiling.
The whole remaining authorization may be consumed. No purchase, refill, second
wallet or assumption of remaining funds is authorized. This completed v1 protocol
never authorizes a third batch; the separately authorized fixed v2 extension below
is a new protocol under the same original wallet, not a restart of this one.

This exception permits **at most five new synthetic POSTs**, after implementation
and independent actual-diff billing/privacy review. It does not implement or
clear the general credible-per-attempt-cost or billing-reconciliation gates.
All charges stay `UNRECONCILED`; public-rate arithmetic stays nominal. The
original `reservation.json` must remain intact and byte-equal to its fixed
500-cent record. Never recreate, delete, release or reset it.

One fixed supplemental, private, exclusive and fsynced batch claim must bind the
predeclared plan hash, prior recorded attempt 1 and maximum five new attempts.
It allocates **no additional funds**. Each new attempt needs its own immutable,
exclusive, fsynced marker before delegation. Existing, partial or corrupt claims
refuse a new run; crash may burn slots but never authorizes restart or retry.
Neither output paths, environment paths, trust modes nor run identifiers create
another envelope. Reports include the first attempt, distinguish invocation-local
counts from unknown prior counts, and never infer zero charges from missing data.

The existing original-ID/original-order capture is the control, not another paid
request. The five fixed variants are: original IDs with reversed catalogue;
readable IDs with original/reversed catalogue; fresh opaque IDs with
original/reversed catalogue. Relabel mappings preserve question-key wire order
by original record, and every provider request must differ from the original.
Model `jev-1.13.0`, `context-relevance-v1`, question text, summaries, expected
Context identities and minimum relevance 0.7 stay unchanged. These are
**post-hoc-informed catalogue/ID ablations, not held-out validation**. New IDs are
mapped back to the original synthetic identities for comparison. Do not feed
those counterfactual IDs into Core or claim new Grounding or replay success.

Freeze the full plan and analysis rule before the first dispatch. Report
score-to-content and score-to-position alignment separately; ordinary score
variation alone does not prove an ID-mapping defect. Clear positional tracking
ends the batch without adoption or more spending. Any authentication, quota,
rate-limit, model, transport or missing/invalid-usage error also ends it
immediately. No HTTP retry or provider/model switch is permitted. Application
abstention is diagnostic data, not permission for adaptive extra requests.

The new paid path may load only the explicitly authorized repository `.env`
credential into a short-lived process. Never print or persist its value or
headers. Import/default/offline tests must not load it, read a real reservation,
or call a provider. Use the existing secret-echo sanitation, strict Jev schema,
request byte/token/deadline bounds and private output helpers unchanged. Group
trust remains an explicit owner-approved primary-group option, not a permission
change. Keep deterministic production defaults and the original smoke assertions.

### Fixed diagnostic command and records

The existing owner authorization covers this fixed protocol after independent
actual-diff review; it does not authorize production adoption or another batch.
The original one-shot smoke command and its independent expectations are unchanged.

Offline preflight and plan compilation never resolve a credential, look up the
OS account, inspect the real ledger or dispatch a provider request:

```sh
PYTHONPATH=tests python3 -m jev_catalogue_probe
PYTHONPATH=tests python3 -m jev_catalogue_probe --capture PRIVATE_SYNTHETIC_CAPTURE
```

The second form emits the canonical plan, including `plan_hash`. Only the closed
15-record cucina/bagno profile qualifies: development/a1/s1, exact synthetic
summaries and Communication, no attachments/references/permitted-context records.
No snapshot is deserialized or copied. Candidate payloads must already be empty.
The plan binds the source-capture digest, canonical provider-body/request digests,
identity maps, two independent required Contexts, pinned versions and stop rule.
It freezes every mapping and request before reading control scores.

The paid form requires **all** of `--live --capture PRIVATE_SYNTHETIC_CAPTURE
--output NEW_PRIVATE_DIRECTORY --expected-plan-hash HASH
--ack-owner-prepaid-usd5 --ack-no-off-credit-charges`. An optional
`--ack-trusted-primary-group` keeps the existing trust semantics. A hash mismatch
refuses before child creation, credential access or claim. Live execution alone
starts a short-lived POSIX child; the existing Core dotenv loader reads the fixed
repository `.env` into a key-filtered mapping. Only `PT_TYPESAFE_API_KEY` is
selected; no deployment configuration or other dotenv variable is loaded into
that mapping. There is no credential, model, provider, environment-path or ledger
path CLI override. The key never crosses argv, pipes, receipts or console output.

`piecetogether.jev._payload(request, model_id)` owns the unchanged canonical
provider body. `FollowupJevSelector(config, api_key,
planned_requests, *, plan_hash, trust_primary_group=False,
ack_no_off_credit_charges=False)` requires exactly five distinct pinned requests
and literal-True financial acknowledgement. It shares the original pre-Core
schema/echo/usage boundary; it never invokes `reserve_once`. Construction is inert.
A lock serializes selection. Each call must match the next full request digest;
unplanned input, exhausted slots or any error makes that instance terminal.

The fixed ledger names are `catalogue-followup.json` and
`catalogue-attempt-1.json` through `catalogue-attempt-5.json`. The exclusive claim
binds the plan hash, original-record digest, prior recorded attempt 1, maximum five
new calls and all five request digests. It contains no new `reserved_cents`.
Each immutable marker binds its slot, plan and request. File and directory fsync
precede delegation, with no-follow, ownership, permissions, single-link and inode
binding checks. Every call revalidates the original record, claim and earlier
markers. Write/fsync uncertainty burns evidence and refuses restart; nothing is
automatically removed or repaired. The old one-shot still refuses its existing
reservation in either trust mode.

New private output exists before credential access or claim. `plan.json`,
`case-1.json` through `case-5.json` as reached, and `receipt.json` are exclusive
0600 artifacts. Batch deadline is 15 seconds; each frozen request uses at most
its captured timeout or 1000ms, whichever is smaller. Existing byte/token guards
remain. Any provider, transport, response, usage, budget or artifact error stops
further dispatch, without retry. A crash can leave missing/partial artifacts and
burnt markers, never permission to resume.

Receipts keep `reserved_cents=500` only after validating the original evidence;
otherwise it is null. They distinguish invocation-local durable markers and
selector delegations from actual HTTP count (unknown), original attempt 1 when
validated, and global counts. Existing/uncertain prior batch evidence gives
`global_recorded_attempts=null`, with conservative authorized upper bound 6,
never a misleading global `1 + 0`. Validated-response usage and nominal estimates
are explicitly partial evidence, never reconciled billing. `UNRECONCILED`,
`OWNER-CONFIRMED`, independent verification false and the full hold remain even
when every case passes. Abstention leaves accepted-selection metrics null.
A completed selection with no selected records has known zero counts and zero
required recall; false-accept rate remains unmeasured/null.

For original/reversed same-ID pairs, content-aligned and position-aligned score
vectors are reported separately. The fixed rule stops only when **all** position
differences are <=0.01 and **any** content difference is >=0.10, using exact decimal
comparisons. The headline `position_tracking_suspected` is true only on an
observed trigger and otherwise null/inconclusive. `stop_rule` distinguishes
`not_tested`, `not_triggered` and `triggered`; an unmet rule does not demonstrate
content grounding or exclude a positional effect. The historical control and
provider variability limit sensitivity. No conclusion is certified.
Other drift is descriptive. Counterfactual IDs never enter Core, and these
post-hoc-informed cases establish neither held-out quality nor p95/production GO.

## Observed catalogue diagnostic outcome (2026-10-23)

The reviewed five-request follow-up completed on the frozen plan
`68f38641db29c2741f904ba84b40303a922ab133d0ca04fc55db64c6f8614f2d`.
All five provider replies passed the pinned schema and usage checks. All five
produced adapter outcome `abstained`: no record reached the unchanged relevance
threshold 0.7. These are application routing decisions over native Noul P(yes),
not a native Jev abstention field.
There were no additional retries, alternate models or third batch.

| Input variant | Bagno Context score | Cucina Context score | Fewest extra records at full Context recall |
| --- | ---: | ---: | ---: |
| Original IDs, original order (earlier control) | 0.39 | 0.44 | 3 |
| Original IDs, reversed order | 0.36 | 0.34 | 6 |
| Readable IDs, original order | 0.45 | 0.52 | 2 |
| Readable IDs, reversed order | 0.40 | 0.38 | 2 |
| Fresh opaque IDs, original order | 0.41 | 0.45 | 3 |
| Fresh opaque IDs, reversed order | 0.39 | 0.37 | 2 |

The last column is an offline sweep of the observed scores, not a new operating
point or new inference. No single threshold selects exactly the two required
Contexts and zero extra records in any row. Changing order or making IDs readable
therefore did not resolve this fixture's fixed selection criterion. These data
do not isolate a parser defect, a unique prompt defect, or general model quality.

The positional stop rule was evaluated three times and never triggered. The
headline remains null/inconclusive: this does not exclude order/ID sensitivity or
demonstrate content grounding. The cases were informed by the initial failure;
they are not held-out or representative deployment validation.

New validated usage was 20,937 input / 2,941 output tokens. Including the earlier
control: 25,466 input / 3,700 output. At the advertised input-only rate, nominal
cost is US$0.000879354 for the follow-up and US$0.001069572 including the control.
**Actual charges remain UNRECONCILED**; these figures are not invoices or verified
remaining credit. All 500 cents stay reserved locally. Read-only post-run checks
confirmed the original record unchanged and the one claim plus five immutable
markers intact, with no additional allocation.

Adapter latency across the five replies was 224.9–271.6 ms, median 236.9 ms
(sample count 5). This excludes a real generative workload, channel delivery and
Core end-to-end processing; it is not a p95, SLA or production latency claim.
False-accept rate is unmeasured because the counterfactual trials never enter Core.

**Decision: NO-GO for adopting the current pinned selector/rubric on this fixed
criterion.** Keep deterministic production selection. A future attempt needs a
justified, explicitly versioned task/rubric and independently labelled held-out
scenarios, not a threshold tweak to fit this capture. No further paid request is
authorized by this completed protocol; preserve all attempt records. Billing
reconciliation and production-adoption evidence are deferred to #33, not marked complete.

## Authorized relevance-v2 diagnostic extension

The operator requested a remodeled task and one bounded retry. This permits at
most five additional synthetic POSTs under the **same original aggregate
500-cent wallet**, including the six earlier recorded attempts. It authorizes
no new US$5, wallet, refill, reservation, release, reset or protocol restart.
Account controls remain OWNER-CONFIRMED, independently verified false; charges
remain UNRECONCILED. Independent actual-diff billing/privacy/concurrency/trust
review and parent release gates still precede any paid execution. Implementation
and offline fake-HTTP tests do not establish that future execution succeeded.

`JevSelector` shares one transport/schema/budget boundary across exactly v1/v2.
V1 question, actual serialized provider bytes and routing remain unchanged for
matching request/config pairs. A new fail-closed rubric/config equality precondition
also applies to v1; this is not a claim that all invalid-caller behavior is unchanged.
V2 inlines only each existing compact candidate (`id`, `kind`, `summary`, `revision`)
and adds explicit kind-specific inclusion rules and the application boundary.
No raw payload, binary Artifact, additional memory, access or disclosure authority
is supplied. Unknown optional kinds refuse before HTTP. Core still supplies
mandatory pending items outside optional filtering and owns fallback, membership,
revision and disclosure checks. Task evidence equals the request/config rubric;
replay accepts only known, exactly matching task/request/result versions. The
Core selection policy version remains `context-selection-v1`.

V2 native Noul P(yes) >=0.7 is positive; <=0.3 is negative. Any value strictly
between causes application `abstained` and bounded reference fallback, even with
another positive. All strong negatives produce completed/empty, not fallback.
These exact initial thresholds are vendor starting heuristics, not local
calibration guarantees. Native probabilities and adapter routing are reported
separately; no model-generated explanation or confidence field is requested.

Offline default and closed plan compilation:

```sh
PYTHONPATH=tests python3 -m jev_relevance_probe
PYTHONPATH=tests python3 -m jev_relevance_probe --capture PRIVATE_SYNTHETIC_CAPTURE
```

Only the old validated 15-record capture profile qualifies. Snapshots are never
deserialized. Five literal messages and independent expected optional sets freeze
before new responses: `cucina e bagno` (both Contexts),
`Riprendiamo il contesto cucina, non quello bagno.` (cucina Context),
`Buongiorno, grazie.` (empty),
`Mi riferisco all'entità chiamata bagno, non al contesto della conversazione.`
(bagno Entity), and `Sì, confermo.` (empty with one fixed synthetic Core-shaped
mandatory pending item and reply reference). New message IDs are deterministic;
the original normalized Communication remains unchanged. No arbitrary input,
expected-label, ledger-root, environment-path or namespace flags exist. Exact
requests, provider bodies, expectations, thresholds and digests bind the canonical
plan hash. Changed plans/hashes refuse before key or claim access. Diagnostics
are post-hoc-informed, not representative held-out or Core end-to-end evaluation.

Live flags, reserved for the parent after review/release, match the old command:
`--live --capture PRIVATE_SYNTHETIC_CAPTURE --output NEW_PRIVATE_DIRECTORY
--expected-plan-hash HASH --ack-owner-prepaid-usd5 --ack-no-off-credit-charges`,
plus the approved `--ack-trusted-primary-group` on hosts needing that trust.
Only the short-lived child calls the existing selected-key dotenv loader; no key
reaches parent argv, pipes, artifacts or logs. Import/default/offline are inert.
The new protocol shares the frozen guard engine with v1; it is not a generic
experiment registry or another HTTP client.

`JevV2BoundedSelector(config, api_key, planned_requests, *, plan_hash,
prior_plan_hash, prior_request_digests, trust_primary_group=False,
ack_no_off_credit_charges=False)` constructs inertly. Exactly five distinct v2
requests and five distinct prior hex digests are required. The expected old claim
is derived offline from the old catalogue plan compiled from the same capture.
Before **every** dispatch, validate the exact original reservation, old claim,
five old markers, new claim and all reached new markers, including owner-only
permissions, no-follow/single-link and FD/path binding. New fixed filenames are
`jev-v2-followup.json` and `jev-v2-attempt-1.json` through
`jev-v2-attempt-5.json`. The new exclusive claim binds the prior claim SHA-256,
current plan and five current request digests. File/directory fsync precedes
HTTP. Old bytes never change. There is no new allocation, namespace, resume or
slot release. Competing processes have one claim winner; partial writes, crashes
and errors burn evidence and refuse all further work. Old smoke/v1 commands
remain closed by their existing records.

Each request timeout is <=2000 ms, the batch deadline is 15 seconds and the
child lifetime is 20 seconds. Auth/quota/rate/model/transport/schema/usage/echo,
budget/deadline and artifact failures are terminal. A semantic expected-set
failure may continue only to the next already-frozen case, never adaptive retry.
Receipts distinguish required prior count six from actually verified prior
count, invocation-local markers/delegations, conditional known global records
(6 + new markers), upper bound eleven, and unknown actual HTTP count. The
500-cent reservation is reported only after validation; otherwise null. Observed
validated usage and public-rate nominal estimates never reconcile charges.
Expected positive/negative matches, ambiguous IDs and application fallback stay
separate. False-accept rate/p95 remain unmeasured, held-out/Core-end-to-end and
production GO false. Even five matches cannot establish production adoption.
The #32 closeout explicitly transfers the unfulfilled deployment acceptance to #33.

### Offline implementation proof (2026-10-23)

Before any v2 response, authoritative specification review clarified the unreleased
question to:

> Does `candidate` satisfy `selection_rule` for inclusion among the optional records used to interpret `state.communication.text`?

The false criterion now symmetrically says the kind's rule does not apply. This removes a conflicting
extra necessity test; a named Context qualifies under its operational rule.
Kind rules, boundary, true criterion, thresholds, expectations, request identities,
guard, versions and model are unchanged. This is pre-live modeling clarification,
not adaptive tuning or semantic-quality proof. The API-boundary test freezes the
exact normative question and both criteria; it failed against the previous phrase.

The earlier 135/542/mypy proof (outer wall 206.625 seconds) remains historical;
it does not validate the clarified provider wire. After the final source changes,
the clarified source passed 135 focused tests (new v2, existing smoke/catalogue,
Context and evaluation suites), strict cached offline mypy on 20 source files,
and all 542 tests with `timeout 600 python3 -m unittest discover -s tests -v`
(exit 0; outer-wrapper wall 281.665 seconds). Twenty-six new tests cover actual
v1 serialized-body compatibility against the base implementation, v2 bands and
capture/replay, mandatory context, fixed scenarios, prior/current evidence,
finite slots, process/thread races, durability/interruptions, unsafe nodes,
terminal fake-HTTP errors, private output and offline/selected-key boundaries.
Financial tests use temporary ledgers/env files, mocked NSS and fake HTTP.

These are local contract/regression proofs, not semantic quality, real billing,
account verification or future live-execution evidence. No live request was made
by the implementer. Independent actual-whole-diff review and scoped clarification
re-review passed; the parent published the reviewed source as
`bec0eb1f80c63c35a5b50c96c5f147a781d12bdf` on main with a verified non-force push.
The completed parent-only execution is recorded below. Production remains
deterministic. The superseded
offline owner plan `41c514d6fe42e6f84459870f534542e3a709f4d8c865a34e0df76ef398b8b879`
must not be dispatched or overwritten. The parent compiled the new private final
plan recorded below; the completed v1 prior-plan hash `68f38641db29c2741f904ba84b40303a922ab133d0ca04fc55db64c6f8614f2d`
remains unchanged.

Review reading limits require no source changes: unavailable receipts require
read-only marker audit, not an assumed HTTP count or unique failure cause.
`reserved_cents=500` verifies the original hold, not the entire prior evidence set.
Generic provider errors intentionally hide specific causes; use exit code and
receipt, not stdout, for status. A deterministic selector's v2 task label alone
is not proof it ran the Jev algorithm; defaults and deployment remain unchanged.
The whole-feature review and scoped clarification re-review together gated release
and the new paid batch. Both gates passed before execution.

### Observed relevance-v2 outcome

The parent compiled a new private final plan after re-review:
`ae88902982833c5b3224778f6dd341e32b17a03e19f334105b4e9249acb08f3f`.
The prior v1 binding remained
`68f38641db29c2741f904ba84b40303a922ab133d0ca04fc55db64c6f8614f2d`.
All five request identities, expected positive/negative sets and thresholds were
unchanged from the pre-live specification. The superseded offline plan was never
dispatched. The selected dotenv key was loaded only in the short-lived live child.

One authorized five-case batch completed with five validated native replies, no
transport/schema/usage error and no additional retry. The exact operating policy
remained yes >=0.7 / no <=0.3 / strict middle -> application fallback.

| Frozen case | Expected optional selection | Observed result |
| --- | --- | --- |
| `cucina e bagno` | Both Contexts, nothing extra | Exact: cucina P(yes)=0.86, bagno=0.82; all 13 others <=0.19 |
| Resume cucina, explicitly exclude bagno | Cucina Context only | Exact: cucina=0.91; bagno Context=0.13 |
| Unrelated greeting | Empty | Exact: all 15 probabilities <=0.19; completed/empty, no fallback |
| Explicit bagno Entity, not the conversation Context | Bagno Entity only | Exact: Entity=0.82; same-named Context=0.10 |
| Confirmation with one mandatory pending item already supplied | No optional records | No positive, but three ambiguous probabilities (0.33, 0.34, 0.36); adapter requests fallback; exact-set result unmeasured/null |

Thus **four cases have exact completed selections; one remains deferred**, not
five passing cases. Across 75 Nouls, four expected positives were found, 68 records
were definite negatives and three were ambiguous. No extra positive appeared in
this sample. In case 5 the ambiguous records were the bagno Context and two prior
`Yes` communications. The mandatory pending item was present in the input, outside
optional filtering. This standalone probe did not execute Core fallback, resolve
Grounding, confirm a claim, or exercise a real generative workload.

The same model `jev-1.13.0` and positive threshold 0.7 now select exactly the two
Contexts in the original case. This corrects any inference that the v1 result
established Jev's general unsuitability. Input modelling and application routing
changed together; these data do not isolate which individual change caused the
improvement. No thresholds, rules or expectations were tuned after these replies.

Validated v2 usage was 38,144 input / 3,795 output tokens. Including the original
smoke and v1 diagnostics, eleven validated replies used 63,610 input / 7,495 output.
At the advertised input-only rate, nominal cost is US$0.001602048 for v2 and
US$0.002671620 overall. **Actual charges remain UNRECONCILED**, not a verified
account balance. Owner-confirmed wallet controls are not independent provider
verification. No funding, account setting, permission or provider/model change.

Read-only post-run audit verified all prior bytes and the new claim plus five
markers: thirteen physical ledger nodes, eleven recorded attempts, original
500-cent local hold preserved, zero new allocation. No reset, release or replay
of a completed protocol occurred. Adapter latency was 231.4–276.6 ms, median
251.3 ms, n=5; this is not p95/SLA or Core end-to-end latency.

**Decision:** retain v2 as the better-specified experimental integration, not an
approved production selector. Default remains deterministic. These are five
post-hoc-informed synthetic cases, not representative held-out validation;
false-accept, calibration, real Grounding and production end-to-end evidence remain
unmeasured and tracked in optional follow-up #33, not claimed completed by closing
#32. This authorized batch is complete; do not spend more
or alter the fifth-case policy to make its result pass. All durable evidence stays.

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
representative held-out or production evidence for required-record recall,
wrong-selection/false-accept rate, fallback rate, end-to-end latency or actual
total cost. The post-hoc synthetic diagnostics above do not close those gates.
Jev remains unenabled; the deterministic selector remains the default. #17 uses captured optional decision
metadata through ordinary proposals, not a separate live per-item Jev adapter;
#27 does not adopt Jev for Artifact perception/classification. Enabling any path
still requires the independent task-specific evidence above.
