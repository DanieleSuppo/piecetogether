# Prepaid Jev Catalogue Diagnostics Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans. Track the checkboxes. The operator delegated implementation/review/delivery and authorized tests using the repository `.env`; ordinary plan approval is waived. No branch/worktree. One writer.

**Goal:** Run at most five frozen synthetic catalogue/ID ablations under the existing US$5 prepaid envelope, without resetting the first reservation or claiming adoption.

**Architecture:** Preserve the original one-shot command and record. Add one fixed supplemental batch claim and immutable pre-dispatch markers in the same private ledger root. Reuse canonical Jev payload construction, response sanitation and private outputs. A separate diagnostic command builds only the fixed synthetic variants; production selection remains unchanged.

**Tech Stack:** Existing Python/POSIX, SQLite-free JSON attempt records, unittest, no dependencies.

**Spec:** `docs/jev-adoption.md`, section “Prepaid catalogue diagnostic exception”; independent decision `/tmp/opencode/jev-followup-budget-decision.md`; offline proof `/tmp/opencode/jev-live-offline-proof.json`. Owner subsequently confirmed that no off-credit/postpaid/card charge is possible. The aggregate wallet ceiling is owner-confirmed, not independently verified.

## Global Constraints

- Existing baseline is `1c8cb93da163915749caedca9e0116c579a00128`. Current shared budget remains US$5 TOTAL, including the first smoke. No purchases, refills, new wallet or third batch.
- Original `reservation.json` must be byte-equal to the fixed full-500 record and remain unchanged. Missing/partial/corrupt/altered evidence refuses; never recreate it. Supplemental records allocate no new money. Actual charges remain UNRECONCILED; nominal prices never settle/release funds.
- Keep model `jev-1.13.0`, rubric `context-relevance-v1`, exact question text, minimum0.7, strict response validation and deterministic production default. No production data, real channel, Artifact bytes or generative-provider calls.
- Default/import/offline tests do not read real `.env`, actual home ledger, NSS or network. Every unit-test ledger/env root is patched to private temporary fixtures. Actual paid execution belongs only to the parent after actual-diff billing review and plan-hash preflight.
- Preserve group trust as explicit primary-group opt-in, private0700 directories/private0600 single-link files, ownership/no-follow/inode checks, fixed OS-home identity and fsync ordering. No chmod, reset, release, force, per-output/run namespace or provider/model retry.
- Preserve the protected unrelated research file unchanged and unstaged. Workers never stage/commit/push/GitHub/delegate or make paid calls.

## Review Focus

1. Existing/partial supplemental claims and concurrent invocations must not create a second batch; old one-shot and follow-up share one envelope.
2. A crash after a durable attempt marker but before HTTP burns that slot, never authorizes restart/resume. A refusal must not falsely report global attempts or zero charges.
3. A malformed/real-data capture must not reach a provider. Only the existing 15-record cucina/bagno synthetic profile qualifies.
4. New IDs must preserve question-key wire order, map back to original identities and never be fed into Core as trusted records.
5. Echoes, unknown usage, API errors and output failures must stop subsequent paid attempts without leaking keys or releasing the hold.

## Task: one complete diagnostic path

**Files:**
- Modify `piecetogether/jev.py`: extract pure `_payload(request: SelectionRequest, model_id: str) -> dict[str, Any]`; selector reuses it without changing bytes, bounds or question text.
- Modify `piecetogether/jev_smoke.py`: narrowly extend existing private ledger/sanitization helpers and add `FollowupJevSelector`.
- Create `tests/jev_catalogue_probe.py`: fixed plan builder, explicit operator command and descriptive analysis.
- Create `tests/test_jev_catalogue_probe.py`: portable temp-only regression/integration tests using existing fixtures, never the user's actual capture/env/ledger.
- Update this plan and `docs/jev-adoption.md` accurately. No other file without a concrete owning-seam reason.

