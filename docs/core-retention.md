# Local Core record retention

## Ordinary non-terminal expiry

Use `Core.expire_records()` for routine retention. The older terminal sweep below
is deprecated compatibility behavior, not ordinary retention. Configuration does
not schedule or execute either operation. No retention administration HTTP endpoint
exists. All examples are local operator calls; first inspect a dry-run on an
approved database. Never run examples against deployment data without authorization.

```json
{
  "database": "state/core.sqlite3",
  "identities": {"development:sender": "actor"},
  "expiry": {"trace_days": 30, "communication_days": 90}
}
```

`Bootstrap.expiry` is a `piecetogether.retention.ExpiryPolicy`. Both horizons are
integer days, 1–365000; booleans, unknown keys and non-object policies are invalid.
Omitted horizons use 30/90 days. Existing live databases need no reset or export.

```python
from datetime import datetime, timezone
from piecetogether.retention import ExpiryPolicy

reference = datetime.now(timezone.utc)
counts = core.expire_records(reference_time=reference, dry_run=True)
# After inspecting protection/erasure counts, apply the same local policy:
counts = core.expire_records(reference_time=reference)
# Optional static override, not a selector or a sender-supplied instruction:
counts = core.expire_records(reference_time=reference, policy=ExpiryPolicy(45, 120))
```

Exact interface:
`Core.expire_records(*, reference_time: datetime | None = None,
policy: ExpiryPolicy | None = None, dry_run: bool = False) -> dict[str, int]`.
`None` uses current UTC time/configured policy. Injected time must be timezone-aware
and not future-dated relative to the local clock. The aggregate report keys are
`traces_erased`, `traces_protected`, `communications_erased`,
`communications_protected`, and `obligations_pending`. The last is a non-identifying
0/1 flag for deployment-wide unfinished byte/event jobs, not a job or record count.
Erasure counts are newly removed records, not a cumulative audit log.
`traces_erased` counts a turn once only when a non-null turn trace or at least one
processing-attempt row was removed, including attempt-only copies removed with an
expired payload. An availability-marker change or Proposal-only cleanup without
those copies does not increment it. A null turn trace alone does not prove that
attempt diagnostics are absent. Dry-run executes the same plan in a rolled-back transaction;
no logical content/availability changes persist. A repeat reports zero newly erased
records. Validation failures and malformed stored timestamps/JSON roll back atomically.

Age comes from Core-owned `received_at` and the latest Core processing start/finish,
never sender `sent_at`. A recent completion of old ingress remains protected until
its processing horizon passes. Exact horizon equality qualifies. Exclusive
maintenance uses the same nonblocking `.retention.lock` fence described below;
conflicting Core/channel/sink/Artifact work raises `RetentionBusy`. No semantic DB
transaction is held during remote I/O.

Prefer the Core API. Direct module-level `retention.expire(db, policy, reference)`
requires a fully initialized Core schema and the caller's transaction and exclusive
maintenance fence, including rollback on failure/dry-run. Optional-table inventory
guards do not make a partial Core schema supported; missing required state is an
error, not silently ignored. The inventory scans retained tables and decodes JSON
inside the exclusive fence. Schedule an approved quiescent maintenance window;
deployment row counts and large-database runtime have not been certified here.

### Erased, protected and retained copies

- Settled completed/rejected turns lose their trace and all processing-attempt
  copies after the trace horizon. Optional `decision_provider`, `decision_model`,
  `decision_question_version`, `decision_raw` and `decision_fallback` fields are
  removed from settled turn and exposed Proposal copies. Semantic Proposal fields,
  pending items, correction target bindings and exposed interpretation remain.
  Artifact candidate/continuity rendering therefore still has its source Proposal.
- Unfinished turn/attempt checkpoints are protected. Pending Artifact publication
  or deletion and non-delivered event-delivery jobs conservatively protect **all**
  old traces/payloads until their owners resolve the obligations. This global block
  sets `obligations_pending=1`; it is distinct from per-record dependency protection.
  Event jobs include exhausted-budget `failed` jobs, not only runnable `pending`
  jobs. Failed delivery remains a global block until its owner explicitly calls
  `Core.recover_event_delivery(event_id)` and successfully dispatches it; age or
  repeated expiry does not recover it. Artifact jobs remain under their existing
  lifecycle owner. An outbox without an active delivery job still protects its
  referenced source, not every trace (`obligations_pending=0` can therefore coexist
  with protected records). Expiry never closes, cancels or fabricates successful jobs.
