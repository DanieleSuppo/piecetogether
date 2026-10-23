# Jev relevance v2 implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. The operator delegated ordinary technical choices and approvals and explicitly requested remodel plus retry. Work on current main, one writer; no branch/worktree. Parent retains review, commit/push and live authority.

**Goal:** Make the Noul inclusion task explicit and probability routing honest, preserve v1 compatibility, then deliver one reviewed fixed five-case retry under the same original wallet.

**Architecture:** Existing Jev client dispatches by rubric, sharing transport/validation. Core's policy is unchanged; task metadata binds each rubric exactly for replay. A fixed v2 diagnostic protocol extends existing append-only attempt logging without new money, arbitrary namespaces or mutable old evidence.

**Tech Stack:** Existing Python standard library, unittest, strict mypy; no dependencies.

**Spec:** `docs/superpowers/specs/2026-10-23-jev-relevance-v2-design.md`.

## Global constraints

- Pin `jev-1.13.0`; preserve v1 QUESTION, serialized provider body and routing for matching request/config pairs. Rubric/config mismatch now fails closed for v1 too (review F5); no general unchanged-invalid-caller-behavior claim. Default selector remains deterministic/v1.
- V2 one Noul per record: inline only existing compact metadata; per-kind atomic rule; P(yes), not confidence or degree of relevance. No raw payload excerpts, new DB access or model-generated authority.
- Initial fixed yes >=0.7 / no <=0.3 / middle ambiguous; any ambiguity uses existing application fallback. V2 config and request agree on 0.7. Never tune after live results.
- Original aggregate <=US$5 prepaid wallet only, OWNER-CONFIRMED no refill/off-credit/card. No funding, ledger reset/release/deletion, new wallet or dynamic experiment namespace.
- Original reservation + old claim + five old markers remain byte-identical. New fixed protocol max five POSTs, prior recorded six, upper bound eleven, zero new allocation. 15s batch, <=2000ms/request, 20s child. Errors terminal, crash burns slot, no resume/retry.
- `.env` key only explicit live child; all tests patch env/NSS/home-ledger roots to private temporary fixtures. No `config/.env`, `var/`, deployment DB, actual key/home-ledger or paid calls by writer/reviewer.
- Preserve `docs/research/opencode-go-deepseek-limits.md` unchanged/unstaged, SHA256 `9777af4ec294a45389d94c7c9fd4370a1003ba15e6d18bd9c7d63a03314b1985`.
- Parent-only commit/main/non-force verified push/live after actual-diff review. No packages/CI/hooks/provider/model/permission changes.

## Review focus

- Strong no vs uncertainty: 0, .1, .3, next representable value above .3, .5, next below .7, .7, .9, 1; any ambiguous item with a positive; no ambiguous items and no positives.
- V1 actual payload byte identity and captures; v2 capture/replay; reject unsupported/cross-version task/request/result evidence without broadening replay.
- Summaries only, mandatory pending unchanged, no new sender disclosure or trusted mutation; unknown optional kind refuses before HTTP.
- Old evidence missing/corrupt/partial/unsafe or group opt-in absent means no dispatch; reports unknown, not historical zero. Concurrent new guards have one fixed winner.
- Plan/import/offline never resolve key/NSS/ledger/network; immutable new claim/markers precede dispatch, wrong plan/sequence/usage/errors stop all further calls.

## Files and one-writer integration

- `piecetogether/jev.py`: versioned payload and probability policy; shared client.
- `piecetogether/context.py`: allow exactly two rubric versions, task evidence from config; unchanged Core selection policy.
- `piecetogether/evaluation.py`: exact known request/task version binding, legacy-safe replay.
- `piecetogether/jev_smoke.py`: share existing frozen-batch mechanics between two fixed protocols; add `JevV2BoundedSelector` and validation of all six prior records.
- New `tests/jev_relevance_probe.py`, `tests/test_jev_relevance_probe.py`; nearest existing suites as needed for targeted regression coverage.
- `docs/jev-adoption.md`, this spec/plan, existing new `docs/research/2026-10-23-jev-usage-audit.md`: correct terminology, protocol and proof. No unrelated edits.

These are one tightly coupled versioned selection/diagnostic path, not parallel component ownership. One implementer integrates it; no overlapping writers or nested agents.

### Task 1 — versioned native decision semantics and replay

Interfaces:
- Retain `_payload(request: SelectionRequest, model_id: str) -> dict[str, Any]`; v1 path unchanged, known v2 path produces inline compact candidate, explicit kind rule and question/criteria from spec.
- Retain `JevSelector(config, api_key)` and result shape; v2 requires matching request/config, .7 initial point, known kinds. Reuse HTTP, budgets, strict native parsing and membership validation.
- Core task evidence derives from request/config rubric; `SELECTION_POLICY_VERSION` and `POLICY_VERSIONS` unchanged. CapturedSelector accepts known v1/v2 only and exact task==request rubric plus all old checks.

