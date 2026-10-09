# #23 — Grounded Change Event delivery evidence

- Ticket: https://github.com/DanieleSuppo/piecetogether/issues/23
- Authority: SPEC https://github.com/DanieleSuppo/piecetogether/issues/12, README, `docs/PRD.md`.
- Fixed review base: `62301a539f088607e54ef95d351fa11265dcfaf3`.
- Implementation branch: `implement/23-grounded-events`.
- Verification seams: normalized Core acquisition, trusted history, Grounding Items, Current Trusted View, original outbox events, injected deterministic ProjectionSink, delivery inspection/recovery. Temporary SQLite files only; no credentials or live services.

## Reproduced defects and TDD

Command for each red/green cycle: `python3 -m unittest discover -s tests -p test_projection.py -v`.

1. Original Core could not publish an existing outbox record: `test_existing_outbox_delivers_original_trusted_event_once` failed with `TypeError: Core.__init__() got an unexpected keyword argument 'projection_sink'`. After adding the boundary and dispatcher: 1 test passed.
2. Retry policy had no deployment configuration: `test_temporary_failure_retries_with_backoff_and_original_payload` failed with `AttributeError: module 'piecetogether.projections' has no attribute 'ProjectionConfig'`. After durable reservation, retry deadlines and attempt recording: 2 tests passed.
3. Service lifecycle did not dispatch: `test_startup_and_new_commit_publish_outside_semantic_transactions` failed because startup sink receipts were `[]` instead of the original event. After startup/post-processing/post-reference dispatch: 4 tests passed.
4. Correction events omitted existing targets: `test_correction_event_names_existing_target_and_both_assertions` failed because `entity_ids` was `[]` rather than the existing Entity ID. After extending newly generated events with affected existing targets/predecessors: 8 tests passed. Stored historical events remain unchanged.

## Regression coverage

`tests/test_projection.py` preserves successful delivery, duplicate idempotency, temporary/persistent errors, increasing backoff, exhausted-budget recovery, restart with a pending lease, crash after sink acceptance, local outcome-storage failure, sink payload mutation, live-lease exclusion and late-result fencing, Contract upgrade, reference events, static config rejection, and candidate/rejected/stale/storage outcomes.

Assertions cover history, Grounding outcomes, trusted revision/view, unchanged original event identifiers/payloads, delivery states/attempts and no model reinvocation. A second SQLite writer inside the deterministic sink proves handoff is outside semantic transactions. A crash uses a process-stop exception; storage failures use temporary SQLite triggers, not live infrastructure.

## Verification before independent review

- `python3 -m unittest discover -s tests -p test_projection.py -v`: 14 tests passed.
- `python3 -m unittest discover -s tests -p test_history.py -v`: 27 tests passed.
- `python3 -m unittest discover -s tests -v`: 195 tests passed (70.308 s).
- `uvx mypy --strict piecetogether`: success, 12 source files.
- `git diff --check`: exit 0.

## Operational boundaries

Delivery is at least once, not exactly once. Consumers own deduplication by event/semantic commit ID. A lease longer than normal sink handoff reduces overlap but cannot eliminate crash/slow-sink duplicates. Exhausted failures require explicit recovery without changing semantic history. The development service dispatches bounded passes at startup and after processing/reference refresh; idle retry/backlog draining uses periodic `core.dispatch_events()` maintenance, not a new background scheduler or queue framework. State API, authentication and new connectors remain out of scope.