**Interfaces:**
- `_payload(request, model_id)` is the canonical source for provider-body digests and question-key order; existing selector behavior is byte-identical.
- `FollowupJevSelector(config: SelectionConfig, api_key: str, planned_requests: tuple[SelectionRequest, ...], *, plan_hash: str, trust_primary_group: bool = False, ack_no_off_credit_charges: bool = False)` implements `select(request) -> SelectionResult`. Exactly five distinct planned inputs, pinned values, explicit literal-True financial acknowledgement. Bind each call to its next planned request digest; one instance serializes calls and becomes terminal after any error.
- `build_plan(capture_data: dict[str, Any]) -> dict[str, Any]` returns a canonical serializable plan including provider-body/request digests, mappings, versions, source-capture identity, analysis rule and hash. Every body differs from the original. A restoration helper may stay private.
- `main(argv=None) -> int` in `tests/jev_catalogue_probe.py`: default inert offline preflight; `--capture` can compute the plan without secrets/real-ledger access. Live additionally requires `--live --output NEW --expected-plan-hash HASH --ack-owner-prepaid-usd5 --ack-no-off-credit-charges`; primary-group trust stays optional and explicit. Load only repository `.env` key through the existing loader inside this explicitly paid child path, never print it or accept it on argv. An expected-plan-hash mismatch refuses before claim/HTTP.

### Frozen five-case plan

Control is the existing original-ID/original-order response, not another request. Before scores, identify the two Context records independently and validate the exact synthetic profile (known kind/summary multiset, development/a1/s1, text `cucina e bagno`, no attachments/references or arbitrary customer text).

1. Original IDs, reversed candidate catalogue.
2. Readable IDs, original candidate catalogue.
3. Readable IDs, reversed candidate catalogue.
4. Fresh opaque IDs, original candidate catalogue.
5. Fresh opaque IDs, reversed candidate catalogue.

All other provider-visible data is unchanged. Relabel candidate/permitted-context identities consistently. Preserve semantic question-key wire order: sort original IDs; map readable IDs to zero-padded original-sort ordinal plus fixed kind/summary slug; generate deterministic UUID5 identifiers with `uuid.NAMESPACE_URL` and prefix `piecetogether-jev-catalogue-v1:`, sort them, assign to original sorted IDs. Assert uniqueness and preserved key order by original identity. Never change the question/rubric to use old answers under a new label.

Analysis is post-hoc-informed, not held-out: per case report scores mapped to original records, required recall/wrong selections at the unchanged0.7, content alignment and catalogue-position alignment. For each same-ID original/reversed pair, flag `position_tracking_suspected` and stop further calls only if all positional score differences are <=0.01 while at least one content-aligned difference is >=0.10. Publish the two error vectors; this is a diagnostic suspicion, not a universal causal/model certification. Other variation is descriptive only. No retuning, adaptive cases or production GO.

### Checked steps

- [x] **RED:** add tests for byte-identical old payload, five deterministic distinct variants, inverse identity mapping/question order, unchanged threshold/question, synthetic-only profile and frozen hash. Default/plan paths tripwire real env/ledger/NSS/network.
- [x] **RED:** add same-envelope tests: original exact500 required; no old record creation/mutation; fixed `catalogue-followup.json` O_EXCL claim binds plan/max5/initial1 with no extra funds; fixed per-attempt exclusive markers before dispatch; concurrent/mixed-mode single winner; corrupt/partial/changed evidence; restart and plan changes refuse.
- [x] Implement the guard using existing descriptor-relative helpers. Fsync complete claim and immutable attempt markers before delegation. Validate bindings and files every time. Burn failures conservatively, never overwrite a marker or retry. Preserve all500 on every outcome. Keep old `GuardedJevSelector`/`reserve_once` behavior unchanged. Share safe selection logic rather than fork echo/response handling.
- [x] **RED:** fake live integration tests count HTTP directly (max5 new; original historical1 separate), stop at first auth/402/429/model/transport/invalid-or-missing-usage/echo/output error, refuse a sixth/unplanned request, and prove exact/private receipts and no key in stdout/stderr/files. API abstention is data, not adaptive retry permission. Test full500 remains held on success/error/crash.
- [x] Implement the fixed command. New private output before claim; total batch deadline15s, each request timeout no larger than captured timeout or1000ms, existing byte/token bounds. Use fixed synthetic input only. Receipts distinguish original1, invocation-local new recorded slots, unknown historical counts on refusal, cap evidence OWNER-CONFIRMED, actual cost UNRECONCILED, nominal estimates and production_go=false. Never call counterfactual IDs a Core replay success.
- [x] Run focused new tests and old smoke tests, exact `uvx mypy --strict piecetogether`, exact `timeout 600 python3 -m unittest discover -s tests -v` with exit/wall receipt, then whitespace/index checks. No source changes after final gates; no unchanged reruns.
- [x] Report exact files/APIs/limits/secret and ledger boundaries/proof/diff hash and stop. Parent independent actual-diff financial/privacy/concurrency review precedes commit/non-force push and all real execution.
- [x] Parent-only after GO: compute live plan hash offline, save private manifest, then execute this one five-request protocol with explicit hash/acks under the user's current authorization. Stop on any guard/provider/usage/position-tracking failure. Preserve all records and report evidence, not adoption; no third batch.

