# Domain Contract Validation Implementation Plan

> **For agentic workers:** Execute inline with `executing-plans`; the user requested implementation and a commit on the current branch without further approval gates. Steps use checkbox syntax for tracking.

**Goal:** Implement issue #14's deterministic Domain Contract and Semantic Proposal boundary.

**Architecture:** Bootstrap supplies a validated declarative Contract. Typed operations describe candidate changes; a pure validator checks those operations before the Core stores an outbound interpretation. Semantic acceptance is distinct from delivery and trusted commit; #15 owns the trusted writer, #17 owns Grounding lifecycle, and #19 owns Artifact acquisition.

**Tech Stack:** Python 3.10+, standard library, SQLite, unittest, strict mypy.

**Spec:** https://github.com/DanieleSuppo/piecetogether/issues/14 and parent https://github.com/DanieleSuppo/piecetogether/issues/12.

## Global Constraints

- Keep the Core domain-independent and deployment-scoped.
- Entity Types are closed. Emergent Concepts are non-authoritative.
- Absence of a claim is not a violation; Contracts are not conversational checklists.
- Models propose; only deterministic Core logic can validate, and validation is not a trusted mutation.
- Test through `Core.accept`, `Core.inspect`, and bootstrap loading using deterministic providers and temporary persistent databases.
- Preserve durable ingress, idempotency, reply retry, and operator-only trace access.
- Run focused test files and strict typechecking during implementation; the full suite once at the end. Review using `/code-review` and commit on the current branch.

## File Map

- `piecetogether/contracts.py`: declarative Contract loading, policy/constraint validation, DomainContractProvider.
- `piecetogether/proposals.py`: frozen typed Proposal operations, envelope, deterministic validation result.
- `piecetogether/core.py`: select Contract at bootstrap; validate before delivery; persist terminal rejection and reprocess-required traces.
- `config/development-contract.json`, `config/development.json`: explicit development Contract and bootstrap reference.
- `tests/test_validation.py`: Core-boundary behavior and invalid bootstrap cases.
- `tests/test_core.py`: maintain the existing malformed-envelope and delivery-retry guarantees.
- `README.md`: supported declarative format, operations, statuses and ticket boundary.

## Vertical Slices

### 1. Durable semantic outcome classification

- [x] Write a Core-boundary test for wrong Contract version and durable deterministic rejection, observing:
  ```python
  self.assertEqual(result['status'], 'reprocess_required')
  self.assertEqual(outcome['trace']['validation_result'], 'stale')
  self.assertIsNone(outcome['outbound'])
  ```
- [x] Run `python3 -m unittest discover -s tests -p test_validation.py -v`; confirm red.
- [x] Implement typed `ValidationResult` and a validator called before outbound construction. Persist rejected proposals and trace reasons; do not re-run terminal rejection on redelivery. Stale redelivery reprocesses with the active Contract.
- [x] Run the focused file and `uvx mypy --strict piecetogether`.

### 2. Closed declarative domain and typed candidate operations

- [x] Add one Core-boundary scenario at a time for allowed/forbidden Entity creation, endpoint constraints, Claim values, Context references and provenance. Example:
  ```python
  self.assertEqual(outcome['trace']['validation_result'], 'rejected')
  self.assertEqual(outcome['trace']['validation_reasons'], ['entity_type_not_allowed'])
  ```
- [x] For each scenario, run the focused file red, implement only its validator branch, and run green.
- [x] Add `DomainContract.from_dict(data)` and config-relative `contract` loading with strict schema/version/reference validation. Reject malformed and unknown policy fields.
- [x] Validate frozen operation records and unique proposal-local IDs; check references independently of operation order. Reject trusted commit intents until the trusted committer exists.
- [x] Run `tests/test_core.py` and strict mypy after this slice.

### 3. Artifact, Grounding and Emergent Concept governance

- [x] Add Core-boundary policy scenarios one at a time for Artifact roles/persistence/retention/supersession declarations and Grounding plans/acceptance modes. Require valid policy references and candidate targets.
- [x] Add scenarios for allowed Emergent Concepts, disabled emergence, canonical-name collisions, invalid targets/values, and forbidden authoritative status. Expected evidence:
  ```python
  self.assertEqual(outcome['trace']['commit_result'], 'not_requested')
  self.assertEqual(outcome['proposal']['operations'][0]['status'], 'non_authoritative')
  ```
