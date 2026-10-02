# Core shell and first observable interpretation (#13)

Approved in conversation on 2026-10-02. Authority: [#13](https://github.com/DanieleSuppo/piecetogether/issues/13) and [SPEC #12](https://github.com/DanieleSuppo/piecetogether/issues/12).

## Architecture

Retain the existing Python 3.10+ standard-library implementation. A standalone JSON-lines process accepts normalized text Communications. Deployment JSON selects static development adapters, explicit channel-to-Actor identity mappings, Contract version, capabilities, environment secret references, and a persistent SQLite file.

Resolve and validate identity before processing. Commit inbound Communication idempotently before model work. The deterministic ModelProvider returns a versioned, candidate-only Semantic Proposal. Validate its envelope and provenance, persist proposal and outbound Communication, then ask the development ChannelPlugin to accept handoff into its durable mailbox. Persist the processing outcome and minimal Evaluation Trace separately from trusted knowledge. This slice has no trusted-state writer.

## Idempotency and failures

The channel, sender, and delivery key identify an inbound Communication. An identical redelivery reuses the durable outcome after restart; conflicting payloads are rejected. A failed or indeterminate channel handoff returns a retryable outcome without an exposed reply. Redelivery reuses the stored proposal and outbound identifier rather than invoking the model again. Model/channel work runs outside database transactions. The minimal trace describes the latest processing attempt and retains an attempt counter; it is not an append-only attempt history.

Run one sequential development worker per database. Concurrent scheduling and trusted semantic revision checks belong to subsequent tickets. Operator inspection requires local deployment filesystem access and is separate from sender-facing replies. Do not expose traces or secrets in those replies.

## Verification seams

The user approved `Core.accept()` with operator `Core.inspect()` for durable outcomes, and the standalone process boundary. Use deterministic provider/channel doubles and temporary persistent SQLite databases. Assert identity, provenance, candidate-only status, durable outcomes, retry behavior, and absence of trace disclosure rather than response wording or SQL internals.

Complete with focused test files and strict typechecking during changes, one full test suite at the end, independent standards/spec reviews using `/code-review`, and a commit on the current branch.