- [x] Write regression tests for old serialized payload, v2 inline candidate/rules/no raw payload, config/request mismatch, bands and ambiguous+positive; v2 Core capture/replay and cross-version rejection. Use fake HTTP only.
- [x] Run focused new cases RED; record genuine expected failures, not infrastructure/import accidents.
- [x] Implement smallest shared client/version dispatch and metadata handling. No second transport or widened provider projection.
- [x] Run focused tests GREEN, including old context/evaluation/selector tests; record results.

### Task 2 — fixed new financial protocol and five frozen scenarios

Interfaces:
- Add `JevV2BoundedSelector(config, api_key, planned_requests, *, plan_hash, prior_plan_hash, prior_request_digests, trust_primary_group=False, ack_no_off_credit_charges=False)`. Exactly five distinct v2 requests, prior tuple five unique hex digests. No filesystem/env/NSS/network in constructor.
- Reuse existing FollowupJevSelector engine with code-owned fixed protocol constants/closed profiles; no caller-chosen paths/namespaces. Preserve all v1 bytes, limits and externally observed behavior. New record names exactly `jev-v2-followup.json`, `jev-v2-attempt-N.json`.
- Derive expected old claim from prior hash/digests and static original-record hash. Validate original, old claim, five old markers and all current records before each new dispatch. New claim binds exact prior claim hash/current plan/current digests.
- CLI default/import inert; `--capture PRIVATE_CAPTURE` compiles pure plan. Live requires same flags as existing probe: `--live --output NEW --expected-plan-hash HASH --ack-owner-prepaid-usd5 --ack-no-off-credit-charges`, plus approved `--ack-trusted-primary-group` on this host.
- Plan uses existing closed `_synthetic_request`/prior catalogue builder; exactly the five messages/expected sets in spec, deterministic identities for new messages, one synthetic Core-shaped mandatory pending item only in case 5. No arbitrary-case/expectation flags.
- New receipt separates native probabilities/positive-negative-ambiguous IDs from adapter routing, expected-set match, conditional costs, prior-known counts. False-accept/Core-end-to-end/held-out/production fields stay explicitly unmeasured/false.

- [x] Write financial regression tests against temporary old reservation+valid prior claim+five markers; missing/corrupt/mismatched records, namespaces, no-funds allocation, terminal errors, concurrent processes, partial/fsync/FD-binding failures, no sixth dispatch/resume, strict/default group safety, echo sanitation and unknown counts. All real boundaries tripwired.
- [x] Write five-case plan tests: literal texts, expectations fixed by kinds/summaries not scores, direct candidate data, five unique bodies, deterministic canonical hash, wrong hash refuses before secret/claim, no snapshot deserialization, no real key/NSS/ledger/network in offline/default, final output failure stops.
- [x] Run new regressions RED, implement narrow shared guard/CLI, run GREEN. Preserve old smoke/catalogue suites and explicit old marker bytes.
- [x] Update docs including application-generated v1 abstention terminology and conditional aggregate-wallet exception for this new authorization. Do not rewrite old raw outcomes or claim representative held-out/p95 evidence.

### Task 3 — source proof and parent release gate

- [x] Run focused new + old smoke/catalogue and nearest context/evaluation tests once final source is stable (135 passed).
- [x] Run `UV_OFFLINE=1 uvx mypy --strict piecetogether` using existing cache only. No installation/network fallback on errors (20 files clean).
- [x] Run exact `timeout 600 python3 -m unittest discover -s tests -v`, capture full log, wrapper exit and wall receipt under `/tmp/opencode/jev-v2-*` (542 passed; exit 0; wall 206.625s).
- [x] Check tracked/new-file whitespace, index empty, protected research hash; create exact whole-feature diff including all new files and source hash manifest against HEAD `a7b032dc80e51c0d619990432ff6dafc6b098cc5` (12 feature files; protected research excluded).
- [x] Writer returns files, meaningful RED/GREEN, proof paths, risks and whole diff at `/tmp/opencode/jev-v2-implementation-result.md`; no commit/push/live. No source edits after gates. Parent independent actual-diff privacy/billing/concurrency/trust review required.
- [x] Parent handles material findings with targeted fixes/re-review; rerun only gates invalidated by actual source changes. Explicitly stage reviewed task files, non-force commit/push on main, remote-SHA verification; do not integrate divergence.

### Pre-live question clarification — final bounded fix wave

Authoritative specification now tests exact operational-rule membership at
`state.communication.text`, with symmetric false criterion. This resolves Q1/Q2
before any v2 response; it is not adaptive post-response tuning. No kind rule,
true criterion, application boundary, threshold, expected set, request identity,
guard, version, model or v1 wire changes. Existing API-boundary test freezes the
exact question and both criteria (135 focused tests still total).

