# Artifact lifecycle (#20)

Authority: #20, SPEC #12 Artifact lifecycle, `docs/PRD.md` sections 11/15,
`GLOSSARY.md`, and existing Artifact Contract policy. Execute autonomously.

## Scope and decisions

Reuse `ArtifactOperation` and the SQLite semantic checkpoint. Supersede names a
new inbound attachment and an Actor-owned, same-type predecessor; it publishes
new bytes and appends a Core-owned `supersedes` relationship without modifying
the predecessor. Delete names an existing Actor-owned Artifact and requires a
bounded reason. The active Contract supplies retention independently for
metadata, bytes and provenance. Lifecycle actions are semantic-commit-only.

Deletion immediately fences read/publication, appends a policy/reason/time
transition where retainable, and schedules idempotent post-commit byte removal.
Retained bytes are not accessible as deleted content. Metadata/provenance purge
is an explicit deletion exception to append-only semantic history. All-delete
removes the Artifact tombstone and lifecycle/provenance residue after removal;
Communication, claim, commit and operational-trace retention remain separate
policies outside this ticket. No provider/channel URL or model-chosen store ref.

## Tasks

- [x] 1. Supersession: Core-boundary red/green tests, validation, immutable new
  Artifact and relationship, publication/retry, currentness projection.
- [x] 2. Deletion: Core-boundary red/green tests, reason/policy/ownership checks,
  independent retention, atomic unavailable transition and durable byte cleanup.
  Cover pending publication, failures/restart/retry, stale and rollback cases.
- [x] 3. Update README, full unittest suite, strict mypy, diff check, independent
  lifecycle/security/concurrency review; fix important findings, commit locally.

## Change points

`proposals.py`: typed intent and structural policy validation.
`history.py`: state/ownership validation, atomic lifecycle/relationship/outbox.
`artifact_store.py`: idempotent remove of both staged and persistent bytes.
`core.py`: post-commit cleanup/recovery and read/finalization fencing.
`view.py`, `context.py`: unavailable/deleted and superseded projections.
`tests/test_artifacts.py`, new lifecycle scenarios, existing validation fixtures.

## Verification / ledger

- Baseline: 160 unittest tests passed on e8ab02f.
- Task 1: Core supersession scenario failed on rejection, then passed with
  immutable predecessor, new content and relationship. Existing validation
  reason ordering preserved; focused checks and strict mypy passed.
- Task 2: deletion API/policy scenarios failed, then all eight retention
  combinations, ownership/reason rejection, stale/outbox rollback, storage
  outage/restart and pending-publication deletion passed. Operational failure
  inspection regression also verified red/green. Twelve focused tests now pass,
  including publication/deletion concurrency and final-review regressions.
- Pre-flight: Task 2 consumes Task 1 publication state and projection; deletion
  fences publication before post-commit removal, with the existing local store
  maintenance lock. Artifact mutation and outbox share the Core checkpoint.
- Ruling: no ticket-specific local plan existed; this checklist records the
  direct implementation of #20/SPEC #12, without expanding product scope.
- Ruling: use a local feature branch in the existing checkout, avoiding extra
  worktree/config changes. No push, merge or issue closure is part of execution.
  Cost if wrong: integration remains a separate operator action.

## Final review and disposition

Independent review: `reviewer-premium`, `ollama-cloud/minimax-m3`, run
`f64e3c06-14e1-43f1-a59b-b04fd481e9bb`. Original report:
`/tmp/piecetogether-20-review.md`. Reviewer labels/verdict were contradictory;
concrete findings were independently reproduced and graded by effect.

- Final: fixed I4, relational provenance residue — matrix regression RED→GREEN;
  purge source columns as well as JSON, ingress and stage provenance.
- Ruling: use `('', artifact_id)` as the retained row's opaque unique key.
  Setting both columns empty (review suggestion) violates their UNIQUE pair
  on a second deletion. Cost if wrong: consumers bypassing Core must understand
  these are sanitized tombstone keys, never source Communication references.
- Final: fixed I5 — a new accepted claim cannot use an Artifact being deleted
  in the same commit; exact Core scenario RED→GREEN, no trusted side effects.
- Ruling: this rejection applies to newly committed derivations, not existing
  Grounded Claims. Their immutable provenance and retention remain separate.
  Cost if wrong: a producer combining grounding and deletion must split turns.
- Final: fixed rejected-batch staging reservation — Core scenario RED→GREEN.
  Contrary to the review's rollback claim, a deterministic rejected return
  does not raise/roll back the checkpoint transaction. Reserve stages only
  after all semantic rejection checks, preventing unreapable linked orphans.
- Ruling: I1's crash repro is not supported by atomic `os.replace` and the
  post-finalization state update. Do not add automatic startup orphan deletion.
  Cost if wrong: unusual legacy staging residue still needs operator cleanup.
- Ruling: I2 is invalid: `current=False, available=True` explicitly represents
  superseded content; `availability='deleted', available=False` distinguishes
  deletion. Cost if wrong: a future consumer may need another projection field.
- Final: minor (deferred): I3 future-caller transaction comment; current sole
  caller rechecks and mutates within the same `BEGIN IMMEDIATE` transaction.
- Final: minor (deferred): M1 adapter-reference docstring polish; local removal
  already rejects invalid paths explicitly (it does not silently drop them).
- Final: minor (deferred): M2 projection/context query optimization for large
  histories; no materialized projection or new indexing added to this ticket.
- Final: minor (deferred): M3 redundant pending-job state is deliberately explicit.
- Ruling: M4's operator-only claim is inaccurate: lifecycle reason is also in
  Core `current_view()`, but not automatically sender-disclosed. No false README
  guarantee added. Cost if wrong: future State API must retain its auth boundary.
- Ruling: M5 availability in bounded model context is intentional, permitting
  informed lifecycle intent without exposing bytes. Cost if wrong: adapter
  schemas must tolerate the added operational field.
- Final: minor (deferred): M6 existing repository lint debt, unchanged in scope.
- Ruling: declined domain-policy selection belongs to the Contract owner;
  Communication/claim/commit/trace retention remains separate, as documented.
  Cost if wrong: Artifact deletion alone is not deployment-wide privacy erasure.
- Ruling: verify the local development store/profile, not unsupported injected
  backends or multiprocess load. SQLite/local maintenance locking remains in
  place; production concurrency is #22. Cost if wrong: deployments outside
  this supported profile need adapter conformance and load verification.

Final verification: `python3 -m unittest discover -s tests -q` — 172 tests
passed; `uvx mypy --strict piecetogether` — all 11 source files passed;
`git diff --check` clean. Keep the local feature branch; do not push or merge.
