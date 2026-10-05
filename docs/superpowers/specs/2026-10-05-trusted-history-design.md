# Atomic trusted semantic history (#15)

Authority: SPEC #12, ticket #15, resolutions #3/#4/#6/#10, `CONTEXT.md`
and `docs/PRD.md`. The user requested autonomous decisions based on this
documentation, including test seams.

## Boundary

Extend the existing Core proposal/checkpoint flow using the existing SQLite
database and Python standard library. A semantic-commit Proposal requests an
effect; it is never proof of acceptance. Core-owned exposed Grounding Items
and subsequent normalized sender Communications supply its evidence.

The Core binds planned interpretations to outbound content by appending a
deterministic rendering of each target's semantic fields and relationships,
bounded with the complete response to 65,536 characters. A model's draft
alone cannot claim exposure for hidden values. Legacy cached drafts without
that binding require fresh processing rather than changing an attempted
outbound's content under the same idempotency key.

The minimum live tracer bullet records exposed plans after durable outbound
and accepted channel handoff, then commits accepted items on a later turn.
Other Grounding outcomes, correction-to-successor-candidate orchestration,
bounded context selection and natural-language confirmation policy extensions
belong to #16/#17. No runtime fixture importer or arbitrary trusted-state
write API is introduced.

## Records and authorization

Keep immutable trusted objects, relationships, Grounding snapshots and commits
separate from proposals, mutable pending items, traces and non-authoritative
Emergent Concepts. Core generates stable object and item identifiers; model
identifiers remain local to the originating proposal. Every trusted record
includes Contract version, commit ID/revision and provenance linking source,
outbound exposure, sender evidence, Actor, Grounding and item.

Grounding plans may expose Entity/Context resolutions independently via
`resolution_ids`, in addition to existing `claim_ids`. Claims cannot implicitly
authorize creating an unaccepted Entity or Context. Resolve operations refer
to existing trusted IDs and cannot rewrite attributes or Context membership.

Commit intents resolve existing pending items, rather than replacing their
candidate payload. Core checks policy, acceptance mode, source Actor,
subsequent receipt after acknowledged exposure, and addressed evidence. An
implicit resolution also needs a rationale in the envelope, but remains
unavailable until #17 supplies its evidence policy. Explicit evidence must
match a Contract `confirmation_forms` declaration (trimmed/case-folded exact
text), defaulting to the minimal unambiguous `yes` form. Missing, failed or indeterminate
exposure, fabricated items/evidence, stale source Contracts and direct object
creation in commit envelopes authorize nothing. Only the accepted outcome is
enabled in this foundational slice.

## Transaction and history

A short `BEGIN IMMEDIATE` transaction revalidates the active Contract and the
Proposal's `semantic_revision`, checks pending items and dependencies, appends
history and Grounding snapshots, advances the revision and inserts an outbox
event together with the processing checkpoint. Model and channel calls remain
outside transactions. Use one deployment semantic revision initially; this
conservative scope can reject independent concurrent work, with scoped
revisions refined in #22.

Corrections, supersessions and contradictions link immutable assertions.
Their source is newly accepted; existing records are never updated. A changed
claim on the same semantic key requires an explicit history relationship to
conflicting current heads, preventing silent replacement. The relationship
must be present in the exposed candidate graph and permitted by the Contract.

Commit retries recover the durable commit/checkpoint and outbound without
another model call or another semantic effect, including after delivery
failure, restart or Contract change. Storage failure rolls back all trusted
writes; stale and rejected work leaves trusted history unchanged. Event
dispatch and Current Trusted View are implemented by #23 and #18 respectively;
the atomic outbox record is part of the commit foundation now.

An attempt losing to an already committed turn preserves the winning durable
checkpoint, including when its model/parsing operation fails. The losing
attempt remains in operational history without erasing the commit's outbound.

## Emergent Concepts

Persist accepted declarations independently as non-authoritative vocabulary,
with source Communication, Actor and Contract provenance. Preserve earlier
descriptions instead of overwriting them. Existing names are available to
later validation/reconciliation, but each use still satisfies the active
Contract's emergent policy. They never create Entity Types or alter the
Contract. Operator inspection is separate from trusted history and sender
replies.

## Verification seams

Tests use `Core.accept`, `Core.inspect`, `Core.inspect_history` and
`Core.inspect_concepts`, temporary durable databases and deterministic model,
Contract and channel adapters. Assert ledger outcomes, provenance, revision,
relationships, Grounding outcomes, outbox, retry classification and traces.
Database fault triggers only arrange failures; assertions use Core interfaces.
Cover complete authorized commits, direct-write rejection, dependency
authorization, correction/supersession/contradiction history, stale work,
atomic rollback, durable retry, and reusable non-authoritative vocabulary.

Run focused unittest files and strict mypy during implementation, the full
suite once at the end, independent standards/spec code review, then commit
the intended changes on the current branch.
