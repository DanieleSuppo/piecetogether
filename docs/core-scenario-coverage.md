# Canonical Core scenario coverage audit (#31)

## Method and baseline

Initial matrix written **before any #31 test edits**, against `8a4429f45c62c4f91f44961f4c3ad9e563b42e45` (348 passing tests). Authority: PRD sections 6 and 22, issue #12 Testing Decisions, issue #31, and the approved [bounded-decision design](superpowers/specs/2026-10-05-semantic-decision-routing-design.md).

Coverage means inspected assertions on deterministic Core-boundary outcomes, not plausible prose, schema validity, model agreement or test names alone. Paths below name existing unittest methods; missing combinations are explicit in the gap register. No live inference, benchmark runner, model-comparison framework or Core domain vocabulary is required.

The preliminary read-only analysis marked several proofs uncertain. Direct inspection resolved them without adding tests: silence/elapsed time is already tested in `test_cross_channel.py`; channel-URL persistence is explicitly rejected in `test_validation.py`; post-commit sink failure and model-free recovery are already tested in `test_projection.py`.

## PRD scenario families

All paths in this table are under `tests/`.

| Family | Existing fixture(s) at baseline | Observable proof |
| --- | --- | --- |
| S1 New information | `test_core.py::test_text_interpretation_is_durable_but_not_trusted`; `test_history.py::test_authorized_commit_atomically_records_objects_claim_and_provenance` | Interpretation remains candidate; subsequent evidence produces atomic trusted objects/claim/provenance/event. |
| S2 New Entity/Context | `test_context.py::test_multi_context_catalogue_is_actor_scoped_and_retains_concepts_and_artifacts`; `test_history.py::test_authorized_commit_atomically_records_objects_claim_and_provenance` | Allowed Entity and first-class Context are committed only through Grounding; Actor-scoped catalogue preserves both. |
| S3 Older Context | `test_context.py::test_captured_jev_relevance_selects_multiple_contexts_without_live_credentials`; `test_view.py::test_context_lifecycle_preserves_identity_members_and_provenance_across_threads` | Multiple Contexts may be selected; lifecycle retains identity, members and provenance across threads. Adversarial old-pending/ranking combination is G1 below. |
| S4 Correction | `test_history.py::test_corrections_supersessions_and_contradictions_preserve_history`; `test_view.py::test_current_heads_retain_provenance_and_transitive_lineage`; `test_grounding.py::test_correction_creates_an_exposed_successor_with_immutable_lineage` | Old assertions remain; successor needs exposure/evidence; current heads and lineage reflect grounded correction. |
| S5 Ambiguous references | `test_grounding.py::test_duplicate_value_items_remain_ambiguous_even_in_a_reply`; `test_grounding.py::test_reference_choices_are_scoped_and_new_requires_ordinary_grounding`; `test_cross_channel.py::test_duplicate_targets_stay_hidden_and_confident_forced_acceptance_is_rejected` | Unknown/unselected/ambiguous Entities never force resolution; confidence .999 cannot overcome ambiguous targets. Concept counterpart is G2. |
| S6 Artifact roles | `test_validation.py::test_artifact_roles_retention_and_lifecycle_obey_contract`; `test_artifacts.py::test_attachment_candidate_claim_can_ground_without_persisting_a_domain_artifact`; `test_artifacts.py::test_persistent_attachment_is_staged_linked_and_finalized_without_a_channel_url` | Ephemeral/source roles do not imply persistence; persistent content uses Core-owned staged bytes and explicit provenance. |
| S7 Artifact supersession | `test_artifact_lifecycle.py::test_supersession_preserves_predecessor_and_links_distinct_published_content` | Distinct immutable records/publications, predecessor preserved, explicit supersession relation. |
| S8 Cross-session/channel | `test_cross_channel.py::test_both_commands_resolve_independently_targeted_items_on_the_new_channel`; `test_cross_channel.py::test_restart_and_redelivery_do_not_duplicate_exposure_commit_or_reply` | Email↔Telegram share configured Actor only; source/exposure/evidence provenance and idempotent effects survive restart. |
| S9 Delayed Grounding | `test_grounding.py::test_delayed_unique_pending_evidence_survives_restart_but_ambiguous_stays_untrusted`; `test_cross_channel.py::test_partial_explicit_and_implicit_resolutions_preserve_unaddressed_items` | Later sender evidence resolves addressed items independently; unaddressed items stay pending. Four-outcome captured-decision replay is G4. |
| S10 Retrieval prohibited | `test_context.py::test_disclosure_is_core_rendered_and_ranking_or_request_cannot_authorize_it`; `test_cross_channel.py::test_retrieval_and_forbidden_disclosure_do_not_leak_pending_or_trusted_history` | Historical retrieval refused; no secret/draft leakage or unauthorized Grounding. |
| S11 Necessary disclosure | `test_context.py::test_disclosure_is_core_rendered_and_ranking_or_request_cannot_authorize_it`; `test_cross_channel.py::test_necessary_item_disclosure_is_bounded_to_the_addressed_pending_item` | Only Core-authorized purpose/item is rendered; ranking does not grant disclosure. |
| S12 Domain guardrail | `test_validation.py::test_allowed_entity_is_only_a_candidate_and_forbidden_types_are_terminal`; `test_validation.py::test_emergent_concepts_are_non_authoritative_and_cannot_bypass_canonical_rules` | Forbidden Entity Types, concept promotion and canonical constraint bypass are rejected without trusted mutation. |
| S13 Concurrent correction | `test_concurrency.py::test_parallel_interpretations_commit_once_and_stale_work_reprocesses_without_clock_ordering`; `test_context.py::test_revision_change_during_model_work_requires_reprocessing`; `test_view.py::test_competing_transition_rechecks_context_lifecycle_even_with_fresh_revision` | Stale work cannot commit; relevant lifecycle/revisions are rechecked atomically. |
| S14 Reference conflict | `test_references.py::test_configured_reference_keeps_distinct_provenance_and_explicit_conflict`; `test_application_api.py::test_state_returns_conflicting_provenance_classes_and_correction_lineage` | Grounded and authoritative heads retain distinct provenance and explicit conflict, not a scalar winner. |

