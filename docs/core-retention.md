# Local Core record retention

## Scope and irreversible behavior

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