- [x] Observe API-boundary RED on the previous phrase; apply only the two normative v2 text changes.
- [x] Re-gate clarified source: 135 focused tests; cached offline strict mypy clean on 20 files; exact full 600-second command passed 542 tests, exit 0, outer wall 281.665s.
- [x] Freeze clarified source hashes and preserve prior gate/review artifacts as immutable history; new artifacts use `/tmp/opencode/jev-v2-clarified-*`.
- [x] Parent performs scoped re-review and compiles a NEW private final owner plan. Superseded offline plan `41c514d6fe42e6f84459870f534542e3a709f4d8c865a34e0df76ef398b8b879` must not be dispatched/overwritten. Completed v1 prior hash `68f38641db29c2741f904ba84b40303a922ab133d0ca04fc55db64c6f8614f2d` stays unchanged.

Review F1–F6 are reading limits only, not source/API additions: unavailable
receipt requires read-only immutable-marker audit, not an assumed HTTP count or
unique termination cause; reservation=500 verifies only original hold; provider
cause remains generic by privacy design; use exit/receipt, not stdout; invalid
v1 rubric/config pairs fail closed; deterministic-v2 task label is not proof of
Jev algorithm execution or production adoption. Defaults remain unchanged.

### Task 4 — parent-only execution and honest evidence

- [x] Compile owner capture offline under key/NSS/network tripwires; save private canonical manifest and expected hash. Check exact prior plan binding and five unique v2 bodies. No real ledger mutation in preflight.
- [x] After review/source release GO, run one new frozen five-case protocol with all flags in a short-lived child reading Root `.env`; new private output. No new user routine confirmation required by existing delegation/current retry request.
- [x] Inspect sanitized receipts; read-only verify old reservation/claim/markers unchanged and new claim/markers correct. Never reset/release/retry after error or completion.
- [x] Record observations vs application decisions, usage-based nominal estimates vs UNRECONCILED charges, quality limits and disabled production default. Commit/push docs only, update #32/#12 honestly, keep missing adoption gates open. Stop; no automatic next experiment or model change.

## Parent delivery and observed outcome

- Independent whole review and scoped Q1/Q2 re-review passed before source release.
  Source commit `bec0eb1f80c63c35a5b50c96c5f147a781d12bdf` was non-force pushed
  and remote SHA verified. Final source proof remains 135 focused / 542 full /
  strict mypy 20, unchanged by subsequent result documentation.
- Final owner plan was privately compiled after GO under key/NSS/ledger/network
  tripwires, hash
  `ae88902982833c5b3224778f6dd341e32b17a03e19f334105b4e9249acb08f3f`.
  Prior v1 hash and request digests remained unchanged. All five byte-budget checks
  passed. Superseded offline hash `41c514d6...` was preserved, never dispatched.
- One explicit live child, all hash/account/no-off-credit/primary-group flags,
  same original wallet. Exit 0, parent wall 1.4346854639006779s, no stderr or raw
  child output printed. Receipt:
  `/tmp/pt-jev-relevance-v2.8xfzwi5b/result/receipt.json`.
- Four exact completed sets, one unresolved application fallback. Original case
  now selects only cucina Context P(yes)=0.86 and bagno Context=0.82. Single-scope,
  greeting/empty, and Entity-vs-Context cases match. Confirmation case has no
  positives but three ambiguous probabilities 0.33/0.34/0.36; do not count it as
  an exact pass or claim native Jev abstention. Mandatory pending input was supplied;
  no actual Core fallback, Grounding resolution or generative workload was run.
- No expected label, rule, threshold or question changed after any v2 response.
  No follow-up paid retry. Four expected positives, 68 definite negatives and three
  ambiguous values across 75 Nouls; no extra positive in this limited sample.
- Read-only audit verified old reservation/claim/five markers unchanged and new
  claim/five markers valid: thirteen physical nodes, eleven recorded attempts,
  unique original hold 500 cents, new allocation zero. No ledger write by audit.
- New usage 38,144 input / 3,795 output; overall eleven replies 63,610 / 7,495.
  Nominal US$0.001602048 new / US$0.002671620 total is not reconciled billing or
  verified remaining credit. Account controls remain OWNER-CONFIRMED.
- Parent result artifacts: `/tmp/opencode/jev-v2-live-start.json`,
  `jev-v2-live-return.json`, `jev-v2-observed-summary.json`. Private summary
  independently checks raw bands/expected sets, arithmetic and immutable records.
- #32 result comment: https://github.com/DanieleSuppo/piecetogether/issues/32#issuecomment-6376974256
  Parent #12 update: https://github.com/DanieleSuppo/piecetogether/issues/12#issuecomment-6376978110
- Decision: retain the improved v2 experimental integration; no production
  activation. Default deterministic, held-out/calibration/false-accept/real
  Grounding/Core end-to-end/billing acceptance still open. This protocol is done.
