# Core record retention implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Track steps with checkboxes. User preapproved a technically consistent plan and requires the current checkout, one writer, one dedicated commit per issue.

**Goal:** Deliver #30's explicit configured retention/redaction/deletion for Communications, trusted semantic history, Evaluation Traces and Current Trusted View, then independently deliver #31's scenario audit.

**Architecture:** Keep Core as the operator boundary, history as ledger owner and Current Trusted View derived. Add a small retention policy/maintenance module only if needed to keep destructive ownership coherent. Apply a static deployment policy through an explicit local operator maintenance call, never a sender proposal or public administrative API. Reuse SQLite transactions and existing Artifact lifecycle; do not build a scheduler or generic privacy framework.

**Tech Stack:** Python standard library, SQLite, unittest, existing Bootstrap and replay contracts.

**Spec:** GitHub #12/#30/#31; `docs/PRD.md` section 15; `docs/evaluation-replay.md`; `docs/superpowers/specs/2026-10-05-semantic-decision-routing-design.md`.

## Global Constraints

- Work on existing `main`; no branch/worktree. Preserve `docs/research/opencode-go-deepseek-limits.md` byte-for-byte and never stage it.
- Use only temporary databases in tests. Never apply deletion to deployment databases.
- Deletion is not a Grounding outcome. Never infer acceptance or new truth from absent evidence.
- Required unavailable evidence makes reads/replay explicitly unavailable, not an empty manufactured state, current-state substitution or new inference.
- Keep deterministic selection default. Jev remains optional and disabled without adoption evidence.
- One writer per workspace. Workers never commit, push, mutate GitHub or spawn children.
- No new dependency, live inference, benchmark runner, comparison framework, CI or hook work.
- Baseline HEAD `17b85e061aff17ea1fb7d3d766a2c1c28be1559b`: 321 passing tests; 13 pre-existing strict mypy errors in core.py/evaluation.py.

## Review Focus

1. Dependent copies in attempts, working proposals, local delivery records, pending exposure and replay snapshots must not retain erased content.
2. Complete deletion cannot retain identifying tombstones or provenance where forbidden; permitted redaction residue must be explicit and content-free.
3. Concurrent or restarted processing must not recreate removed data, deliver erased drafts or commit with missing evidence.
4. Projection-only policy must prevent source-ledger reconstruction from bypassing policy; source-history removal must not leave an apparently valid projection.
5. Snapshot fidelity must be checked against source state, not merely against replay of the same sanitized snapshot.

## Ownership and scope

- `piecetogether/core.py`: static Bootstrap parsing and validation; explicit operator retention entrypoint; ingress/retry/recovery and inspection gates.
- `piecetogether/history.py`, `view.py`, `application_api.py` as necessary: ledger deletion/invalidation, derived availability and fail-closed public read behavior.
- Optional `piecetogether/retention.py`: policy dataclasses, category availability and transaction implementation if that is the clearest cohesive owner.
- `piecetogether/evaluation.py`: capture/configuration fidelity and explicit missing-evidence replay behavior.
- `tests/test_retention.py`, `tests/test_evaluation.py`: only retention, resurrection/isolation and source-snapshot regression proof.
- `docs/core-retention.md`, `docs/evaluation-replay.md`: actual operator API, exact residue, maintenance preconditions, external-copy limits.

### Task 1: Configured Core-layer retention

**Deliverable:** Independent retain/redact/delete policies for all four categories, applied atomically through a local operator call. Default retains existing behavior. Use an explicit deployment-scoped maintenance operation if selecting individual record dependency closures would introduce an unrequested framework; document its scope prominently. No automatic expiry scheduler is required by #30. Avoid claiming selective erasure or external-copy erasure.

**Interfaces:** A typed static policy attached to Bootstrap; a local `Core.apply_retention(...)` operation using the configured policy; an explicit unavailable error or result for retained metadata without evidence, with no new sender/admin HTTP endpoint. Exact argument names may follow the traced owners; document them before the report. Independent category policy must not permit dependent copies to bypass erasure. Complete deletion may retain only non-identifying category availability/policy state needed to reject future reads; never deleted values, record IDs, provenance, arbitrary reasons or tombstones. If safe continuation is impossible, reject processing explicitly rather than fabricate history; document recovery requirements.

