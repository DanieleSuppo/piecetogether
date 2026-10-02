# Core Shell Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Execute inline on the current branch as requested by the user.

**Goal:** Finish the staged #13 Core shell and verify durable, idempotent, candidate-only interpretation.

**Architecture:** Retain the approved standalone JSON-lines process and SQLite persistence. Deterministic adapters run outside transactions; stored proposals and outbound Communications survive restart and retries.

**Tech Stack:** Python 3.10+, standard-library SQLite/JSON/unittest, mypy for development typechecking.

**Spec:** `docs/superpowers/specs/2026-10-02-core-shell-design.md`.

## Global Constraints

- Python 3.10+; no runtime dependencies or external services.
- One sequential development worker per database.
- Commit inbound Communication idempotently before model work.
- This slice has no trusted-state writer.
- Do not expose traces or secrets in sender-facing replies.
- Test only the approved Core and standalone process seams.
- Retain the staged implementation; commit on the current branch.

## File responsibilities

- `piecetogether/core.py`: bootstrap validation, typed records/adapters, durable acquisition and operator inspection.
- `piecetogether/__main__.py`: bounded JSON-lines process and operator command.
- `config/development.json`: runnable static development deployment.
- `tests/test_core.py`: durable Core outcomes, identity, envelope validation, retries.
- `tests/test_service.py`: standalone bootstrap, restart, sender response boundary.
- `README.md`: run and verification commands.

### Task 1: Verify that accepted text can produce a bounded reply

**Files:** `tests/test_core.py`, `piecetogether/core.py`.

**Interfaces:** `Core.accept(payload: Any) -> dict[str, Any]` consumes normalized input; `Core.inspect(communication_id: str) -> dict[str, Any]` exposes operator-only durable outcomes.

- [x] Add a Core-boundary regression using a 32,768-character text and assert a completed, durable candidate-only outcome.

```python
result = core.accept({**message, "text": "x" * 32768})
self.assertEqual(result["status"], "completed")
self.assertEqual(core.inspect(result["communication_id"])["trace"]["commit_result"], "not_requested")
```

- [x] Run `python3 -m unittest discover -s tests -p test_core.py -v`; reproduce the response-envelope size mismatch before changing production code.
- [x] If reproduced, keep inbound bounded at 32,768 characters and allow a bounded response of 65,536 characters to accommodate the interpretation prefix.

```python
or len(proposal.draft_response) > 65536
```

- [x] Re-run the Core test file and `uvx mypy --strict piecetogether`.

### Task 2: Preserve a durable outcome for malformed provider envelopes

**Files:** `tests/test_core.py`, `piecetogether/core.py`.

**Interfaces:** `ModelProvider.propose(inbound: Communication, contract_version: str) -> SemanticProposal`; malformed runtime values must not create an outbound interpretation or escape without a durable failure trace.

- [x] Add a provider double returning a `SemanticProposal` with a dictionary instead of a typed Candidate Claim. Assert retryable outcome, no outbound Communication, and a durable model failure trace through the Core boundary.

```python
return SemanticProposal(1, contract_version, inbound.id, ({},), "Unsafe interpretation")
```

- [x] Run `python3 -m unittest discover -s tests -p test_core.py -v` to reproduce the missing nested-envelope validation.
- [x] Validate the typed candidate collection before reading claim attributes.

```python
or not isinstance(proposal.candidate_claims, tuple)
or any(
    not isinstance(claim, CandidateClaim)
    or claim.source_communication_id != inbound.id
    or claim.status != "candidate"
    for claim in proposal.candidate_claims
)
```

- [x] Re-run the Core test file and `uvx mypy --strict piecetogether`.

### Task 3: Final verification, review, and commit

**Files:** all intended #13 files and these planning artifacts.

- [x] Run `python3 -m unittest discover -s tests -p test_service.py -v`.
- [x] Run `python3 -m unittest discover -s tests -v` once at the end and `uvx mypy --strict piecetogether`.
- [x] Invoke `/code-review` on all #13 changes relative to HEAD with issue #13 as the spec. Address substantive findings with focused TDD; re-run checks after any fixes.
- [x] Inspect `git status`, `git diff`, `git diff --cached`, and `git log --oneline -10`. Stage only intended files; verify the staged diff has no secrets and passes `git diff --cached --check`.

Commit command after staged-diff inspection: `git commit -m "feat: deliver candidate-only Core shell (#13)"`. Verify the resulting commit and worktree.

## Execution evidence

- The maximum-text regression failed with a retryable outcome before increasing the bounded response limit; the Core test file passed afterward.
- The malformed-candidate regression reproduced an uncaught `AttributeError`; explicit collection/member guards now produce a durable failure trace without exposure.
- `/code-review` reported no documented-standard violations and one verified spec defect: deeply nested JSON terminated the worker. A process-boundary test reproduced exit 1 before adding `RecursionError` to the per-line error handler, and passed afterward.
- Independent spec re-review confirmed the crash fix and no remaining blocking findings. Latest-attempt trace snapshots and whole-line size counting are deliberate for this local development slice; the standards suggestions were non-blocking local-code judgement calls.
- Final checks: 12 tests pass; `uvx mypy --strict piecetogether` reports no issues in three source files. The full suite was repeated after the review fix because production code changed.