## PRD invariants and required #12 gates

| Invariant / gate | Existing fixture(s) | Assertion boundary |
| --- | --- | --- |
| I1 No silent extraction; I7 LLM proposes, Core commits | `test_history.py::test_direct_model_mutations_and_fabricated_items_leave_history_unchanged` | Direct entity/context mutation and fabricated acceptance rejected; ledger unchanged. |
| I2 Observable interpretation; failed exposure cannot ground | `test_history.py::test_failed_or_indeterminate_exposure_and_silence_create_no_groundable_items`; `test_cross_channel.py::test_failed_exposure_on_origin_channel_cannot_be_grounded_elsewhere` | Failure/indeterminate handoff creates no groundable item, no trusted revision. |
| I3 Later sender evidence; silence never acceptance | `test_cross_channel.py::test_silence_and_elapsed_time_do_not_resolve_or_trigger_outreach`; `test_grounding.py::test_pending_resolution_has_no_semantic_effect`; `test_history.py::test_sender_identity_and_evidence_reference_cannot_be_forged` | Advancing time without sender input leaves items/revision unchanged and triggers no provider call/outreach; wrong Actor/evidence rejected. |
| I4 Contract-constrained creation | `test_validation.py::test_allowed_entity_is_only_a_candidate_and_forbidden_types_are_terminal`; `test_validation.py::test_relationship_endpoints_and_claim_constraints_are_checked_in_any_order` | Forbidden type, endpoint, value, concept and policy combinations fail validation. |
| I5 Preserve history | `test_history.py::test_corrections_supersessions_and_contradictions_preserve_history`; `test_view.py::test_current_heads_retain_provenance_and_transitive_lineage` | Corrections do not overwrite predecessor claims or provenance. |
| I6 Provenance, subject to retention | `test_history.py::test_authorized_commit_atomically_records_objects_claim_and_provenance`; `test_retention.py::test_history_erasure_invalidates_evidence_and_view_without_new_grounding`; `test_evaluation.py::test_initial_snapshot_and_capture_match_source_pending_grounding_and_artifact_lifecycle` | Trusted source/Actor/Contract/Grounding links retained; policy erasure explicitly invalidates availability, not Grounding. |
| I8 Acquisition is not retrieval | S10/S11 fixtures; `test_context.py::test_high_score_does_not_grant_disclosure_and_exhausted_fallback_is_explicit` | Scores/ranking cannot broaden disclosure. |
| I9 No unrelated external action | `test_validation.py::test_emergent_concepts_are_non_authoritative_and_cannot_bypass_canonical_rules`; `test_validation.py::test_malformed_typed_fields_and_unknown_versions_cannot_bypass_validation` | Unsupported operations are rejected; supported semantic outputs remain state/events, not autonomous business-action execution. |
| I10 Core owns memory | `test_cross_channel.py::test_restart_and_redelivery_do_not_duplicate_exposure_commit_or_reply`; `test_evaluation.py::test_real_core_replays_grounding_commit_provenance_events_without_inference` | Durable Core state survives disposable provider instances/restart and replays without provider state/inference. |
| I11 Explicit channel continuity, no switching/outreach | `test_cross_channel.py::test_unmapped_senders_do_not_reach_the_provider_or_link_by_content`; `test_cross_channel.py::test_silence_and_elapsed_time_do_not_resolve_or_trigger_outreach` | Unmapped identities never reach inference; only sender-chosen channel receives a response. |
| I12 Reference vs conversational provenance | S14 fixtures | Both classes coexist and conflict explicitly. |
| Candidates/traces excluded from API/events | `test_application_api.py::test_decision_evidence_never_enters_state_events_or_sender_replies`; `test_view.py::test_candidates_and_operational_records_never_cross_view_boundary` | Scores/decisions/working records are absent from trusted consumer and sender payloads. |
| Stale work cannot commit | S13 fixtures; `test_projection.py::test_candidate_rejection_staleness_and_semantic_storage_error_emit_nothing` | Stale/rejected work creates no trusted event. |
| Sink failure cannot roll back trusted state | `test_projection.py::test_sink_failure_after_new_commit_is_not_semantic_rejection_or_storage_rollback`; `test_projection.py::test_persistent_failure_is_recoverable_after_restart` | Commit/history/view survive failure; backoff/exhaustion/recovery retain same event without another model call. No duplicate fixture needed. |
| Persistent Artifact cannot depend on channel URL | `test_validation.py::test_artifact_roles_retention_and_lifecycle_obey_contract`; `test_artifacts.py::test_persistent_attachment_is_staged_linked_and_finalized_without_a_channel_url` | URL `content_reference` explicitly rejected as `artifact_staging_unavailable`; valid bytes remain Core-controlled. No duplicate fixture needed. |