- [x] Run each scenario red, implement its deterministic rule, then run green. Unsupported lifecycle/evidence targets reject explicitly; do not invent prior semantic state.
- [x] Verify mixed proposals reject atomically, malformed runtime fields cannot bypass validation, delivery retry reuses accepted output, and missing claims are allowed.
- [x] Run the focused test files and strict mypy.

### 4. Documentation, verification, review and commit

- [x] Add an explicit generic development Contract; describe its declarative format and the accepted/rejected/reprocess-required outcomes in README.
- [x] Run `python3 -m unittest discover -s tests -v` once and `uvx mypy --strict piecetogether`.
- [x] Invoke `/code-review` against `cbf9ce0`, inspect Standards and Spec findings, address actionable findings with focused checks.
- [x] Inspect `git status`, `git diff`, and `git log --oneline -10` before finalizing.
- Finalization: stage only intended files and commit on the current branch using repository style. This plan accompanies the implementation commit.

## Coverage Check

Issue #14's allowed domain and policies are covered by slices 2–3. Typed/versioned operations and durable accepted/rejected/stale outcomes are covered by slices 1–2. Non-authoritative Emergent Concept governance is covered by slice 3. Persistence of reusable concepts and trusted atomic history are explicitly assigned to downstream #15 rather than manufactured by this ticket.

## Review Resolution

- Standards: bounded free-text candidates, clarified helper names and path semantics, parenthesized finite-number checks, and distinguished unavailable Entity resolution from forbidden creation. The DomainContractProvider seam remains because SPEC #12 explicitly names it; a deterministic provider-boundary test verifies it.
- Spec: rejected duplicate Grounding targets and added the typed additional-context request, with explicit safe rejection until #16 implements controlled retrieval.
- The suggested Concept/type-sentinel collision is not a type-policy bypass: these namespaces are independent and Entity operations always check the closed Entity Type catalogue. A Core-boundary test verifies that a Concept with a sentinel-like name cannot create that Entity Type.
- The full suite passed 28 tests before review. Final focused runs pass 21 validation, 9 Core and 4 service tests (34 total); strict mypy passes all five production modules. Premium Spec review was unavailable due provider quota; a separate independent reviewer completed that axis and verified the fixes without unresolved Spec blockers.
- Final malformed-metadata checks verify that non-JSON Contract references, Communication references and intent values produce durable operational failure traces before entering semantic validation or trace serialization.

## Independent Peer Review Follow-up

Requested separately against `cbf9ce0..3df8168` using `requesting-code-review`; evaluated using `receiving-code-review` and reproduced the reported failure windows before editing.

1. **Cached validation:** pending deliveries previously bypassed the active Contract and legacy version checks. Captured JSON now restores typed operations and passes through the same validator as fresh proposals. Valid retries preserve their original outbound identifier; stale replies require reprocessing before handoff.
2. **Atomic rejection:** proposal capture and terminal status previously used separate writes. The validation checkpoint now commits proposal, outbound, trace, semantic status and attempt evidence together. Failed storage remains operationally retryable. An interrupted older rejection is recovered from its recorded evidence without another model invocation.
3. **Attempt evidence:** reprocessing previously overwrote stale proposals and outcomes. A small SQLite attempt record preserves each processing attempt and is exposed through operator-only `Core.inspect`. The current attempt updates its handoff outcome atomically; subsequent attempts preserve completed earlier records. Existing last checkpoints migrate once at bootstrap.

Regression seam: `tests/test_processing.py`, through `Core.accept`, `Core.inspect`, deterministic providers/channels, legacy persistence fixtures and real SQLite trigger faults. These checks also cover final-write failure after handoff, transactional rollback when attempt storage fails, and typed candidate-graph restoration.

The independent follow-up identified and verified one additional legacy recovery edge: an intentionally omitted, unserializable rejected proposal still has valid rejection evidence in its explicit trace marker. Recovery now recognizes that marker while excluding operational storage failures. Both the positive recovery and negative storage-failure cases have regressions. The reviewer confirmed all three Important findings resolved, with no remaining Important or Critical blockers within #14's scope.