- [ ] Write temporary-database tests for four categories separately, default retain, invalid config, redaction residue and complete deletion.
- [ ] Observe focused red before implementation.
- [ ] Implement the smallest coherent policy/transaction integration. Fence concurrent/in-flight writes and restart recovery. Keep Artifact-byte ownership unchanged.
- [ ] Test dependent-copy removal, no new semantic commit/event/Grounding acceptance, explicit unavailable reads, restart/no resurrection, and independent policy behavior.
- [ ] Run focused tests plus nearest affected existing gates; checkpoint changed files, exact commands/results and next step in `/tmp/opencode/issue30-task1-report.md`.
- [ ] Independent reviewer-premium checks real tracked and new-file diff for spec compliance and quality. Fix material findings and rerun their proof before Task 2.

### Task 2: Replay retention and source fidelity

**Consumes:** Task 1's actual policy and availability interface.
**Produces:** Source-faithful capture/replay integration; explicit unavailability when required retained evidence is absent; operator documentation.

- [ ] Add failing tests comparing restored snapshot and isolated capture state directly with source history, view, pending state and retention policy/availability, including redacted/deleted state. Include negative capture/replay after required evidence deletion and successful replay after optional diagnostics removal.
- [ ] Preserve retention schema/configuration in SQLite snapshots. Ensure startup recovery cannot undo retention. Use `ReplayUnavailable`; never call a provider to replace missing evidence.
- [ ] Document local maintenance semantics, four categories, residue, explicitly unsupported external copies/backups, and fixture-copy responsibilities. Do not promise physical storage sanitization beyond what SQLite can guarantee.
- [ ] Run targeted retention/evaluation gates, then `python3 -m unittest discover -s tests -v` with timeout 600 seconds; run `uvx mypy --strict piecetogether`, distinguish baseline errors; run `git diff --check`.
- [ ] Whole #30 reviewer-premium gate on the actual diff including new files; fix and reverify material findings.
- [ ] Parent creates one dedicated #30 commit, pushes without force, verifies remote SHA, publishes criterion proof/limits, closes #30 and appends pointer under #12 Decisions-so-far.

### Task 3: #31 scenario audit, only after #30 delivery

- [ ] Claim #31 only after #30's verified delivery.
- [ ] Create criterion/scenario → existing exact test or missing-test matrix before changing tests. Cover every #12 canonical family/invariant and every #31 bounded-decision requirement; inspect actual assertions, not test names alone.
- [ ] Audit #16/#17/#27 decision adoption and any Jev adoption evidence. Without domain recall/wrong-selection/fallback/latency/cost evidence, leave adapter disabled and record that decision.
- [ ] Add only missing deterministic Core-boundary fixtures. Assert independent expected ledger/Grounding/provenance/view/disclosure/event/retry transitions, including Italian/synonyms, old mandatory pending items, multiple Contexts, irrelevant high confidence, ambiguity/unknown IDs, partial responses, outage/malformed/fallback/exhaustion and trust-boundary bypass attempts. Replay captured versions/decisions with no inference.
- [ ] Focused tests, full suite (600 seconds), strict mypy with documented baseline, diff check; independent actual-diff reviewer-premium gate for trust boundaries.
- [ ] Dedicated #31 commit, non-force push and verified SHA, GitHub criterion proof/closure and parent Decisions-so-far pointer. Preserve user research file. Update recovery checkpoint after each unit.

## Progress

- Preflight complete: unchanged HEAD and configuration; origin/main aligned; all issue dependencies closed; #30 assigned, #31 unclaimed.
- Authorized analyst retry succeeded; cited behavior map: `/tmp/opencode/issue30-analysis.md`.
- Plan self-review: all #30 criteria map to Tasks 1–2; #31 maps to Task 3. No public admin API, scheduling, selective erasure framework or Artifact-byte lifecycle redesign is required.
- Tasks 1–2 implementation complete. Independent retention review found startup recovery and direct local sink resurrection gaps; both were fixed and independently re-reviewed. Whole-#30 release review passed with no material findings.
- Final #30 proof: 348 tests passed (`python3 -m unittest discover -s tests -v`, 600-second timeout); strict mypy reports exactly the same 13 pre-existing diagnostics in core.py/evaluation.py. Source-direct snapshot/initial-state comparisons cover policy, availability, history, view, pending Grounding and Artifact lifecycle failures. `git diff --check` passed.
- #30 delivery follows the verified release gate. Task 3 remains unclaimed until #30 is pushed, verified and closed. Terminal deployment-wide maintenance and external-copy/Artifact-byte/physical-storage limitations are documented in `docs/core-retention.md`.