- Only completed, unreferenced development Communications qualify for payload
  expiry. Own turn/attempt/trace/Proposal/mailbox payload copies are removed together.
  Referenced trusted provenance, exposed/pending interpretations, correction
  bindings, Artifacts/ingress/stages, concepts, outbox/projection evidence, other
  retained turns/attempts (including captured context copies) protect sources.
  Exact identifier references are checked conservatively; expiry does not infer a
  semantic dependency closure. A foreign retained diagnostic copy protects its
  source rather than falsely claiming deletion. Both inbound and outbound IDs are
  checked. Email/Telegram payloads remain protected: transport handoff ownership
  is not proved closed by routine expiry. Their settled diagnostics can still age.
- Required semantic history is not deleted. Protected records can contain old
  values indefinitely. This policy is not per-person/global fact erasure, nor a
  guarantee that every value disappears at day 30/90.

Minimal permitted payload-expiry residue: `turns.id`, channel, sender,
idempotency key, completed status and empty `{}` inbound; null Proposal/outbound/
trace; and `record_expiry(communication_id TEXT PRIMARY KEY, trace_expired INTEGER,
payload_expired INTEGER, settled_at TEXT)`. The booleans have 0/1 checks; the ID
references the retained turn. `settled_at` preserves Core lifecycle timing after
trace removal. IDs and sender/key remain identifying **idempotency residue**, not
anonymization or full erasure. No arbitrary reason, counter, hash or audit payload
is persisted. Trace-only expiry retains Communication payloads and the semantic
Proposal. Exposed semantic interpretations and trusted sources remain explicitly
protected, not silently claimed erased.

`Core.inspect(id)` raises `RetentionUnavailable` for expired payloads. Ingress first
validates bounded channel/sender/idempotency identity fields, configured channel,
mapped identity and the credential guard. Only then may it look up expired
idempotency, before requiring text/sent_at, parsing attachments, comparing removed
payloads, or model work. An authorized retry raises `RetentionUnavailable` even
when only identity fields remain. Disabled channels, unmapped identities and
credential-bearing evidence receive their normal `ValueError` without revealing
expired-record existence. The development channel also checks the source
turn before delivering, so a stale in-memory outbound cannot recreate its removed
mailbox payload. A trace-only expired turn remains idempotently
retryable with its retained reply and no inference; inspection returns
`trace: {"availability": "expired"}` and `attempts: []`. A replay/capture requiring
that old trace or payload raises `ReplayUnavailable`; it never reconstructs it.
New Communications, Grounding, commits/events and retained-source capture/replay
continue after expiry. Expiry itself produces no semantic commit, revision,
Grounding Outcome or Grounded Change Event.

### Reversible view-read withholding

`Core.set_view_withheld(withheld: bool) -> None` is an explicit local operation.
`True` durably withholds derived view reads; `False` restores them **only while
legacy source/processing availability remains retained**. The additive
`view_access(singleton INTEGER PRIMARY KEY CHECK(singleton=1), withheld INTEGER)`
has a 0/1 check. Missing table/row means readable. Withholding is not erasure:
source history, events, acquisition, Grounding, references, commits, channel/sink
retries and Artifact work continue. Direct `view.current_view(db)` and Core reads
raise `RetentionUnavailable`; authenticated State API reads return HTTP 503
`{"error": "retention_unavailable"}`. Event access keeps its own authorization.
Offline new-work fixtures preserve the withheld-view availability marker.

### Additive migration, replay and rollback

Updated startup creates `record_expiry` and `view_access` idempotently; it neither
changes nor clears `retention_state`. Legacy tables missing these new tables mean
ordinary records/read access were not expired/withheld. Existing terminal rows
remain terminal and all new maintenance calls reject them. A removed semantic
revision is never recreated after a legacy source sweep. Snapshots retain both
new tables and static expiry policy; older fixtures without additive tables or
expiry configuration keep legacy defaults. Source-versus-restored and initial
isolated capture fidelity are tested directly, not only capture-versus-replay.