## Bounded-decision coverage and pre-edit gap register

The **before** statuses below preserve the pre-edit audit, not the final coverage status. Exact S7/S13 citations above were reconciled against their bodies; the preliminary guessed suffixes were `distinct_publications` and `stale_work_reprocesses`. All other explicit test citations were checked against actual assertions. The post-gap table records only the added combinations.

| #31 criterion | Existing proof | Before status / narrow missing proof |
| --- | --- | --- |
| Multiple relevant Contexts | `test_context.py::test_captured_jev_relevance_selects_multiple_contexts_without_live_credentials` | Covered independently; G1 combines with adversarial ranking and old mandatory items. |
| Mandatory/older pending | `test_context.py::test_mandatory_pending_grounding_survives_empty_ranking_and_has_explicit_budget_outcome`; delayed/cross-channel fixtures | Covered independently; G1 pins displacement under a wrong high-ranked candidate. |
| Irrelevant high rank / confident wrong selection | `test_context.py::test_high_score_does_not_grant_disclosure_and_exhausted_fallback_is_explicit`; confident-but-wrong Grounding fixtures | **G1 missing:** independent relevance expectations for a schema-valid wrong selection, mandatory recall and forbidden effects, captured selector/model/question replay. Do not pretend Core can identify all semantic mistakes. |
| Catalogue omits a required source record | No baseline fixture forced `SelectionConfig.catalogue_records` compaction. | **G1 catalogue missing (final-review correction):** force a real seeded Context out, preserve mandatory pending items, and separate required-available recall from missing count. The original invented absent ID did not prove this. |
| Missing/ambiguous Entity/concept | Entity cases in `test_grounding.py::test_reference_choices_are_scoped_and_new_requires_ordinary_grounding` | **G2 partial:** explicit missing/ambiguous concept decisions with high-confidence metadata must not invent/promote/commit; cite existing Entity proof rather than duplicate it. |
| Italian synonymous concepts | `test_context.py::test_multi_context_catalogue_is_actor_scoped_and_retains_concepts_and_artifacts`; `test_history.py::test_emergent_concept_survives_restart_and_is_reusable_without_promotion` | **G3 partial:** end-to-end Italian synonymous wording reuses selected persisted concept through Grounding, provenance and Current Trusted View without promoting domain vocabulary. Preserve legitimate per-source descriptions. |
| Partial mixed Grounding responses | `test_grounding.py::test_implicit_mixed_italian_outcomes_are_independent`; `test_cross_channel.py::test_partial_explicit_and_implicit_resolutions_preserve_unaddressed_items` | **G4 partial:** captured decision evidence on accepted/rejected/corrected/pending items together, explicit expected ledger/provenance/view/events/retry and replay without inference. Existing semantics, no new per-item Jev adapter. |
| Provider outage/malformed output | `test_context.py::test_captured_jev_failures_fallback_and_exhausted_usage_is_explicit`; `test_context.py::test_unusable_selection_falls_back_without_semantic_rejection_or_trusted_effect` | Covered; ten captured failure modes plus direct malformed results. |
| Bounded fallback / budget exhaustion | `test_context.py::test_input_budget_is_enforced_before_invoking_a_selector`; `test_context.py::test_additional_context_requests_are_scoped_purpose_labelled_and_cumulative`; `test_evaluation.py::test_offline_replay_preserves_all_selector_fallback_and_budget_outcomes` | Covered; pre-call/mandatory-record/retrieval/fallback limits remain explicit. |
| Confidence cannot authorize disclosure/identity/promotion/Contract/silence/commit | Above boundary fixtures; `test_grounding.py::test_confident_but_conflicting_implicit_decision_stays_pending_with_trace`; `test_cross_channel.py::test_duplicate_targets_stay_hidden_and_confident_forced_acceptance_is_rejected` | Existing independent boundary tests apply regardless of score. G1/G2/G4 strengthen captured-score combinations; no production score authority is added. |
| Captured decisions/model/question versions replay | `test_evaluation.py::test_real_core_replays_grounding_commit_provenance_events_without_inference`; `test_evaluation.py::test_offline_replay_preserves_all_selector_fallback_and_budget_outcomes` | Covered generically; new G1/G4 must use this seam, not expected-output echoing. |

