# Operational retention and controlled erasure implementation plan

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans. Operator delegated policy decisions and execution. Current main, one writer, no new branch/worktree. Each unit checkpoints and receives independent privacy/concurrency review before the next mutation unit.

**Goal:** Make routine retention compatible with ongoing Core operation, and provide verifiable complete erasure of controlled local data without claiming deletion of unknown external copies.

**Architecture:** Extend the existing retention/Core owner, preserve required semantic evidence, add explicit per-record expiry and independent view withholding rather than weaken legacy terminal fences. Complete erasure uses durable freeze → byte-removal jobs → runtime purge → non-identifying completion. Reuse existing Artifact store and replay seams.

**Tech Stack:** Python standard library, SQLite, existing local ArtifactStore, POSIX maintenance locks, unittest.

**Spec:** `docs/superpowers/specs/2026-10-22-operational-retention-design.md`, user-authorized #32. Typing prerequisite is `docs/superpowers/plans/2026-10-22-core-typing.md` and its actual report `/tmp/opencode/core-typing-result.md`.

## Global Constraints

- Default ordinary horizons: settled operational traces **30 days**, unreferenced completed Communication payloads **90 days**. Keep trusted/pending/retry/Artifact evidence until no longer required. Protect rather than guess a dependency closure.
- Erasure is not Grounding, semantic acceptance, revision-producing knowledge or a Grounded Change Event.
- Never erase deployment data/configuration during implementation. All proof uses temporary databases/stores. Do not initialize `var/` databases.
- Preserve `docs/research/opencode-go-deepseek-limits.md` unchanged and unstaged. No branch/worktree; one writer.
- No generic privacy framework, scheduler service, benchmark framework, dependency, CI or hook work. Explicit local maintenance is sufficient.
- Ordinary expiry must not clear old terminal retention rows or resurrect erased history. New state must have an unambiguous compatibility path.
- Strict mypy must stay green after typing prerequisite. Workers never stage/commit/push/GitHub/delegate or call paid providers. Jev spent/reserved remains US$0 in these units.

## Review Focus

1. A completed exposed interpretation may still be necessary for future correction; trace expiry cannot discard its semantic proposal/target bindings.
2. New ingress must check expired idempotency/availability before parsing removed payloads or generating a new interpretation.
3. Every controlled duplicate is either erased or explicitly protected; no successful expiry hides leaked decision payloads in redundant proposals/attempts/deliveries.
4. Removing source bytes cannot precede a durable worklist/freeze; partial removal cannot be reported complete or undone by startup publication.
5. Store ownership/path/symlink checks must prevent erasing shared/unrelated data; provider/backup/export status cannot become globally complete by omission.

### Task R1: Ordinary non-terminal retention

**Files:** `piecetogether/retention.py`, `core.py`, `history.py`/`view.py`/`application_api.py` only as needed at owning boundaries, `evaluation.py`; focused `tests/test_retention_expiry.py` and nearest replay tests; operator docs.

**Interfaces:** Define a validated `ExpiryPolicy` (or equally local name) in retention owner for the selected 30/90-day horizons. Attach static policy through Bootstrap using existing parsing conventions. Add local `Core.expire_records(...) -> dict[str, int]` with injected timezone-aware reference time, optional dry-run and aggregate erased/protected counts. Add an explicit local reversible view-read withholding operation if needed; it must not block processing while source remains intact. Exact parameter spelling may follow existing conventions, but document it and hand it off before completion. This is a coherent existing-state seam, not a new administration platform.

- [x] Read actual source bodies (recover compressed handles if necessary), all relevant callers and tests. Analyst report `/tmp/opencode/retention-v2-analysis.md` contains explicitly unverified source-body claims; do not mistake those for proof or binding design.
- [x] Add focused failing behavioral tests: expire old settled traces, keep processing and Grounding, restart/idempotent retry, pending/correction/failed-delivery protection, expired evidence unavailable, view withholding without processing halt, source/snapshot fidelity and offline replay after expiry.
- [x] Implement additive idempotent expiry metadata/availability. Use Core-owned `received_at`/processing time, not attacker-controlled sent_at, to determine age. Validate policy/time inputs, reject malformed or naive timestamps, and do not hide active records behind future cutoffs.
- [x] Expire old settled trace/attempt copies; preserve necessary exposed semantic interpretation and pending bindings. Strip optional captured provider/decision diagnostics from redundant settled Proposal copies where safe. Protect operational retry checkpoints instead of replaying with incomplete evidence.
- [x] Expire old completed Communication payloads only when actual trusted/pending/Artifact/retry/outbox/transport dependencies do not require them. Keep only documented minimal idempotency/lifecycle residue, ensure explicit unavailable reads/retries, remove local payload duplicates. If uncertain, count as protected. Do not delete live trusted history as a shortcut.
- [x] New records and commits remain operational; old legacy terminal databases remain fenced. Preserve new state/config in SQLite snapshots. Compare restored snapshot and initial isolated capture directly with source; replay new work after expiry, reject old required evidence without inference.
- [x] Ensure independently withheld derived view returns an explicit unavailable response while retained-source processing continues. Restoring view access must never bypass an erased-source fence or invent history.
- [x] Run targeted expiry/retention/evaluation/service gates and exact strict mypy. Run full unittest discover -s tests -v with timeout600, then diff-check. Checkpoint `/tmp/opencode/retention-r1-result.md` with interfaces/schema, migration/rollback, files, exact tests/results and R2 handoff. Stop for parent independent actual-diff review before R2.

