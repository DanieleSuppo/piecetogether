# Operational retention and controlled complete erasure — approved technical direction for #32

The operator delegated policy decisions and execution. These are agent-selected engineering policies, not claims that the operator specified the exact durations or API names. No real deployment data is erased during this work.

## Goal and boundary

Ordinary retention must not disable Core processing. Complete application-data erasure is a separate explicit operator operation, allowed to stop the deployment intentionally. Preserve all load-bearing semantic evidence until its dependent trusted/pending state is deliberately erased. Never label absence as a Grounding Outcome or invent missing replay evidence.

No per-person dependency-closure framework, scheduler service, dynamic plugin platform or generic privacy system. Local maintenance APIs/CLI and existing SQLite/Artifact lifecycle are sufficient.

## Selected policies

1. **Operational trace default:** 30 days. Expire settled turn/attempt diagnostic and model-decision copies, with explicit per-turn trace-unavailable status. New Communications, inference, Grounding, trusted commits/events and replay of newly captured work continue normally.
2. **Unreferenced Communication default:** 90 days. Expire only completed input/output payloads proved irrelevant to live trusted provenance, unresolved Grounding, operational retries, Artifact jobs, transport handoffs or event delivery. If dependency safety cannot be established, retain and report protection instead of guessing a closure. Preserve minimal explicit idempotency/lifecycle evidence where policy permits; an expired Communication read/retry is unavailable, never a reconstructed payload.
3. **Trusted history and required source evidence:** retain while referenced by live trusted/pending state. There is no routine automatic deletion of live claims, relationships or provenance. An expiry report distinguishes erased from dependency-protected records; the age horizon is not a false promise that every old value disappears.
4. **Current Trusted View:** remains derived from available retained history. Routine expiry does not shut it down. A view-only withholding policy must not prevent unrelated processing. Missing source history still makes the affected projection explicitly unavailable.
5. **Full erasure:** explicit local `Core.erase_all(...)`-style maintenance, separate from ordinary expiry and semantic proposals. Deletes all Core runtime data and associated Core-controlled local Artifact bytes, staging data and idempotency keys. This operation is intentionally terminal for the erased deployment; it is not used for routine retention. It does not silently clear an older terminal fence or resurrect a previously erased database.
6. **Scope honesty:** report `core_local` erasure separately from external/provider/backup/export copies. No global-complete claim unless all declared required resources are verified removed. Unknown or inaccessible copies remain unverified/pending with an owner/action, never implicitly complete. Do not automatically delete third-party/provider source messages or unknown filesystem paths.
7. **Physical scope:** logical removal and supported local SQLite/file cleanup, with explicit WAL/journal/backup/storage constraints; no impossible forensic media-sanitization guarantee.

## Ordinary-retention owner and invariants

Use retention.py/Core as the existing owner. An explicit local `expire_records`/equivalent operation accepts an injected reference time or validated cutoffs, applies the above default horizons, and returns aggregate erased/protected/unavailable counts. Clock/cutoff validation must reject naive/invalid/future-dangerous input without changing data. No free-form SQL selectors or sender-facing admin endpoint.

Expiry uses the same exclusive maintenance fence as current sweeps and short SQLite transactions. If an in-flight model/channel/sink/Artifact action conflicts, reject `RetentionBusy` rather than race. Core processing does not hold a semantic DB lock during remote I/O.

`turns.trace` and processing_attempts are operational evidence; exposed Grounding proposals are load-bearing Working State. Preserve the minimal semantic interpretation and target bindings required by unresolved items. Remove optional provider/question/raw-decision diagnostics from redundant completed Proposal copies when safely possible; do not destroy pending correction targets or delivery/retry checkpoints. Never call model/channel to replace removed evidence. Existing settled idempotent responses must not accidentally re-enter interpretation after their evidence expires.

Per-record expiry availability must distinguish old expired evidence from new valid evidence. Do not mark the whole deployment permanently unavailable for an ordinary trace expiry. A capture from retained live state after expiry may replay new work; replay/read requiring expired old evidence fails explicitly. Preserve new availability metadata/config in source-faithful snapshots and test directly against source.

## Compatibility and legacy terminal state