Ship cooperative updated processes together. Older binaries/raw SQLite writers
are not a supported mixed-version security boundary; they do not understand the
new per-record availability and must not process an expired deployment. Before
actual expiry, code rollback requires no destructive schema contraction. First
explicitly restore view reads through the local API or keep updated readers:
older code does not honor independent withholding. After expiry, rollback cannot
restore removed content; keep updated readers or stop
processing. Never restore pre-expiry backups to pretend erasure was undone.
Reversible view withholding is restored through its explicit API, not table edits.

Logical SQLite removal enables `secure_delete`; WAL/journals, filesystem/media
remnants, backups, exported fixtures, provider/caller/log copies remain outside
this operation. Artifact bytes/staging/keys are not removed. Complete controlled
local erasure and durable byte-removal orchestration are a separate R2 unit, not
implemented by this API.

## R2: controlled-local logical erasure

**Independent review of the coherent R2 diff is required before release. No real
deployment erasure is authorized by this implementation.** Ordinary expiry above
remains 30/90 days and non-terminal. Full erasure is an explicit local operator
override of ordinary Contract keep policies, not Grounding or a semantic Proposal.
There is no sender, model or HTTP erasure authority.

Local operator-only interfaces, with no sender/Proposal/HTTP route:

- **For an offline deployment, use**
  `erasure.preflight_local(database: Path, *, store_root: Path | None)` and
  `erasure.prepare_local(database: Path, *, store_root: Path | None)`, then explicitly
  `erasure.erase_local(database: Path, *, store_root: Path | None)` to remove data.
  All return `dict[str, Any]`. `erasure.status_local(database: Path)` reads persisted
  status without Core startup or schema writes. For a pre-R2a database without
  erasure metadata, run offline preflight before calling status; status does not
  initialize missing schema. Maintenance opens only an existing initialized
  database, expands private erasure schema, and uses a non-creating local store
  handle.
  They never construct Core, model, selector, channel or sink, and never run
  startup recovery. Pass the explicit configured local root, not an inferred
  path; pass `None` only for a no-store deployment. **Do not construct an ordinary
  Core just to begin erasure:** normal retained startup intentionally recovers
  pending publications/deletions.

For already-running Core handles, or handles restarted after durable preparation:

- `Core.erasure_preflight() -> dict[str, Any]`: inspect the controlled local store
  without freezing or changing runtime data/worklists. It takes the exclusive
  nonblocking database maintenance fence and existing store coordination. Normal
  startup may already have initialized schema/empty-store ownership; preflight
  itself does not attest ownership. The existing coordination lock file may be
  created if absent; no content/key file is created, published or removed.
- `Core.prepare_erasure() -> dict[str, Any]`: inspect the complete read-only inventory
  before the first freeze, then durably freeze, revalidate authoritative inventory
  outside SQLite transactions, and atomically merge pending removal jobs.
  A wrong/missing/unsupported/unverified store or inventory blocker (including stray
  root entries, nested directories, unknown names, unsafe locks or stat errors)
  returns blocked **unprepared** status
  without changing runtime state; ordinary retained processing remains usable.
  Already-prepared deployments stay frozen and keep every job on any new error.
  This explicit operator boundary may override ordinary Contract keep policies;
  no model or ordinary acceptance can invoke it. There is no unfreeze operation.
  Offline `prepare_local` uses the same ordering.
- `Core.erase_all() -> dict[str, Any]`: remove only the frozen validated local
  worklist, then separately gate runtime purge. Prefer the offline entrypoint to
  begin erasure without normal startup recovery. Ordinary store methods remain frozen.
- `Core.erasure_status() -> dict[str, Any]`: read persisted aggregate status,
  rechecking logical database emptiness for completed state. It does not inspect
  files or run recovery; completion describes the preceding verified erase, not
  a new physical-media inspection.

