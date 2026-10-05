# Atomic Trusted History Implementation Plan

> **For agentic workers:** Execute inline using `executing-plans`, task by task.

**Goal:** Satisfy #15 through atomic authorized semantic commits and reusable
non-authoritative Emergent Concepts.

**Architecture:** Extend Core's existing SQLite checkpoint with a history
module. Validate source proposals and Core-owned exposure/evidence within a
short transaction; preserve history and durable retry outcomes.

**Tech Stack:** Python 3.10+, SQLite, standard-library unittest, strict mypy.

**Spec:** `docs/superpowers/specs/2026-10-05-trusted-history-design.md` and #12/#15.

## Global Constraints

- One Core deployment serves one application deployment.
- LLMs produce proposals; deterministic Core logic alone commits trusted state.
- Silence and failed/indeterminate exposure cannot authorize grounding.
- No semantic locks during model, channel or remote storage work.
- Immutable history, distinct provenance, non-authoritative Emergent Concepts.
- User authorized autonomous decisions and execution on the current branch.

## Task 1: Atomic authorization tracer bullet

Files: `piecetogether/history.py` (records/transactions),
`piecetogether/core.py` (checkpoint integration/operator seams),
`piecetogether/proposals.py` (revision, independent resolution targets,
state-aware validation), `tests/test_history.py` (Core-boundary scenarios).

Interfaces: `SemanticProposal.semantic_revision: int | None`,
`GroundingPlanOperation.resolution_ids: tuple[str, ...]`,
`GroundingResolutionOperation.rationale: str | None`,
`Core.inspect_history() -> dict[str, Any]`,
`Core.inspect_concepts() -> list[dict[str, Any]]`.

- [x] Write a failing Core scenario exposing Entity, Context and Claim items,
  then accepting them with a later sender Communication.
- [x] Run `python3 -m unittest discover -s tests -p test_history.py -v` (red).
- [x] Persist Core-owned exposures after accepted handoff; validate commit
  evidence/dependencies and append history plus outbox in the checkpoint.
- [x] Run the focused file (green), then `uvx mypy --strict piecetogether`.

Expected key assertions:
```python
self.assertEqual(history['revision'], 1)
self.assertEqual(history['claims'][0]['value'], 3)
self.assertEqual(history['grounding_items'][0]['outcome'], 'accepted')
self.assertEqual(len(history['commits']), 1)
self.assertEqual(history['events'][0]['semantic_commit_id'],
                 history['commits'][0]['id'])
```

## Task 2: History, authorization and failure invariants

Files: same production files and `tests/test_history.py`.
Consumes: Task 1 interfaces. Produces: history-preserving lineage and durable
commit retry with active-Contract and revision fencing.

- [x] For each behavior, write and run Core scenarios: direct
  writes, fabricated evidence, failed exposure, unaccepted dependencies,
  stale revisions, correction/supersession/contradiction, storage rollback,
  retry after channel failure/restart and active Contract changes.
- [x] Add only the validation/persistence logic needed by that scenario.
- [x] Repeat focused tests and strict mypy at meaningful checkpoints.

Expected rollback/staleness assertions:
```python
self.assertEqual(core.inspect_history(), before)
self.assertEqual(result['status'], 'reprocess_required')
self.assertEqual(core.inspect(result['communication_id'])['trace']
                 ['validation_result'], 'stale')
```

## Task 3: Emergent Concept persistence and finish

Files: `piecetogether/history.py`, `piecetogether/core.py`,
`piecetogether/proposals.py`, `tests/test_history.py`, `README.md`.
Consumes: existing active Contract policy and candidate validation.
Produces: durable reusable non-authoritative catalogue with provenance.

- [x] Write a failing declaration/restart/reuse scenario; require the reused
  name to validate without another declaration and remain non-authoritative.
- [x] Persist accepted declarations and feed persisted names to validation.
- [x] Verify disabled emergence and canonical constraints still reject reuse.
- [x] Update README with the writer, operator seams and remaining ticket scope.
- [x] Run `python3 -m unittest discover -s tests -v` and strict mypy.
- [x] Use `code-review` against the fixed starting commit `4a0129c`, address
  findings, rerun checks justified by resulting changes.
- Final operation: inspect status/diff/log, stage intended files and commit on `main`.

## Verification result

75 tests passed; strict mypy passed for all six source files. The independent
Standards review found no documented breaches. The independent Spec review's
exposure-binding and committed-checkpoint recovery findings were reproduced,
fixed with red/green regressions, and confirmed resolved in targeted re-review.
