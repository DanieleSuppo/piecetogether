# Core and replay typing implementation plan

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans. This is the first bounded #32 delivery, not the retention or Jev implementation. The operator authorized autonomous technical decisions and execution on current `main`.

**Goal:** Make `uvx mypy --strict piecetogether` pass by correcting the thirteen existing contract errors, without changing trusted semantics or hiding errors.

**Architecture:** Keep existing ModelProvider and ChannelPlugin contracts. Make captured/recording implementations satisfy them honestly, including explicit failure for unsupported transport/inference operations. Express Artifact byte-store behavior as one structural protocol in its owning module, used by the real local store and the two existing replay implementations; never inherit a fake local filesystem store or cast replay doubles into one.

**Tech Stack:** Python 3.10-compatible standard typing/Protocol, existing unittest fixtures and SQLite.

**Spec:** User's follow-up authorization; GitHub #32 first acceptance criterion; `docs/evaluation-replay.md` isolation and no-inference guarantees.

## Global Constraints

- Baseline `390cbdd70e15db73100190d57c2cacc7b8fa844c`, existing `main`; no new branch/worktree. One writer.
- Do not read/change/delete/stage `docs/research/opencode-go-deepseek-limits.md` or initialize any `var/` database. Temporary fixture databases only.
- No type ignores, unchecked broad casts, disabling strictness, or widening public contracts to `Any` solely to suppress errors.
- Preserve no-inference replay, captured failure classification, capabilities, Artifact lifecycle/byte ownership, Grounding, disclosure, idempotency and trace fidelity.
- No authenticated/provider requests or spending. Jev live budget remains US$5 maximum for a later separately gated unit; spent/reserved US$0.
- Workers never stage/commit/push/mutate GitHub/spawn agents. Parent owns independent review and release.

## Review Focus

1. Captured channel normalization must fail explicitly if unsupported, not invent or silently replace normalized inputs.
2. Captured store methods must never read/write live files or pretend missing bytes exist.
3. Evidence-producing model dispatch must reject a missing/non-callable implementation safely, while ordinary providers keep working.
4. Recording channel capabilities must preserve configured attachment support and delivery-only fixture adapters.
5. Structural protocol must describe actual existing responsibilities, not become a generic plugin framework.

### Task 1: Correct the replay implementation contracts

**Files:** Modify `piecetogether/core.py`, `piecetogether/evaluation.py`, `piecetogether/artifact_store.py`; add only focused assertions in `tests/test_evaluation.py` / `tests/test_core.py` if needed.

**Consumes:** Current `ModelProvider.propose(inbound, contract_version, pack) -> SemanticProposal`, `ChannelPlugin` capabilities/normalize/prepare/deliver, current Artifact store methods, `_generate` optional `GenerationResult`, and existing replay capture contracts.

**Produces:** A structurally typed byte-store interface covering the methods actually called by Core; honest typed recording/captured adapters; strict typecheck success without a runtime behavior downgrade. The precise protocol name may follow the owning module conventions.

- [x] Inspect the exact thirteen diagnostics in `/tmp/opencode/limits-mypy-baseline.log`, all callers of changed contracts, and existing no-inference tests before editing.
- [x] Add narrow regression tests for unsupported captured normalization/byte access and normal/evidence-producing provider dispatch where existing tests do not already pin them. Observe failures when behavior actually changes; do not manufacture product RED for annotation fixes.
- [x] Narrow callable dispatch in `Core._generate`; annotate recording operation storage. Supply legitimate ModelProvider/ChannelPlugin members in replay wrappers and explicit unavailable failures for unsupported actions.
- [x] Define the real shared ArtifactStore behavior at `artifact_store.py`, preserve actual local implementation, and make captured/recording implementations match it explicitly. Do not suppress the concrete-class mismatch with a cast or fake subclass.
- [x] Run targeted `test_evaluation`, `test_scenario_coverage`, `test_core`, `test_artifacts`, `test_artifact_lifecycle` and relevant service/media cases; run exact strict mypy and require exit 0.
- [x] Run `python3 -m unittest discover -s tests -v` with timeout600, save full log outside repo; run `git diff --check` and inspect actual new-file diff.
- [x] Write checkpoint/report `/tmp/opencode/core-typing-result.md` with changed files/contracts, commands/results, any behavior changes, compatibility/rollback and next gate. Parent dispatches independent reviewer-premium for replay isolation/public contracts, corrects material findings, then makes a dedicated reviewed commit/push.

## Forward/rollback and stop condition

No stored data/schema, provider configuration or deployment data changes. Existing real adapters remain compatible structurally. Rollback is reverting the typing commit; new regression tests prove no capture/replay isolation or runtime contract weakened. Stop this unit when strict mypy is green, relevant/full tests pass and actual-diff review clears. Retention-v2 and Jev policies are separate bounded units of #32 and must not be smuggled into this diff.