Every report has `scope="core_local"`,
`phase="unprepared"|"prepared"|"removing"|"files_removed"|"complete"`,
`inventory="pending"|"verified"|"blocked"`, a sanitized `blocker` or `None`,
`pending_files`, `local_logical_complete`, `global_complete=False`, **`complete=False`**,
`retryable` and `retry_after_seconds`. `local_logical_complete=True` appears only
on a successful erase receipt or persisted completed status with verified database
emptiness. Preflight and preparation always return false for both completion fields,
including after completion. Verified inventory alone never proves removal.
Preflight counts current inventory candidates; persisted status counts jobs not yet
verified absent, including unavailable jobs. Reports expose no paths, IDs, hashes,
owner tokens, free-text errors or content. Unknown stored blocker text is sanitized.
Fixed external resource groups (`providers_and_transports`, `backups_and_storage`,
`exports_logs_and_caller_copies`) always remain separately `unverified`, with
operator ownership/action. No every-copy or forensic-media guarantee exists.

### Dedicated-store evidence and safety boundary

Only the exact first-party `LocalArtifactStore` is eligible. Replay/captured,
remote, custom and subclass stores remain unsupported/unverified; no filesystem
store is fabricated to authorize erasure. A configured missing store or absent
store with known ingress/stage/publication/deletion/Artifact obligations blocks.
No-store deployments with no known byte obligations or owner evidence can prepare
and complete a zero-file local erasure; runtime purge still receives the same gate.

Updated Core startup binds an **empty** local store to one canonical database
using a random token in `artifact_store_owner` and a mode-0600, fsynced
`.core-owner.json`. The marker includes the database path, so it is identifying
restricted maintenance metadata, not successful final residue. Existing nonempty
unmarked stores are never auto-claimed, even if metadata resembles this Core.
Legacy unverified stores keep ordinary byte processing but preparation blocks.
There is **no legacy ownership-attestation/override API**. Known foreign
ownership is never overwritten; another Core cannot write/read through a local
store handle bound to that foreign owner. An unbound standalone handle honors
the marker's owner database and freeze. Before creating a database lock, byte
access validates the canonical existing owner database read-only, matching
`artifact_store_owner` token/root evidence, and any known handle database binding.
Malformed/symlinked/racing markers raise sanitized `RetentionUnavailable`, not raw
filesystem errors. Existing frozen/legacy-terminal `RetentionUnavailable` errors
retain their original fixed diagnostics rather than becoming owner-corruption errors.

A missing marker on a known bound store preserves its known database fence and
erasure-unfrozen check. A healthy unprepared database still permits ordinary
stage/read/finalize/remove operations, including after restart, with bytes preserved.
The database's recorded canonical root and safe boundary must still match. Marker
loss never authorizes erasure: preflight/prepare remain ownership-unverified, and
startup/access never restores the marker or attests/rebinds existing ownership.
There is no recovery/attestation API. Unmarked unbound legacy stores remain ordinarily
usable. The non-identifying `.core-erased` sentinel blocks bound, stale and new
unbound handles independently of database availability or owner-marker presence.
Malformed sentinels also deny ordinary bytes. New handles do not recreate missing
store directories behind a sentinel. Startup never rebinds an erased store.
Preserve `.core-erased` permanently, including after completion. After owner
metadata is purged, it is the only remaining gate for unbound handles; removing
it re-enables unbound byte writes and voids that fence.

Bound ordinary byte access intentionally shares the stable canonical owner/root
boundary with erasure validation. Root/ancestor symlinks, moved roots, or restored
mismatching root/database/token evidence deny access; no implicit rebind follows a
move, symlink change or restore. Do not relocate or replace a bound deployment's
canonical paths. This is a deliberate fail-closed boundary, not a recovery framework.
Both database/store lock opens reject symlinks. Do not share dedicated stores.

Legacy terminal retention remains a distinct boundary: Core facade operations stay
unavailable, but direct ArtifactStore bytes remain separately owned and accessible
with either present or missing markers. Only genuine R2 preparation freezes direct
byte access in both cases. The erased-store sentinel independently denies bytes
in every state, including legacy-terminal databases and absent owner markers.

Email/Telegram acquisition `run()` exits cleanly on `RetentionUnavailable`, emitting
one standard-library logging warning: `Acquisition stopped: retention unavailable.`
The message has no channel, identity, path, payload or exception text; no global
logging configuration is installed. A genuine freeze stops before polling or sleeping.
Transient `RetentionBusy` uses the
existing bounded exponential backoff; retained recovery and normal poll cadence
remain unchanged.

Inventory rejects lexical traversal, symlink ancestors/root/directories/files,
non-regular entries, hardlinked files, foreign UID entries, unknown names and
nested directories. The database cannot live inside the store. Only these files
are removal candidates, including unregistered orphans and supported partials:

- `persistent/<32 lowercase hex>`: publications/orphans;
- `staging/<32 lowercase hex>`: stages/orphans;
- `keys/<64 lowercase hex>`: durable idempotency keys;
- `staging/<32 lowercase hex>.tmp`, `keys/<32 lowercase hex>.tmp`: partials;
- `.core-owner.json`: identifying owner evidence, deferred until finalization.

`.artifact-maintenance.lock` must be an empty, regular single-link owned file, but is
coordination, not a content-removal job. Keep it in place while any old handles
could exist. Required database references must independently match the 32-hex
reference grammar; metadata cannot inject arbitrary filesystem paths. Unsafe or
unverifiable inventory produces no new partial job batch and no false success.
Missing individual files do not erase their original publication/deletion
obligations. Nothing searches or deletes arbitrary export/provider paths.

### Durable phases and compatibility-safe expansion

Private schema (all non-primary-key fields except `blocker` are NOT NULL):

```sql
artifact_store_owner(singleton PRIMARY KEY CHECK(singleton=1), token TEXT NOT NULL, root TEXT NOT NULL)
erasure_state(singleton PRIMARY KEY CHECK(singleton=1),
              phase TEXT CHECK(phase IN ('prepared','removing','files_removed','complete')),
              inventory TEXT CHECK(inventory IN ('pending','verified','blocked')), blocker TEXT)
erasure_jobs(relative_path TEXT PRIMARY KEY, kind TEXT, size INTEGER,
             device INTEGER, inode INTEGER,
             state TEXT DEFAULT 'pending' CHECK(state IN ('pending','absent','unavailable')))
erasure_control(singleton PRIMARY KEY CHECK(singleton=1),
                store_required INTEGER CHECK(store_required IN (0,1)),
                failures INTEGER CHECK(failures>=0))
```

SQLite primary-key nullability semantics still apply. Job kinds are constrained to
`publication`, `stage`, `key`, `temporary`, `owner`. Internal inventory returns
restricted relative path/kind/size/device/inode authority, never a public worklist.
The exclusive database fence precedes the store lock; filesystem inventory and
removal never span a semantic SQLite transaction. Only the documented Core and
offline operator entrypoints are supported APIs; raw helpers are internal seams.

Initialization first expands the R2a-only phase/job CHECK constraints with a
savepoint-protected SQLite table rebuild. It copies all original rows, preserving
freeze, owner evidence and original work identities; no reset or unfreeze occurs.
Unknown added columns, attached user indexes or triggers on the legacy erasure
tables block expansion instead of being silently dropped. Restart/idempotency,
outer-transaction rollback and partial-expansion rollback are tested. Inspecting
an existing database may therefore perform this compatibility-safe metadata
expansion, but never byte removal. Deploy cooperative updated processes together;
older binaries, especially unbound store handles unaware of the sentinel, are
not a supported mixed-version boundary.

Complete read-only `inspect_inventory` preflight precedes the first durable freeze,
including boundary/ownership, root allowlist, lock, nested-entry and file-stat checks.
A first blocker leaves the database, source and jobs unchanged and ordinary processing
usable; the transient blocked report is not persisted by `erasure_status`. Inspection
authorizes no removal and creates no store or owner evidence (only the existing
coordination lock may be created if absent).
Freeze then commits **before authoritative post-freeze inventory or any
irreversible byte I/O**, and that inventory revalidates all predicates under both fences.
Inventory success merges jobs in a separate short transaction. Existing jobs are never dropped, and changed
size/device/inode/kind blocks rather than rebinding removal authority. SQLite
batch failure rolls back every new job but leaves freeze durable. Storage errors
after the freeze leave sanitized durable blocked status; explicit preparation can
retry inventory before removal starts. Erase retries never adopt new files.
Already-prepared jobs/errors remain durable; no retry auto-unfreezes.
Unexpected exceptions propagate with any existing freeze retained. Existing pending
publication/deletion jobs and source metadata remain untouched. First-party
processing, reads, recovery, expiry, legacy sweeps, channel/sink I/O and direct
owned-store byte access fail closed. Startup of a prepared database does not
recover, publish, refresh references, rewrite semantic state or recreate a
missing store. Capturing prepared state and replaying a prepared snapshot fail
before inference/transport/store construction. Legacy terminal rows are neither
cleared nor inferred as completed erasure; preparation may inventory them while
keeping those fences intact. Legacy snapshots without the new tables retain old
default behavior.