Existing successful terminal sweeps remain irreversible/fail-closed. Missing legacy retention table means default retain, as before. Do not automatically reinterpret historical `retention_state` rows as harmless expiry or recreate a removed semantic revision. New ordinary retention uses an unambiguous new state/API/schema path, or an explicit versioned migration that preserves those guarantees. Document the supported same-version rollout; old raw writers/old binaries remain outside cooperative fencing. Do not claim arbitrary mixed-version erasure safety.

Forward migration is additive/idempotent, with a non-destructive inspection/dry-run path. Rollback before erasure restores prior code/state; erased content cannot be rolled back by resurrecting backups. Old databases containing complete source erasure remain unavailable; retained live databases can use ordinary expiry without a fresh deployment.

## Full-erasure durable phases and Artifact ownership

- Freeze the deployment durably under the exclusive maintenance fence before any irreversible byte removal. All updated Core/channel/sink operations and startup recovery check this phase.
- Record only the work needed to remove controlled bytes/copies in a durable pending worklist before discarding source metadata. Do not discard pending publication/deletion obligations. An operation may cancel pending publication for full erasure; it must never publish bytes merely to erase them later.
- Perform byte removal outside a semantic transaction using the existing store maintenance lock and idempotent removal. Retry after restart uses the same durable jobs. Missing file is success; permission/storage failure is pending and observable, not complete.
- Remove every controlled publication, stage and durable key, including supported partial/orphan files, only within a validated dedicated Core store boundary. No broad rm -rf, symlink traversal, deletion of another deployment's shared store, arbitrary path injection or secret disclosure. If exclusive store ownership cannot be established, block/report that resource instead of claiming completion.
- Ordinary Contract keep policies are not legal holds. A separately authorized full-deployment erase may remove deployment-owned runtime data; no model/Proposal can invoke this administrative override. Explicitly document the boundary and never perform it on real user data in this implementation task.
- Finalize runtime-data/identifier/provenance erasure only after required local removals succeed. Successful final residue is non-identifying erasure/availability state, not IDs, checksums, free-text failure messages or old job references. A partially failed operation may retain necessary restricted work metadata until retry completes and must say so.
- Report external/provider messages, remote sinks, backups, exported replay fixtures, logs and caller copies separately. Provide a finite operator inventory/receipt workflow, not a universal deletion-adapter framework. A local-complete result must never be described as every-copy/global erasure.

## Proof / stop conditions

1. Expire old settled traces, accept and ground a new turn, restart and retry without new inference/duplicate commit. Trusted history/view/events unchanged by expiry itself.
2. Pending Grounding/correction plus failed delivery survive expiry safely or are explicitly protected; removed evidence is not silently reconstructed.
3. Unreferenced Communication expiry removes all controlled payload copies; referenced/pending/job-dependent records are protected with explicit counts; expired reads/retries unavailable.
4. View-only withholding does not terminally stop processing; live source projection remains evidence-preserving and source deletion stays unavailable.
5. Real source-versus-snapshot comparisons after expiry; replay new captured work succeeds, replay old missing evidence fails without inference.
6. Full erasure with real temporary Artifact bytes/stages/keys and pending jobs removes all verified controlled files and runtime evidence; injected byte-removal failure survives restart and retries idempotently; no event/Grounding/semantic acceptance generated by erasure.
7. Malicious/shared/symlink store path or external-unknown resource does not receive a false complete result or delete unrelated data.
8. Legacy terminal DB and default DB migration/restart/rollback matrices remain explicit and fail closed.
9. Strict mypy green after typing unit; focused/full unittest timeout600; independent privacy/concurrency/destructive-boundary review of actual diff/new files. Only relevant reviewed commits pushed without force.

## Bounded implementation units

- Unit R1: ordinary non-terminal expiry, dependency protection, availability, migration/replay and focused tests/docs. No full-erasure byte orchestration yet.
- Unit R2: durable complete-local-erasure with byte/key removal, retry/receipt/inventory and focused/full tests/docs. Consumes R1's actual interfaces, does not redesign it.
- Parent reviews each unit before the next. Jev billing/adoption is separate; no paid requests during retention work.