## Offline implementation verification

- Initial RED: eight tests failed because the diagnostic module did not exist.
- Exact stop-rule regression RED: an epsilon admitted a position difference above
  0.01; decimal comparisons now preserve the frozen boundaries without tolerance.
- Initial author proof: 59 focused and 514 full tests passed. The first independent
  review approved billing/privacy controls and requested two evidence-label fixes.
- Both new label regressions failed before the fix: known zero counts were null,
  and an untested position rule was incorrectly reported false.
- Final diagnostic + unchanged smoke gate: 61 tests passed in 9.068 seconds.
- Nearest context/evaluation gate: 48 tests passed before the label-only fix;
  the final full suite includes these tests again.
- `uvx mypy --strict piecetogether` (cached/offline uv): passed, 20 source files.
- Final `timeout 600 python3 -m unittest discover -s tests -v`: 516 tests passed
  in 220.035 seconds; exit 0, wall 220.12956780893728 seconds. Final receipt is
  `/tmp/opencode/jev-catalogue-label-fix-full-receipt.json`; the canonical full
  log is `/tmp/opencode/jev-catalogue-probe-full-tests.log`.
- Worker used only portable synthetic fixtures, fake HTTP, private temporary env
  paths and patched temporary ledger roots. No actual dotenv/key/home-ledger or
  provider access occurred. Owner-capture plan hash and every paid invocation
  remain parent-only after independent actual-diff review.
- Exact whole-feature diff and reports are under `/tmp/opencode/`; the unrelated
  research file remains unchanged and unstaged. No source edits follow final
  gates. Scoped re-review of the final evidence-label fixes completed GO with
  no remaining findings (`jev-catalogue-probe-review.6f0c91b4.md`).
- Owner-capture offline compilation succeeded without key, ledger or network
  access: five cases, plan hash
  `68f38641db29c2741f904ba84b40303a922ab133d0ca04fc55db64c6f8614f2d`.
  Canonical JSON and the digest were checked again before dispatch; Python
  tuple/list representation differences did not change any value or wire body.

## Parent execution and decision

- Source delivery: `00f973f92f34917f0b7a492a32a57ac40a04c3af`, main, non-force
  push verified before the paid invocation. No source changes followed the gates.
- One batch completed using the selected repository dotenv key in the isolated
  child, the frozen hash, a new private output and all account/no-off-credit/group
  acknowledgements. No permission change, funding or account-setting change.
- Receipt: `/tmp/pt-jev-catalogue.7qpiav3r/result/receipt.json`; parent exit 0,
  wall 1.3893761349609122 seconds, no stderr and no raw child output printed.
- Five validated replies, five abstentions, no records at threshold 0.7. Maximum
  observed score 0.52. Positional rule tested three times without a trigger;
  headline null remains inconclusive. No counterfactual ID entered Core.
- Read-only post-run validation confirmed the exact original reservation, the
  frozen claim and all five markers. Six recorded attempts including the control;
  all 500 cents remain held. Audit made no ledger writes or additional requests.
- Follow-up usage 20,937 input / 2,941 output; including control 25,466 / 3,700.
  Nominal US$0.000879354 new / US$0.001069572 total is not reconciled billing.
- An offline threshold sweep found no exact two-Context/zero-extra selection in
  any of the six variants. Minimum extras at full recall for the new cases were
  6, 2, 2, 3, 2 respectively. This is not a retuned threshold or held-out proof.
- Decision: keep deterministic production selection. Current rubric/selector
  does not pass this fixture's fixed criterion. Actual charges, representative
  held-out quality and production end-to-end evidence remain open in #32.
  No third batch, reset, release, repeated smoke or model fallback is permitted
  by this protocol. Private observed summary: `/tmp/opencode/jev-catalogue-observed-summary.json`.

Rollback: do not run live; keep deterministic defaults. Do not roll back/delete/reset attempt evidence. No code installation or offline test may create a real ledger claim or read the actual credential file.