### Removal, retry and separately gated completion

Schedule a quiescent maintenance window. The inventory is rescanned before every
unlink, so runtime can grow quadratically with file count. Large-store runtime
has not been benchmarked; do not assume the temporary-fixture timings apply.

The durable order is:

1. Commit preparation and its closed, verified worklist. Commit `removing` before
   creating the non-identifying sentinel. Erase retries consume existing jobs;
   they never adopt new files or rebind a job to a replacement inode.
2. Exclusively create/validate `.core-erased`, containing only
   `core-local-erased-v1\n`, and fsync both sentinel and store directory before
   **any** unlink. An existing malformed fence blocks rather than being overwritten.
3. Revalidate root, DB owner/token binding, frozen job grammar, complete directory
   allowlist and stat identity before each erasure-only anchored unlink. Open
   directory/file descriptors with no-follow and compare size/device/inode plus
   regular-file/single-link/UID predicates. A changed identity is durable
   `unavailable`, never deletion authority for its replacement. Unknown entries
   block; they are not silently claimed erased.
4. Fsync each containing directory before committing that job as `absent`.
   A crash or SQL failure after unlink-before-commit retries missing files safely,
   syncing the directory again. Ordinary stage/finalize/remove remain forbidden;
   no pending stage is published. Verify every non-owner job absent and all data,
   key and staging directories empty **before removing owner evidence last**.
5. After owner loss, resume only from previously frozen jobs, matching known
   owner/root database evidence and a valid durable sentinel. New, foreign and
   unknown stores are never adopted. Keep empty coordination locks in place.
6. Verify the final root contains exactly empty `persistent`, `staging`, `keys`,
   the empty store lock and valid sentinel, then commit `files_removed`.
7. In a separate `secure_delete=ON`, `BEGIN IMMEDIATE` runtime transaction, reject
   unknown tables, triggers, views, user-defined indexes, added/hidden columns or
   unexpected column defaults; require all jobs absent. Delete the explicit first-party runtime table set: turns, attempts,
   deliveries, semantic/reference/trusted/Grounding/pending/concept state,
   outbox/event delivery/projection records, Email/Telegram handoffs/checkpoints,
   Artifact ingress/stage/publication/deletion/lifecycle/failure metadata, R1
   expiry/view/legacy flags, owner/root/token and all erasure jobs. Write
   `complete` and independently recheck schema and **every controlled table's
   emptiness after that final write**, rolling back on any residue.

Successful database residue is only `(complete, verified, NULL)` erasure state
and non-identifying control flags (`store_required`, zero failures), plus empty
schema. No record ID, path, token, checksum, receipt payload, semantic revision,
commit or event survives. All phases, including COMPLETE, remain frozen across
Core startup, direct bytes, workers, snapshots and replay. No unfreeze exists.

Storage/permission/SQL failures retain original jobs and durable frozen status
with fixed `storage_unavailable` / `database_unavailable` classification. Consecutive
failures suggest bounded retry delays 2, 4, 8, …, 300 seconds; the caller schedules
retry, no worker or background scheduler is added. Identity/ownership/unknown-resource
blockers are not transient storage success and require investigation, not a force
flag. If SQLite cannot persist a failure, a fixed `RetentionUnavailable` error
states that the deployment remains frozen; it does not falsely promise durable
retry metadata. `RetentionBusy` is a raised, retryable coordination conflict,
not a terminal unavailable state: quiesce acquisition/Core/store activity, then
retry the same offline API. Do not restore an owner marker or clear flags to force
progress. Explicit preparation is not worklist reconciliation after removal starts.

Operator sequence (only on a separately authorized deployment): stop all writers,
use offline preflight and inspect blockers/external inventory, prepare, then call
`erase_local(database, store_root=...)`. Inspect `local_logical_complete`; if false,
retain the freeze, investigate fixed blockers and retry after the suggested delay.
Use `status_local(database)` for persisted progress. Do not call ordinary Core
construction to start or repair erasure, and do not run normal publication recovery.