G1's relevance expectations and catalogue-absence accounting use fixture-local set arithmetic against actual seeded source IDs declared before capture. The real compaction case separates required-available recall from required records missing from the catalogue. Neither case adds a Core metric API or benchmark framework. A schema-valid, wrong relevance ranking may select internal-only material; the required safety property is no broadened disclosure, identity inference or trusted mutation, not semantic omniscience.

## Decision paths and Jev adoption assessment

- #16 owns `context.SemanticSelector.select`, implemented by `context.ReferenceSelector` (default) and optional `jev.JevSelector`. Core owns mandatory records, membership, scope, revision, budgets and disclosure. The initial shorthand `context.Selector` was reconciled to the actual protocol name.
- #17 uses `proposals.ReferenceResolutionOperation` and `proposals.GroundingResolutionOperation`; `SemanticProposal` carries optional captured decision metadata through ordinary deterministic validation and `context.check_disclosure`. No separate live Jev Grounding adapter was adopted.
- #27 uses `proposals.ArtifactOperation` through the existing `openai_responses.OpenAIResponsesModel` generative/captured Proposal seam plus Contract validation. Jev was not adopted as a multimodal or Artifact classifier.
- [`jev-adoption.md`](jev-adoption.md) records a provider contract and an evaluation procedure, **not measured deployment adoption**. Captured HTTP responses prove adapter mechanics only.

| Required adoption evidence | Available measured deployment result |
| --- | --- |
| Required-record recall, with absent catalogue records separate | None |
| Wrong selections / false accepted proposals against independent expected transitions | None |
| Fallback rate and cause | None |
| End-to-end latency including retries/fallback | None |
| Total cost including composed/generative/fallback calls; unknown usage explicit | None |

**Decision: Jev remains unenabled.** No live comparison is required to close this audit; absent adoption evidence means keep deterministic default. Schema-valid outputs and LLM consensus alone are not correctness or adoption criteria.

## Post-edit gap mapping

