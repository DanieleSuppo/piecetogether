# Bounded Context Assembly Implementation Plan

> **For agentic workers:** Use `executing-plans` to implement task-by-task in this session. The user approved the existing documentation and requested execution without further questions or checkpoints.

**Goal:** Deliver #16's bounded Context Packs, deterministic disclosure, and optional first-party Jev selector.

**Architecture:** Assemble an Actor-scoped snapshot before model work. One selection interface ranks a bounded catalogue; Core merges mandatory records and independently enforces budgets, revisions, requests and disclosure. Capture the input and decision evidence in the existing processing trace, preserving atomic history and durable delivery recovery.

**Tech Stack:** Python 3.10+, SQLite, stdlib HTTP/JSON, unittest and strict mypy; no runtime dependencies.

**Spec:** `docs/superpowers/specs/2026-10-05-semantic-decision-routing-design.md`, GitHub #16 and SPEC #12.

## Global Constraints

- Deterministic reference adapter is the default and requires no credentials.
- Multiple Contexts may be selected; mandatory pending Grounding cannot be dropped.
- Provider calls run outside semantic transactions.
- Selection never creates Grounding evidence, trusted state or broader disclosure.
- Jev uses a pinned model, versioned rubric, deployment secret references and bounded calls/time/input; no raw Artifact input.
- Test at the approved Core service, inspection and bootstrap seams, with captured provider outputs.
- No universal confidence threshold, generic provider framework, benchmark runner or second judge.
- Run focused test files and typechecking during implementation, the full suite once at the end, then code-review and commit on the current branch.

## File responsibilities

- `piecetogether/context.py`: frozen Context Pack/selection/configuration records, bounded snapshot assembly, reference selection, requests, revision/disclosure checks.
- `piecetogether/jev.py`: first-party HTTP adapter, per-candidate Noul relevance, primitive-specific response validation and provider limits.
- `piecetogether/core.py`: bootstrap adapter selection, Context Pack model input, bounded proposal loop, checkpoint/retry integration and trace capture.
- `piecetogether/proposals.py`: typed disclosure operation and bounded context-request shape validation.
- `tests/test_context.py`: Core-boundary scenarios, captured Jev HTTP outputs and bootstrap failures.
- Existing test adapters: accept the new `context_pack` argument; retain their behavior and history invariants.
- `README.md` and `docs/agents`-independent adoption documentation: input contract, disclosure policy, configuration and focused adoption procedure.

## Task 1: Context Pack tracer bullet

**Interfaces:** `ModelProvider.propose(inbound, contract_version, context_pack) -> SemanticProposal`; `Core(..., selector=...)`; `Core.inspect(id)['trace']['context_pack']`.

- [x] Add a Core-seam test for a supplied, bounded Pack with Actor/Contract identity and captured revision.
  ```python
  result = core.accept(message)
  self.assertEqual(result['status'], 'completed')
  self.assertEqual(core.inspect(result['communication_id'])['trace']['context_pack']['semantic_revision'], 0)
  ```
- [x] Run `python3 -m unittest discover -s tests -p test_context.py -v` and verify red.
- [x] Implement frozen pack/record/config types, Actor-scoped snapshot catalogue and model input; adapt existing deterministic test models to receive the Pack.
- [x] Verify green with the focused file and `uvx mypy --strict piecetogether`.

## Task 2: Mandatory records, selection and budgets

**Interfaces:** `SemanticSelector.select(request) -> SelectionResult`; `SelectionRequest` carries only Communication, bounded candidates, permitted context, rubric and remaining budgets.

- [x] Add scenarios for two Contexts, older pending Grounding, persisted concepts and candidate Artifact metadata, unknown/duplicate IDs, malformed assessments and high-score ineligible records.
  ```python
  self.assertTrue(required_ids <= selected_ids)
  self.assertFalse(other_actor_ids & selected_ids)
  self.assertEqual(pack['outcome'], 'budget_exhausted')
  ```
- [x] Verify red/green behavior for structural merging, lexical ranking and explicit exhausted budgets. Selection returns zero or multiple candidates rather than a forced reference resolution; Choice-based none/new/ambiguous resolution remains with later owning tickets.
- [x] Store catalogue revisions, raw decisions, model/rubric versions, usage, latency and fallback evidence in existing traces.
- [x] Verify the focused file and strict typechecking.

