# Evaluation Trace and Deterministic Replay — #29

> **For agentic workers:** Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` to implement this plan task by task.

**Goal:** Complete operational trace evidence and replay Core semantic decisions without any inference call, using existing deterministic validation and commit boundaries.

**Authority:** GitHub #29, parent #12, and `docs/superpowers/specs/2026-10-05-semantic-decision-routing-design.md`. All five #29 acceptance criteria apply; the analyst's reconstructed criteria are not a substitute for the issue.

**Architecture:** Extend existing per-attempt Working State traces and adapter evidence. Add one small, operator-only replay module. Replay uses captured decisions, a consumer-owned initial-state fixture, and an isolated database. Reuse Core context assembly, validation, commit, and assertion seams; do not build a separate semantic engine.

**Scope exclusions:** No benchmark runner, vendor-specific evaluator, live-model comparison, new dependency, sender/API trace endpoint, retention-policy redesign, or automatic full-database snapshot for every normal production turn. #30 owns the broader retention work.

## Observed behavior and invariants

- `Core._accept` persists normalized ingress and reserves a processing attempt before inference. Transactions do not span provider work.
- `context.assemble`, `context.select`, and `context.retrieve` own structural scope, budgets, selection, and revisions. `context.model_view` enforces the generator's disclosure boundary.
- Context selection already records revisions, primitive assessments, fallback, and latency. Intermediate generative proposals and generative usage are currently lost.
- Existing delivery retries restore a captured Proposal and do not call the model. This is not a general offline replay contract.
- `history.check` and `history.commit` own deterministic validation and atomic trusted mutation. Trusted history and outbox payloads never include trace evidence.
- Core-generated record, commit, event, and outbound IDs are nondeterministic. Replay must preserve identity relationships, not substitute current state or make fresh inference.
- `ContextPack.captured` omits media bytes and clears optional catalogue payloads. It is not a complete initial-state fixture.
- Replay must reject missing required evidence, unsupported policy/question versions, changed candidate revisions, or unavailable required Artifact versions. It must never fall through to a live provider.
- Preserve secret rejection, default-deny sender disclosure, stale fencing, idempotency, immutable history, and independently resolved Grounding Items.

## Baseline

- `python3 -m unittest discover -s tests -p test_processing.py -q`: 14 tests pass.
- Full discovery exceeded a 120-second command timeout. This is not a passing baseline and needs a longer bounded run during verification.
- Existing untracked `docs/research/opencode-go-deepseek-limits.md` belongs to the operator; leave it untouched.

## Completion evidence (2026-10-20)

Checks below reflect the inherited implementation, independent review, and fresh
verification after scoped review fixes. Original red-run chronology was not
reconstructed; the new review regressions were observed red before their fixes.
Focused verification passed 102 tests. Full discovery (`-q`, same discovery as
`-v`) passed 321 tests in 161.487s with a 300-second timeout. `git diff --check`
passed. Review findings and remaining fixture limits are recorded in the SDD
ledger; final independent re-review remains the acceptance gate.

## Task 1 — Capture complete decision evidence

**Files:** `piecetogether/core.py`, `piecetogether/context.py`, `piecetogether/jev.py`, `piecetogether/openai_responses.py`; focused tests in existing processing, context, and provider test modules.

- [x] Add failing tests for a retrieval turn with two generative calls, a provider failure, and a successful response carrying usage.
- [x] Introduce a fixed trace/replay schema version and fixed Core selection/disclosure/validation policy versions. These are code versions, not deployment knobs.
- [x] Keep existing trace keys compatible. Add an ordered generative-attempt list, rather than replacing the existing integer `model_calls` field.
- [x] Record each call's effective provider/model ID, task/instruction version, input Communication and Context Pack references/revisions, safe captured Proposal, latency, usage, and outcome. Capture failures without persisting credential-bearing output or exception text.
- [x] Preserve every intermediate Proposal used for retrieval. Retain the final committed/normalized checkpoint separately from the originally captured provider Proposal.
- [x] Extend adapter metadata through the existing provider boundary without mutable shared `last_usage` state. Preserve simple deterministic providers and custom providers that return the existing Proposal type.
- [x] Record pinned OpenAI model ID, instruction version, and reported token usage when available. Missing usage remains explicitly unknown, not zero.
- [x] Extend selector evidence with canonical request identity, task/question version, permitted input references, candidate revisions, effective operating point, and selection/abstention. Preserve raw primitive-specific values without turning them into a common confidence probability.
- [x] Preserve raw serializable malformed/unknown responses and failure classifications before deterministic fallback. Unserializable required evidence must be explicitly unavailable.
- [x] Record semantic commit/event references and final processing/retry/delivery outcomes on each attempt, including paths that return before delivery.
- [x] Keep replay-required decisions separate from optional full prompts, raw transports, and diagnostic payloads. Required evidence must remain usable when optional diagnostics are absent.

**Proof:** Focused tests assert distinct per-call snapshots, correct effective model/version/usage, failure capture, unchanged fallback bounds, and unchanged trusted/event payloads.

## Task 2 — Replay selectors without inference

**Files:** New `piecetogether/evaluation.py`, small extraction in `piecetogether/context.py` if needed; new `tests/test_evaluation.py`.

**Internal interface:** `CapturedSelector` implements the existing `SemanticSelector.select(SelectionRequest) -> SelectionResult` protocol using ordered captured attempt evidence.

- [x] Add failing tests for successful selection, abstention, malformed/unknown selection, provider outage, and exhausted fallback budget.
- [x] Bind captures to canonical request identity, candidate IDs/revisions, permitted context, question version, and selection policy version.
- [x] Return captured valid results or reproduce captured operational failures. Reuse the same validation and deterministic bounded fallback logic as live processing.
- [x] Preserve budget/time-derived outcomes from captured evidence; do not let the speed of offline execution turn a recorded timeout into success.
- [x] Make missing, mismatched, consumed, or redacted evidence raise an explicit `ReplayUnavailable` error. This error must not be swallowed as a normal provider outage and trigger replacement inference or an invented answer.
- [x] Assert full capture consumption so missing or additional decision calls fail comparison.

**Proof:** Reproduced selected IDs, order, revisions, disclosure eligibility, fallback reasons, and budget outcomes match captures. All network/inference entry points fail tests if invoked.

## Task 3 — Generic semantic replay contract

**Files:** `piecetogether/evaluation.py`, `piecetogether/core.py`, narrowly scoped `piecetogether/history.py` changes only where deterministic reuse requires them; `tests/test_evaluation.py`.

**Operator-only contract:** A `ReplayCase` carries the initial-state fixture, normalized inputs, Contract and policy versions, Artifact version metadata, captured decisions/Proposals, and expected semantic outcomes. `replay(case) -> ReplayResult` runs in a temporary database and raises `ReplayUnavailable` for absent evidence. A capture helper can obtain a case at the Core service boundary for deterministic scenario fixtures.

- [x] Add a failing semantic scenario: expose candidates, accept Grounding, commit trusted assertions, and produce an outbox event; replay it with providers that must never run.
- [x] Use SQLite's standard backup/snapshot facilities for explicit fixture capture. Never copy the live database file without a consistent snapshot, mutate the source database, or snapshot every normal production turn.
- [x] Capture only replay configuration and evidence. Never export environment values, deployment credentials, secret references resolved to values, or arbitrary optional prompt diagnostics.
- [x] Restore the captured Contract and selection/policy versions. Never silently use the active deployment's newer Contract, current provider configuration, or latest state.
- [x] Restore normalized Communications and required Artifact identities/versions. Where recorded Proposals make media inference unnecessary, replay verifies captured Artifact metadata without fetching external bytes. Missing evidence required for a replayed lifecycle operation fails explicitly.
- [x] Reuse real Core processing with injected captured adapters, or extract the smallest shared deterministic processing seam needed. Do not implement a parallel validator/committer.
- [x] Handle generated identities explicitly. If exact replay needs captured ID allocation, pass a scoped identity source through the owning functions; do not monkey-patch module globals. Comparison must preserve relationships, revisions, grounding evidence, and semantic values, and must not hide semantic differences under broad normalization.
- [x] Capture operational channel outcomes and use a local replay channel. Replay never sends a message, dispatches an external sink, or constructs an inference/remote-storage provider.
- [x] Replay captured operational retries using recorded outcomes. Reject unsupported captures explicitly rather than returning recorded expected assertions as computed output.
- [x] Compare recomputed Context Packs, validation/rejection, commit/retry outcome, trusted assertions/provenance/relationships, and emitted semantic events against expected results. Return explicit mismatches, not success based only on restored state.
- [x] Ensure optional diagnostics can be removed without breaking replay, while deleting required evidence produces an explicit failure.

**Proof:** No-inference replay reproduces successful grounding/commit and rejected scenarios. Tampering with input revisions, question/policy versions, Proposal, required evidence, or expected assertion produces an explicit failure or mismatch. Source state remains unchanged.

## Task 4 — Boundaries, documentation, and verification

**Files:** `tests/test_evaluation.py`, existing relevant test modules, `README.md`, and `docs/jev-adoption.md` where its offline replay guidance needs the new concrete seam.

- [x] Add regression assertions that provider confidence, raw decisions, and trace payloads never enter trusted records, Current Trusted View, State API responses, events, or sender replies.
- [x] Cover restored attempts after restart and existing delivery-only retry without extra inference.
- [x] Document the operator-only capture/replay interface, sensitive-fixture handling, required evidence versus optional diagnostics, and explicit replay failure after retention removes required data.
- [x] Run focused processing/context/provider/evaluation suites first.
- [x] Run full `python3 -m unittest discover -s tests -v` with a longer bounded timeout; investigate any failure without widening feature scope.
- [x] Inspect `git diff --check`, changed files, and fixture contents for secrets and unrelated changes.
- [x] Obtain independent review of replay isolation, trust boundaries, version matching, and concurrency safety. Fix substantive findings and rerun affected tests.

## Stop condition

All five acceptance criteria in #29 have direct test evidence. Replay computes semantic outcomes with no external inference, missing required evidence fails explicitly, traces remain Working State, and existing processing behavior remains intact. Report material limits; do not close #29 as complete if any criterion remains unproved. Do not commit, push, or close the issue merely because this plan was written.