Only `tests/test_scenario_coverage.py` adds tests. It composes existing `test_context` fixture helpers and calls real Core validation, Grounding, persistence, view, events and replay. No production policy is patched. External network/provider construction is guarded by asserted-unused tripwires. There is no production/data-format migration; rollback removes additive proofs/docs without changing stored data or mixed-version behavior.

| Gap | Exact added method in `ScenarioCoverageTests` | Independent expectations and resulting proof |
| --- | --- | --- |
| G1 missing → covered | `test_g1_wrong_confident_selection_retains_older_mandatory_items_and_replays` | Italian cucina/bagno turn after an older terrazzo exposure. Required IDs are three old items plus two Contexts; the irrelevant claim is declared before selection. All five required records are available in the catalogue. Wrong .999 selection retains all three mandatory items, selects cucina and one irrelevant claim, misses available bagno: required-available recall **4/5**, wrong selections **1/5**. This proves an available-but-unselected miss, not catalogue truncation. A valid semantic error may enter the internal pack; no claim disclosure, foreign Actor record, new Grounding or trusted history/view/event mutation follows. Selector/model/question evidence survives serialized real replay. |
| G1 catalogue missing → covered | `test_g1_real_catalogue_compaction_separates_missing_required_records_and_replays` | Required IDs come from the actual seeded cucina Context in source history and three older exposed terrazzo items, before capture. `SelectionConfig(catalogue_records=3)` fills the real catalogue with mandatory items, excluding the optional required Context. Capture and real replay independently assert `catalogue_truncated is True`, exact mandatory catalogue/selection, required Context absence, required-available recall **3/3**, and missing required count **1** separately. No new Grounding or trusted history/view/event mutation occurs; the seeded source remains unchanged. |
| G2 partial → covered | `test_g2_missing_ambiguous_concepts_and_confidence_cannot_create_authority` | Two legitimate per-source `preferenza` descriptions are selected. Unknown known-reference, forced ambiguous selection and ungrounded new concept reject; none/ambiguous remain unresolved. .999 decision metadata cannot promote, create forbidden Entity Type, link Actor identities through unsupported `linkActor`, or accept a fabricated Grounding Item. History/view/events/concepts remain exactly unchanged; rejected replies are absent and terminal retries are idempotent. Existing Entity fixtures are cited above, not duplicated. |
| G3 partial → covered | `test_g3_italian_synonym_reuses_persisted_concept_through_grounding_and_view` | `L'opzione favorita` selects the persisted `preferenza` record via its prior description; captured generative interpretation uses that name without a new declaration. Internal concept selection does not grant disclosure. Observable `legno caldo` candidate stays untrusted until later Contract-valid confirmation. Exactly one new claim/view head/event retains expected source/exposure/evidence/Actor/Contract provenance. Persisted descriptions remain unchanged and non-authoritative, with no Contract promotion. Serialized offline replay recomputes the same expected trusted effects. This proves captured synonym interpretation, not live-model quality or a deterministic semantic synonym resolver. |
| G4 partial → covered | `test_g4_four_mixed_decisions_preserve_independent_transitions_retry_and_replay` | Independent map: colore accepted, misura rejected, materiale corrected, tempo pending. .999 per-item confidence and versioned decision metadata accompany actual Italian evidence. Only blu commits initially; failed successor delivery leaves commit durable but successor unexposed. Restart/delivery-only retry exposes metallo without inference or duplicate commit; later confirmation commits only metallo. Tempo stays pending/undisclosable. Exact claim values, revisions 1→2→3, correction lineage, source/exposure/evidence provenance, view heads, event assertion/item IDs and final idempotent retry are asserted against literals/fixture IDs, both captured and replayed. |

A replay match alone is never the oracle: both G1 cases and G3/G4 independently assert actual replay history, view, events and processing outcomes. Replay cases round-trip through JSON; real Core recomputes results with all external provider construction/network tripwires unused and the default model's inference method forbidden. Capture leaves the original seeded Core unchanged. G2 intentionally checks captured decision trace metadata directly, without replay: its unsupported raw operation is not representable through the typed proposal replay seam.

## Five issue acceptance criteria