### Task R2: Complete controlled local erasure

**Consumes:** R1's actual availability/schema and typing-unit ArtifactStore contracts, existing local store lifecycle methods.
**Produces:** Explicit local `Core.erase_all(...)`-style operator operation, observable durable pending-removal work and honest scope-qualified erasure receipt; no sender/admin HTTP endpoint or global-copy guarantee.

Execute R2 as two serial, independently reviewable subunits to bound worker context/runtime. **R2a** owns validated dedicated-store inventory, durable freeze/worklist schema and compatibility/recovery gates; it performs no byte removal and makes no complete-erasure claim. **R2b** consumes that exact handoff, owns idempotent removal/finalization/receipt and end-to-end proof. Do not release an incomplete R2a erasure feature to production; commit/push the coherent R2 implementation only after R2b and final review pass.

- [x] Pin temporary-store tests for publication/staging/keys/partial/orphan files, existing pending publication/deletion, restart/retry, missing files, injected storage failure and unsafe/shared/symlink boundaries.
- [x] Persist erasure freeze and removal inventory before I/O. Reject/fence all updated first-party writes/reads/recovery that could leak or recreate erased data; no publication on erase startup.
- [x] Remove only validated dedicated Core-controlled byte/key resources idempotently under existing store coordination, outside semantic DB transactions. Preserve pending work on failure with sanitized classification. Do not discard obligations or claim success prematurely.
- [x] After every required controlled removal succeeds, purge all runtime identifiers/content/provenance/jobs, leaving only non-identifying completion/availability state. No semantic commit/event/Grounding acceptance. Old terminal residue is not auto-cleared.
- [x] Receipt distinguishes local completion from external/provider/backup/export/log/caller copies. Provide finite inventory and owner/action/verification status; unknown required resources remain pending/unverified. Never unlink arbitrary export paths or silently delete third-party messages.
- [x] Document explicit operator override versus ordinary Contract keep policy, dedicated-store requirements, no real execution, irreversible rollback limits and physical-storage exclusions.
- [x] Targeted tests, full unittest timeout600, strict mypy green and diff-check; checkpoint `/tmp/opencode/retention-r2-result.md`. Independent destructive/privacy/concurrency review of the actual coherent diff and new files passed. Dedicated commit/non-force push delivery is tracked in #32; this checkbox does not authorize production erasure.

R2 checkpoint: R2a fix2 scoped independent review and the coherent full-R2
privacy/destructive-boundary/concurrency review passed. Final proof: 35 new
erasure/migration tests, 73 erasure/preparation tests, 455 full tests in 150.707s,
strict mypy GREEN19, and clean whitespace. The earlier nearest gate passed 269
tests before the final old-schema status correction. Final full unittest output
is complete and says OK; the worker timeout lost the wrapper exit/wall receipt,
so no captured exit code is claimed. Recovery finalized artifacts without source
changes or repeated tests. Exact offline APIs are
`erasure.erase_local(database, *, store_root)` and `erasure.status_local(database)`;
`Core.erase_all()` extends the existing operator helpers. R2a CHECK expansion was
implemented and tested first. Sentinel → verified non-owner absence → owner-last
→ separately gated runtime purge is implemented. The author report is
`/tmp/opencode/retention-r2b-result.186c8af6.md`, full log is
`/tmp/opencode/retention-r2b-full-tests.log`, and independent review is
`/tmp/opencode/retention-r2-release-review.md`. Minor review notes were addressed
through operator documentation: retain the load-bearing sentinel, distinguish
prior freed-page residue, schedule large-store maintenance, and initialize old
metadata through offline preflight before read-only status. No production erasure
has occurred or is authorized here. External/physical copies remain unverified.

## Migration and release boundary

No automatic legacy-data reset. Additive schema initialization/dry-run is safe to repeat. Before irreversible erasure, rollback may revert code and preserve original data. After actual erasure, restoration of pre-erasure backups is not rollback: it resurrects removed data and is not performed. Deploy updated cooperative processes together; raw writers and old binaries are not a claimed security boundary.

R1 implementation and independent review (including scoped correction re-review) passed: 382 full tests with timeout600, 85 focused, 22 expiry cases and strict mypy green. Corrected authorization-before-expiry lookup, explicit global-obligation reporting, real diagnostic-copy counters and actual ContextPack cross-run copy proof. R1 delivery precedes R2; no complete erasure has been executed on deployment data.

Stop after R1/R2 requested behaviors, focused/full proof and independent review pass. Jev remains a separate budget/privacy/adoption unit; missing credentials do not justify blocking safe typing/retention delivery or pretending production adoption succeeded.
