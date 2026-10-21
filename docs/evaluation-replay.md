# Operator-only Evaluation Trace and replay

Evaluation Trace is Working State, not trusted history. `Core.inspect(id)` exposes it
only to the local operator. Neither sender acquisition nor the State API exposes
traces, provider scores, prompts or replay fixtures.

## Evidence

Trace schema `1` records fixed Core selection, disclosure and validation policy
versions. Each selector attempt records its canonical request, permitted input
references, candidate IDs/revisions, question/rubric and task version, operating
point, primitive-specific raw output, selection/abstention, bounded fallback,
usage and latency. Malformed serializable outputs remain evidence. Unserializable
or credential-bearing outputs have an explicit unavailable/redacted marker.

`generative_attempts` is ordered and separate from the existing `model_calls`
integer. Each call retains its input Pack, Pack digest, provider/model and
instruction/task version, safe original Proposal, Proposal digest, usage, outcome
and latency. Retrieval Proposals remain available even after a later Proposal
commits. The turn's `proposal` is the separately validated, possibly normalized
checkpoint. Attempts also record validation, commit/retry/delivery outcomes,
semantic commit and event IDs, and end-to-end latency. Delivery-only retries name
the restored attempt and do not add inference calls.

OpenAI uses its configured pinned model, `semantic-proposal-v1` instructions and
reported token usage. Legacy custom providers can still return `SemanticProposal`.
They can declare `provider_id`, `model_id` and `instruction_version`, or implement
`propose_with_evidence(...) -> GenerationResult` from `piecetogether.openai_responses`.
Core prefers that optional method. Missing model metadata or usage remains `None`
(unknown), never an inferred deployment model or zero usage. No shared `last_usage`
state is used.

Required replay evidence is the normalized input, consistent initial-state
snapshot/revision, Contract/configuration and policy versions, ordered selector
outputs and Proposals, immutable Artifact metadata, scoped identity/time
allocations and operational channel/lifecycle outcomes. Optional full prompts,
HTTP transports and diagnostics are not required. `ReplayCase.diagnostics` can be
removed entirely. Scores retain their primitive meaning; they are not a common
probability of correctness and never authorize Grounding or disclosure.

## Explicit fixture capture

Use a dedicated, quiescent fixture Core with deterministic or captured adapters.
Do not use a serving Core with stateful adapters: capture reuses its model and
selector objects. Capture never runs automatically on normal turns.

```python
from piecetogether.evaluation import ReplayCase, capture, replay

# fixture_core already has the consumer's Contract, initial state and fixture adapters.
case = capture(fixture_core, lambda recorder: recorder.accept(normalized_payload))
result = replay(case)
assert result.matched, result.mismatches

# Optional JSON persistence; protect the destination before writing.
data = case.to_dict()
restored = ReplayCase.from_dict(data)
assert replay(restored).matched
```

For multi-turn Grounding scenarios, the callback can use `recorder.core.inspect(id)`
to obtain exposed item/outbound IDs, replace `recorder.core.model` with another
fixture adapter, call `recorder.accept(...)` again, and call `recorder.restart()` to
exercise durable retry. A supplied `channel=` fixture can report refusal, outage
or indeterminate delivery. The default channel writes only to an isolated local
mailbox. No projection sink, Reference State refresh, acquisition worker or State
API is started for fixture execution.

Capture uses SQLite backup, then removes unrelated transport tables and optional
trace diagnostics from the snapshot. It runs the scenario against a temporary
copy; it does not mutate the source database or export credentials, secret
references or environment values. Fixtures preserve the replay-relevant static
configuration, including the four-field retention policy, and the SQLite
`retention_state(category, availability)` schema and rows. Configuration is not
proof that a sweep ran: a configured but unapplied policy remains distinct from
durable availability. Expected results are a baseline for comparison,
not an independent judgment of model quality: a consumer may edit them to state
its own expected semantics.

Fixtures preserve Artifact publication and pending-deletion failure records visible
in trusted history and the Current Trusted View; capture does not silently discard
these fields. Startup recovery follows the source configuration in the isolated
Core: default retain permits normal recovery; configured maintenance suppresses
recovery before the sweep. Capture does not copy source Artifact bytes. A pending
publication that requires absent bytes makes capture unavailable, not a repaired
or silently empty fixture.