`complete=False` and `global_complete=False` always remain explicit, even when
`local_logical_complete=True`. Providers/transports, backups/WAL/journals/media,
exports/logs/caller copies and unknown external sinks remain operator-owned,
separately unverified. `secure_delete` is logical SQLite cleanup, not physical or
forensic erasure. It clears pages freed by this purge, not pages already freed by
earlier non-secure deletes. No external deletion adapters or arbitrary path
traversal exist.

Before prepare, rollback can leave/remove empty additive tables without source
data loss; remove a newly created empty-store marker only through an explicit
operator-controlled migration, never auto-claim legacy bytes. After prepare,
keep updated readers/writers or stop all processes: older binaries do not honor
this new freeze. Do not clear state, downgrade active workers, restore backups or
silently resume processing. Before any future removal, preserved bytes make a
separately reviewed recovery possible, but no automated rollback/unfreeze is
provided. After removal starts, restoring old backups resurrects erased data and is not
rollback. Raw SQLite/filesystem writers, old binaries, aliases and hostile OS
writers are outside cooperative fencing. Protect local database/marker access;
use a quiescent same-version rollout. No real administrative operation occurred
in implementation or tests; tests use temporary stores/databases only.

## Deprecated terminal compatibility sweep

### Scope and irreversible behavior

`Core.apply_retention()` is an explicit, **deployment-wide, terminal maintenance
sweep**, not per-person/per-record erasure or an expiry scheduler. It uses the
static Bootstrap policy. It has no sender/admin HTTP endpoint and takes no record
IDs or free-text reason. Defaults retain all existing behavior.

Stop acquisition and other local Core work before sweeping. A conflicting model,
channel, sink, Artifact operation, or Core read causes `RetentionBusy`; nothing is
erased. Coordination uses POSIX advisory locks on the resolved database path plus
`.retention.lock`. Keep that file in place. All processes must run this version,
use the same database path on a local filesystem, and use the Core/first-party
channel and projection sink interfaces. Direct SQLite writers, older binaries, hard-link aliases and
external processes that bypass these interfaces are not coordinated.

Any successfully applied non-retain setting durably disables further processing,
recovery, reference refresh, event dispatch, Artifact publication/cleanup and
channel handoff/checkpoint writes. These operations raise `RetentionUnavailable`,
including on old Core instances and after restart with a retain configuration.
This deliberately rejects unsafe continuation rather than fabricating missing
history or replaying incomplete checkpoints. Resume service only with a **fresh,
separate deployment/database**, with explicit decisions about old transport
messages and Artifact storage. Do not clear `retention_state` or restore a backup
into the erased deployment to resume it.

## Configuration and local call

```json
{
  "database": "state/core.sqlite3",
  "identities": {"development:sender": "actor"},
  "retention": {
    "communications": "retain",
    "semantic_history": "retain",
    "evaluation_traces": "delete",
    "current_view": "retain"
  }
}
```

**Any non-`retain` setting makes the whole deployment terminal after the sweep,
including a view-only policy. `redact` erases category content too; it does not
keep masked records.** Resume only with a fresh, separate deployment/database.

Each category independently accepts `retain`, `redact`, or `delete`. Unknown keys,
invalid values and non-object policies are rejected. Omitted settings retain.
Programmatic Bootstrap configuration uses `piecetogether.retention.RetentionPolicy`.
Configuration alone does not run a sweep.

```python
from piecetogether.core import Bootstrap, Core

core = Core(Bootstrap.from_file(config_path))
effective_availability = core.apply_retention()
```

Load destructive configuration on a quiescent deployment. Constructing a Core
with any non-`retain` policy initializes schema but skips checkpoint recovery,
Artifact recovery/publication, reference refresh and event dispatch before the
first sweep. Default retain startup keeps its existing recovery behavior unless
durable availability already records a sweep. After a non-retain sweep, startup
skips recovery even when configured to retain.
The return value maps all four category names to effective actions. Repeated
sweeps are idempotent. `delete` outranks `redact`, which outranks `retain`; later
configuration never restores erased evidence or weakens durable availability.

## Independent settings and dependent invalidations