## Task 3: Bounded requests and sender disclosure

**Interfaces:** `ContextRequestOperation(scope_ids, purpose, budget)`; `DisclosureOperation(record_id, purpose)`; proposal request loop shares cumulative Pack/model budgets.

- [x] Test a known scoped request followed by a final proposal; test unknown/other-Actor requests and repeated requests exhausting the budget.
  ```python
  self.assertEqual(result['status'], 'completed')
  self.assertEqual(trace['retrieval'][0]['purpose'], 'disambiguation')
  self.assertIsNone(exhausted['reply'])
  ```
- [x] Implement shape-first request validation, bounded Core retrieval and model reinvocation outside transactions.
- [x] Test default-deny disclosure and general retrieval refusal with an independent secret fixture. Implement typed, Core-rendered eligible historical references; unrestricted model prose cannot authorize historical disclosure.
- [x] Test semantic changes during provider work and delivery retries; recheck captured revisions at the atomic checkpoint and restore Pack evidence on delivery recovery.
- [x] Verify `test_context.py`, affected existing test files and strict typechecking.

## Task 4: Optional Jev adapter

**Interfaces:** static `SelectionConfig` in Bootstrap; `JevSelector.select(request)` returns the same bounded result as the reference adapter.

- [x] Add bootstrap tests for aliases, missing pinned/rubric/budget/secret configuration and unknown selectors.
- [x] Add captured HTTP Noul responses for multi-Context relevance, malformed/unknown IDs, abstention, timeout, rate-limit failure and usage exhaustion. Test through Core with the real Jev adapter, replacing only external HTTP.
  ```python
  self.assertEqual(trace['selection'][0]['response']['model'], 'jev-1.13.0')
  self.assertEqual(trace['selection'][0]['fallback_reason'], 'invalid_selection')
  ```
- [x] Implement stdlib HTTPS with no automatic remote retry; deterministic fallback consumes the remaining call/time budget. Enforce bounded response bytes, UTF-8 input upper bounds and documented 64k total / 32k state-plus-longest-question limits. Validate exact answer keys, Noul range, pinned model and finite usage metadata.
- [x] Document provider limits reverified on 2026-10-06 and deployment-specific operating-point/adoption metrics.
- [x] Verify the focused file and strict typechecking.

## Task 5: Verification, review and commit

- [x] Update README behavior/configuration and add a focused adoption procedure using existing domain scenarios, Italian, ambiguous references, multiple subjects, older pending Grounding and synonymous concepts.
- [x] Run `python3 -m unittest discover -s tests -v` once and `uvx mypy --strict piecetogether`.
- [x] Invoke `code-review` against starting commit `47f18aa`; address findings, rerunning only affected checks unless a finding requires broader coverage.
- Final action: inspect `git status`, `git diff`, and `git log --oneline -10`; stage only intended files and commit to the current branch.

## Plan self-review

All #16 acceptance criteria map to Tasks 1–4; verification/adoption and commit map to Tasks 4–5. The approved seam remains Core, with external HTTP captured at its system boundary. The deployment-wide semantic revision remains the conservative fence; pending exposure and concept changes also require snapshot revalidation because they do not advance that revision. Artifact metadata comes only from existing validated candidate classifications; Artifact persistence/perception remains with its owning tickets.

## Execution evidence

- Full suite before final review: 89 tests passed. Post-review regression scenarios were verified in focused files; the full suite was not repeated.
- Strict mypy: eight source files clean throughout the final integration.
- Standards review: no documented-standard breaches; simplified dead primitive/reference machinery, imports and budget logic.
- Spec review: fixed refusal-only phantom exposure, terminal request-budget validation and actual rendered-response limits. Scoped re-review confirmed all four original findings addressed, with no load-bearing regression.
- Added foreign-ID Actor-scope regression and preserved Jev usage metadata on exhausted responses.
- Deterministic delivery/CI completed without live TypeSafe credentials. Production adoption requires the procedure in `docs/jev-adoption.md`.