Fixtures contain sensitive normalized Communications, internal context, trusted
state and Artifact lifecycle failure messages. Keep them operator-only, outside
sender-accessible storage and public repositories. Create files with restrictive
permissions, use approved storage, and apply the deployment's retention/redaction
policy to fixture copies too. Only load operator-controlled SQLite fixtures, not sender-supplied databases.

## Offline execution and failure

`replay(case)` restores an isolated temporary database and executes real Core
assembly, bounded selection/retrieval, validation, Grounding exposure, commit and
outbox generation. Captured adapters replace inference, channel delivery and
Artifact publication/removal. It never constructs an OpenAI/Jev adapter, sends a
message, refreshes remote state, dispatches a sink or fetches Artifact bytes.
Per-instance identity/time sources preserve exact IDs, relationships and semantic
timestamps; no production globals are patched. Parallel replays are independent.
Recorded selector timing preserves time/budget exhaustion despite faster replay.

The result compares recomputed Packs, selection/fallback, validation and rejection,
commit/retry outcomes, normalized Proposals, Grounding Items, provenance,
relationships, Current Trusted View and semantic events. `result.mismatches`
names differing sections (`steps`, `history`, `view`, `events`). Expected assertions
are never restored into the computed result.

Missing/redacted/consumed evidence, changed revisions, unsupported policy/question
versions, unavailable Artifact metadata or extra decision calls raise
`ReplayUnavailable`. This error bypasses ordinary provider fallback. Retention
that removes required evidence makes replay unavailable; replay never substitutes
current state, another provider call or an invented answer.

### Local retention and fixture copies

[Local Core retention](core-retention.md) is an explicit, deployment-wide,
terminal maintenance sweep, not expiry or selective subject erasure. Its four
categories are Communications, trusted semantic history, Evaluation Traces and
Current Trusted View. Defaults retain; `redact` and `delete` both erase category
content, preserving only non-identifying category/availability markers. Source
category sweeps also erase dependent working evidence and the semantic revision.
Trace-only sweeps retain source Communications and trusted history but remove
attempts, Proposal checkpoints and pending exposure evidence. View-only sweeps
retain source history but explicitly disable projection derivation.

Any applied non-retain category makes both capture and replay raise
`ReplayUnavailable`, even for an empty scenario or a later retain configuration.
The check runs before isolated Core startup can initialize missing evidence or
recover old work. Missing initial-state tables or the semantic revision also fail
explicitly; an erased source is never treated as a valid empty history. A missing
retention table alone means legacy retain, consistent with Core availability.
Fixtures must include retention configuration; older exports lacking it cannot
be replayed by guessing defaults. Recapture only from complete retained evidence.
Removing optional prompt/transport diagnostics or `ReplayCase.diagnostics` does
not erase required evidence and remains replayable.

The local sweep does not reach exported fixtures, caller-held copies, external
projections/provider messages, logs, snapshots or backups. Operators must track
and apply approved retention to every fixture copy separately. Do not restore a
pre-erasure fixture into an erased deployment or use replay to resume that
deployment. ArtifactStore still owns bytes and storage keys; this mechanism adds
no byte-deletion path. SQLite logical erasure and `secure_delete` do not guarantee
physical sanitization of WAL/journals, filesystem remnants, backups or storage
media. External-copy cleanup and physical-media sanitization are unsupported.

Artifact staging and successful publication/removal use captured metadata and
outcomes, not external bytes. Capture uses a local fixture ArtifactStore when one
is enabled; it does not copy source Artifact files. A pre-existing pending
publication requiring absent bytes fails capture explicitly. Unserializable
provider decisions and malformed generative envelopes without a captured Proposal
also remain explicitly unreplayable. Arbitrary concurrent external state changes,
maintenance operations outside the recorder, channel preparation that rewrites
semantic text/identity rather than transport metadata, and live-provider quality
evaluation are not simulated by this fixture seam.
