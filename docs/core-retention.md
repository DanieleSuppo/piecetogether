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