| Requested category | Effect of `redact` or `delete` |
| --- | --- |
| Communications | Erases all Communications and dependent trusted semantic history, traces, pending exposure, local delivery/acquisition/handoff copies, concepts, outbox/delivery bookkeeping and Artifact metadata. Invalidates Current Trusted View. |
| Trusted semantic history | Erases the ledger, reference assertions/commits, Artifact metadata, events and dependent Communication/working/trace/delivery copies. Invalidates Current Trusted View. |
| Evaluation Traces | Erases both turn and attempt traces, attempt records, turn Proposal checkpoints and pending exposure Proposals/items/bindings, which can contain decision diagnostics. Retains normalized inbound/outbound Communications, trusted history, events and derived view. Does not resolve pending items or change committed Grounding outcomes. |
| Current Trusted View | Disables projection reads/derivation. Retains the independently configured source ledger, Communications and traces. No cached projection exists to erase. Source history remains operator-readable; it is not a substitute State API response. |

Communications and semantic history conservatively invalidate each other: source
provenance, exposed interpretations, later confirmations and Context Packs share
values and identifiers. This whole-deployment operation does not attempt to
classify individual dependency closures. A source-category action raises every
dependent category to at least that action, including when their own setting is
`retain`. Traces-only and view-only actions do not erase the trusted ledger.
Retained categories can independently contain the same facts as erased categories;
this is not a claim of global fact/subject erasure.

## Residue, reads and transaction

Both redaction and deletion remove category content. This conservative redaction
implementation keeps **only non-identifying category availability**, not partially
masked record payloads. Redaction and deletion differ by the durable action marker;
neither keeps per-record tombstones, identifiers, timestamps, counts, provenance
or arbitrary reasons for erased records. Database schema and the deployment lock
file remain. Static configuration/identity mappings are not runtime records and
are not edited by maintenance.

`retention_state(category TEXT PRIMARY KEY, availability TEXT NOT NULL)` stores
only non-retain rows. Category names are `communications`, `semantic_history`,
`evaluation_traces`, `current_view`; availability is `redact` or `delete`. Missing
rows/table mean `retain` for legacy/default databases. `retention.availability(db)`
returns all four effective values. Erasure and these rows commit in one SQLite
`BEGIN IMMEDIATE` transaction. Failure rolls back both. No semantic commit,
revision increment, Grounding Outcome, acceptance or Grounded Change Event is
created. Complete source erasure removes even the old semantic revision row;
startup does not recreate it.

Erased Communications/history/events/view reads raise `RetentionUnavailable`, not
an empty apparently valid history/view. `history.inspect_history(db)` and
`view.current_view(db)` also enforce availability. Trace-only erasure keeps
`Core.inspect(id)` usable for retained Communications, returning `trace` as
`{"availability": "redact"}` or `{"availability": "delete"}`, `attempts: []`, and
no Proposal checkpoint. Authenticated affected State API/event requests return
HTTP 503 `{"error": "retention_unavailable"}`. JSONL acquisition returns that
error too; local CLI inspection reports `Communication unavailable.`

## Artifact bytes and copies outside Core

ArtifactStore byte ownership and Contract byte-retention rules are unchanged.
The sweep does **not** remove bytes, staging files, immutable store keys, remote
attachments, provider messages, external projections, backups, exported replay
fixtures, logs, or copies already returned to callers. Source erasure removes
Core Artifact metadata and stops recovery, so existing byte references/jobs cannot
be recovered through that database. Complete required Artifact publication/deletion
work **before** source erasure, using the existing Artifact lifecycle and its
Contract policy. Source erasure rejects with `RetentionBusy` while any pending
publication or deletion job exists; it never discards a byte-removal obligation.
Plan separately for residual byte storage and its keys. Do not
claim this sweep deletes Artifact bytes or external copies.

SQLite `secure_delete` is enabled for the sweep, but no physical-media sanitization
promise covers WAL/journals, snapshots, filesystem remnants or storage backups.
Operators must separately apply approved retention to those copies and to any
exported replay fixtures. [Operator replay](evaluation-replay.md) preserves the
retention schema, durable availability and static policy in snapshots. Capture and
replay reject any applied non-retain category with `ReplayUnavailable` before
startup recovery; neither reconstructs erased evidence nor substitutes provider
inference. Configured but unapplied policy remains replayable when required
evidence is retained. This protection does not modify or erase pre-existing
exports: operators remain responsible for those copies and backups.