| Criterion (issue order) | Proof |
| --- | --- |
| 1. Expected semantic transitions, provenance/view/disclosure/events/retry | Existing S1–S14 fixtures plus G1–G4; G4 pins four outcomes, event payloads and delivery-only retry independently. |
| 2. Every PRD scenario family and required invariant, without framework | S1–S14 and I1–I12 tables above; five methods added to the existing unittest suite only. No benchmark/model-comparison runner. |
| 3. Bounded-decision combinations, including Italian/synonyms/failures/budgets | Pre-edit register plus G1–G4 closes the missing combinations; existing malformed/outage/fallback/exhaustion and Entity fixtures remain the proof for already-covered cases. |
| 4. Confidence has no semantic authority; versioned no-inference replay | G1 wrong selection/internal-only boundary; G2 promotion/Contract/identity/fabricated-commit metadata cases; G4 later evidence per item; existing high-confidence ambiguity, silence/outreach and disclosure fixtures. Silence invokes no provider, so there is no independent score to apply to that path. G1/G3/G4 replay captured versions with independent expectations. |
| 5. Jev adoption requires independent domain/operational evidence | Adoption assessment above and `jev-adoption.md`: no measured recall, wrong-selection, false-accept, fallback, latency or total-cost deployment evidence. Deterministic default unchanged; optional Jev remains unenabled. |

## Completion proof

Initial audit was recorded before test edits. Five additive deterministic fixtures pass existing production behavior; no production defect or behavior change was needed. Fixture development exposed only a Python 3.10 unittest helper mismatch and an invalid fixture confirmation form (`Confermo, sì` under default `yes` policy); both were corrected in test setup/data, not Core. These were not product RED evidence.

Verification: new fixtures **5/5** pass (2.218s); nearest Context/Grounding/evaluation/validation/cross-channel gates **102/102** pass (72.520s); full suite **353/353** passes (246.093s, 600-second timeout), versus 348 tests at baseline. Exact `uvx mypy --strict piecetogether` exits 1 with **13 errors in core.py/evaluation.py**; line-number-normalized diagnostics match a fresh export of baseline `8a4429f45c62c4f91f44961f4c3ad9e563b42e45`. `git diff --check` and separate untracked test/audit whitespace checks pass. No staged files. Strict mypy remains accepted baseline debt, not a green gate. The correction adds real catalogue compaction proof and removes the invented absent-ID assertion; its deliberate no-capacity-limit control fails on `catalogue_truncated is False`. This is an evidence correction, not a production defect or product RED claim.

Rerun from the repository root:

```bash
python3 -m unittest discover -s tests -p test_scenario_coverage.py -v
PYTHONPATH=tests python3 -m unittest -v test_context test_grounding test_evaluation test_validation test_cross_channel
timeout 600 python3 -m unittest discover -s tests -v
uvx mypy --strict piecetogether
git diff --check
```

To reproduce the strict-mypy comparison without switching branches or creating a worktree, export the stable #30 baseline `8a4429f45c62c4f91f44961f4c3ad9e563b42e45` (`8a4429f`) into a temporary directory. Both mypy invocations are expected to exit **1** with **13 errors in core.py/evaluation.py**; normalize only diagnostic line numbers before comparison:

```bash
evidence_dir=$(mktemp -d)
git archive 8a4429f45c62c4f91f44961f4c3ad9e563b42e45 piecetogether | tar -x -C "$evidence_dir"
status=0
uvx mypy --strict piecetogether > "$evidence_dir/current.log" 2>&1 || status=$?
test "$status" -eq 1
status=0
(cd "$evidence_dir" && uvx mypy --strict piecetogether) > "$evidence_dir/baseline.log" 2>&1 || status=$?
test "$status" -eq 1
python3 - "$evidence_dir" <<'PY'
import re
import sys
from pathlib import Path
root = Path(sys.argv[1])
def diagnostics(name):
    text = (root / name).read_text()
    assert text.count('error:') == 13, text
    return re.sub(r'^(piecetogether/[^:\n]+):\d+(?::\d+)?:', r'\1:<line>:', text, flags=re.M)
assert diagnostics('current.log') == diagnostics('baseline.log')
print('Identical normalized diagnostics: 13 baseline errors')
PY
```

Logs remain local run artifacts, not repository evidence dependencies. Independent whole-diff trust-boundary review and scoped correction re-review passed. The catalogue-absence proof was corrected to exercise real source-record compaction; no material finding remains. Release staging is limited to this audit, the Jev adoption note and the new scenario test file.
