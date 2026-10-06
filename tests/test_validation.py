import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from piecetogether.core import Bootstrap, CandidateClaim, Core, SemanticProposal
from piecetogether.contracts import DomainContract
from piecetogether.proposals import (
    ArtifactOperation, ClaimOperation, ContextOperation, ContextRequestOperation,
    EmergentConceptOperation,
    EntityOperation, GroundingPlanOperation,
    GroundingResolutionOperation, RelationshipOperation,
)


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config = Bootstrap(
            Path(self.directory.name) / 'core.sqlite3', {'development:s1': 'a1'}
        )
        self.message = {
            'channel': 'development',
            'sender': 's1',
            'idempotency_key': 'm1',
            'text': 'An interpretation',
            'sent_at': '2026-01-01T12:00:00Z',
        }

    def use_contract(self, **changes):
        data = {
            'schema_version': 1,
            'version': 'development-v1',
            'entity_types': {'Subject': {'creation': True, 'attributes': {'label': {'type': 'string', 'max_length': 20}}}},
            'relationship_types': {},
            'claim_concepts': {},
            'artifact_types': {},
            'grounding_policies': {},
            'emergent_concepts': {'allowed': False},
            **changes,
        }
        path = Path(self.directory.name) / 'contract.json'
        path.write_text(json.dumps(data))
        self.config = replace(self.config, contract=path)
        return data

    def propose(self, operations=(), contract_provider=None, **changes):
        class ProposalModel:
            def propose(model, inbound, contract_version, context_pack):
                return SemanticProposal(**{
                    'schema_version': 1,
                    'contract_version': contract_version,
                    'communication_id': inbound.id,
                    'candidate_claims': (),
                    'draft_response': 'An interpretation',
                    'operations': operations,
                    **changes,
                })

        result = Core(
            self.config, model=ProposalModel(), contract_provider=contract_provider,
        ).accept(self.message)
        return result, Core(self.config).inspect(result['communication_id'])

    def test_allowed_entity_is_only_a_candidate_and_forbidden_types_are_terminal(self):
        self.use_contract()
        result, outcome = self.propose((EntityOperation('s1', 'Subject', {'label': 'A subject'}),))
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(outcome['trace']['validation_result'], 'accepted')
        self.assertEqual(outcome['trace']['commit_result'], 'not_requested')

        self.message['idempotency_key'] = 'm2'
        rejected, outcome = self.propose((EntityOperation('s2', 'Forbidden'),))
        self.assertEqual(rejected['status'], 'rejected')
        self.assertEqual(outcome['trace']['validation_reasons'], ['entity_type_not_allowed'])
        self.assertIsNone(outcome['outbound'])
        self.assertEqual(Core(self.config).accept(self.message), rejected)

    def test_relationship_endpoints_and_claim_constraints_are_checked_in_any_order(self):
        self.use_contract(
            grounding_policies={'conversation': {'acceptance': ['explicit', 'implicit']}},
            claim_concepts={'count': {'target_types': ['Subject'], 'value': {'type': 'integer', 'minimum': 0, 'maximum': 10}, 'grounding_policy': 'conversation'}},
            relationship_types={'related': {'source_types': ['Subject'], 'target_types': ['$context']}},
        )
        graph = (
            RelationshipOperation('related', 's1', 'ctx1'),
            ClaimOperation('c1', 's1', 'count', 3, 'conversation'),
            ContextOperation('ctx1', ('s1',)),
            EntityOperation('s1', 'Subject'),
        )
        result, outcome = self.propose(graph)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(outcome['trace']['validation_result'], 'accepted')
        self.assertEqual(outcome['trace']['commit_result'], 'not_requested')
        for i, (operation, reason) in enumerate((
            (RelationshipOperation('related', 'ctx1', 's1'), 'relationship_endpoints_not_allowed'),
            (RelationshipOperation('invented', 's1', 'ctx1'), 'relationship_type_not_allowed'),
            (RelationshipOperation('related', 'missing', 'ctx1'), 'relationship_endpoints_not_allowed'),
            (ClaimOperation('c2', 's1', 'count', True, 'conversation'), 'claim_value_not_allowed'),
            (ClaimOperation('c2', 's1', 'count', 11, 'conversation'), 'claim_value_not_allowed'),
            (ClaimOperation('c2', 'ctx1', 'count', 3, 'conversation'), 'claim_target_not_allowed'),
            (ClaimOperation('c2', 's1', 'invented', 3, 'conversation'), 'claim_concept_not_allowed'),
            (ClaimOperation('c2', 's1', 'count', 3, 'invented'), 'grounding_policy_not_allowed'),
        )):
            with self.subTest(reason=reason, operation=operation):
                self.message['idempotency_key'] = f'invalid-{i}'
                rejected, outcome = self.propose(graph[2:] + (operation,))
                self.assertEqual(rejected['status'], 'rejected')
                self.assertEqual(outcome['trace']['validation_reasons'], [reason])
                self.assertIsNone(outcome['outbound'])

    def test_artifact_roles_retention_and_lifecycle_obey_contract(self):
        retention = {'metadata': 'retain', 'bytes': 'delete', 'provenance': 'retain'}
        self.use_contract(artifact_types={
            'Evidence': {
                'roles': ['ephemeral-evidence', 'source-evidence'],
                'persistence': 'forbidden', 'retention': retention, 'supersession': False,
            },
            'Record': {
                'roles': ['source-evidence', 'persistent-domain-artifact'],
                'persistence': 'required',
                'retention': {'metadata': 'retain', 'bytes': 'retain', 'provenance': 'retain'},
                'supersession': True,
            },
        })
        result, outcome = self.propose((ArtifactOperation('a1', 'Evidence', ('ephemeral-evidence', 'source-evidence'), retention=retention),))
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(outcome['trace']['commit_result'], 'not_requested')
        for i, (operation, reason) in enumerate((
            (ArtifactOperation('a1', 'Unknown', ('source-evidence',)), 'artifact_type_not_allowed'),
            (ArtifactOperation('a1', 'Evidence', ('persistent-domain-artifact',)), 'artifact_roles_not_allowed'),
            (ArtifactOperation('a1', 'Evidence', ('source-evidence',), retention={'bytes': 'retain'}), 'artifact_retention_not_allowed'),
            (ArtifactOperation('a1', 'Record', ('source-evidence',)), 'artifact_persistence_required'),
            (ArtifactOperation('a1', 'Evidence', ('source-evidence',), action='persist'), 'artifact_persistence_not_allowed'),
            (ArtifactOperation('a1', 'Record', ('persistent-domain-artifact',), action='persist', content_reference='https://channel.example/file'), 'artifact_staging_unavailable'),
            (ArtifactOperation('a1', 'Evidence', ('source-evidence',), action='supersede', predecessor_id='a0'), 'artifact_supersession_not_allowed'),
            (ArtifactOperation('a1', 'Record', ('persistent-domain-artifact',), action='supersede', predecessor_id='a0'), 'artifact_predecessor_unavailable'),
        )):
            with self.subTest(reason=reason):
                self.message['idempotency_key'] = f'artifact-{i}'
                rejected, outcome = self.propose((operation,))
                self.assertEqual(rejected['status'], 'rejected')
                self.assertEqual(outcome['trace']['validation_reasons'], [reason])
                self.assertIsNone(outcome['outbound'])

    def test_grounding_plans_obey_policy_and_cannot_manufacture_sender_evidence(self):
        self.use_contract(
            grounding_policies={'sensitive': {'acceptance': ['explicit']}},
            claim_concepts={'note': {'target_types': ['Subject'], 'value': {'type': 'string'}, 'grounding_policy': 'sensitive'}},
        )
        candidates = (
            EntityOperation('s1', 'Subject'),
            ClaimOperation('c1', 's1', 'note', 'A note', 'sensitive'),
        )
        result, outcome = self.propose(candidates + (GroundingPlanOperation('sensitive', ('c1',), 'explicit'),))
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(outcome['trace']['commit_result'], 'not_requested')
        for i, (operation, reason) in enumerate((
            (GroundingPlanOperation('sensitive', ('c1',), 'implicit'), 'grounding_mode_not_allowed'),
            (GroundingPlanOperation('unknown', ('c1',), 'explicit'), 'grounding_policy_not_allowed'),
            (GroundingPlanOperation('sensitive', ('s1',), 'explicit'), 'grounding_target_not_allowed'),
            (GroundingPlanOperation('sensitive', (), 'explicit'), 'grounding_target_not_allowed'),
            (GroundingPlanOperation('sensitive', ('c1', 'c1'), 'explicit'), 'duplicate_grounding_target'),
            (GroundingResolutionOperation('pending1', 'sensitive', 'later-input', 'explicit', 'accepted'), 'grounding_item_unavailable'),
        )):
            with self.subTest(reason=reason):
                self.message['idempotency_key'] = f'grounding-{i}'
                rejected, outcome = self.propose(candidates + (operation,))
                self.assertEqual(rejected['status'], 'rejected')
                self.assertEqual(outcome['trace']['validation_reasons'], [reason])
                self.assertEqual(outcome['trace']['commit_result'], 'not_requested')
                self.assertIsNone(outcome['outbound'])

    def test_old_contract_requires_fresh_processing_without_exposure(self):
        class OldContractModel:
            def propose(self, inbound, contract_version, context_pack):
                return SemanticProposal(
                    1, 'obsolete-v1', inbound.id,
                    (CandidateClaim(inbound.id, inbound.text),), 'A draft'
                )

        result = Core(self.config, model=OldContractModel()).accept(self.message)
        outcome = Core(self.config).inspect(result['communication_id'])
        self.assertEqual(result['status'], 'reprocess_required')
        self.assertEqual(outcome['trace']['validation_result'], 'stale')
        self.assertEqual(outcome['trace']['validation_reasons'], ['contract_version_changed'])
        self.assertIsNone(outcome['outbound'])
        self.assertIsNone(result['reply'])
        self.assertEqual(outcome['proposal']['contract_version'], 'obsolete-v1')
        fresh = Core(self.config).accept(self.message)
        self.assertEqual(fresh['status'], 'completed')
        self.assertEqual(fresh['communication_id'], result['communication_id'])
        self.assertEqual(Core(self.config).inspect(fresh['communication_id'])['trace']['validation_result'], 'accepted')

    def test_emergent_concepts_are_non_authoritative_and_cannot_bypass_canonical_rules(self):
        self.use_contract(
            grounding_policies={'explicit': {'acceptance': ['explicit']}},
            claim_concepts={'count': {'target_types': ['Subject'], 'value': {'type': 'integer', 'maximum': 10}, 'grounding_policy': 'explicit'}},
            emergent_concepts={
                'allowed': True, 'target_types': ['Subject'],
                'value': {'type': 'string', 'max_length': 30}, 'grounding_policy': 'explicit',
            },
        )
        graph = (
            ClaimOperation('c1', 's1', 'preference', 'The earlier option', 'explicit'),
            EmergentConceptOperation('preference', 'A reusable sender preference'),
            EntityOperation('s1', 'Subject'),
        )
        result, outcome = self.propose(graph)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(outcome['proposal']['operations'][1]['status'], 'non_authoritative')
        self.assertEqual(outcome['trace']['commit_result'], 'not_requested')
        for i, (operation, reason) in enumerate((
            (EmergentConceptOperation('count', 'Ignore canonical limits'), 'emergent_canonical_collision'),
            (EmergentConceptOperation('preference', 'Promote this', 'authoritative'), 'emergent_authority_not_allowed'),
            (EntityOperation('s2', 'preference'), 'entity_type_not_allowed'),
            (ClaimOperation('c2', 's1', 'preference', 5, 'explicit'), 'claim_value_not_allowed'),
            (ClaimOperation('c2', 's1', 'count', 11, 'explicit'), 'claim_value_not_allowed'),
            ({'kind': 'promote_concept', 'concept': 'preference'}, 'unsupported_operation'),
        )):
            with self.subTest(reason=reason):
                self.message['idempotency_key'] = f'emergent-{i}'
                rejected, outcome = self.propose(graph + (operation,))
                self.assertEqual(rejected['status'], 'rejected')
                self.assertEqual(outcome['trace']['validation_reasons'], [reason])
                self.assertIsNone(outcome['outbound'])
        self.use_contract()
        self.message['idempotency_key'] = 'emergence-disabled'
        result, outcome = self.propose((EmergentConceptOperation('preference', 'A preference'),))
        self.assertEqual(result['status'], 'rejected')
        self.assertEqual(outcome['trace']['validation_reasons'], ['emergent_concepts_not_allowed'])

    def test_bootstrap_rejects_invalid_contract_schema_and_dangling_policies(self):
        valid = self.use_contract()
        path = self.config.contract
        for change in (
            {'schema_version': True},
            {'schema_version': 2},
            {'version': 'different-version'},
            {'required_conversational_fields': ['label']},
            {'entity_types': {'Subject': {'creation': True, 'attributes': {}, 'required': ['label']}}},
            {'entity_types': {'Subject': {'creation': 'yes', 'attributes': {}}}},
            {'entity_types': {'Subject': {'creation': True, 'attributes': {'count': {'type': 'number', 'minimum': 10, 'maximum': 5}}}}},
            {'relationship_types': {'related': {'source_types': ['Unknown'], 'target_types': ['Subject']}}},
            {'claim_concepts': {'note': {'target_types': ['Subject'], 'value': {'type': 'string'}, 'grounding_policy': 'missing'}}},
            {'grounding_policies': {'bad': {'acceptance': ['silence']}}},
            {'emergent_concepts': {'allowed': True}},
            {'emergent_concepts': {'allowed': False, 'promote': True}},
            {'artifact_types': {'A': {'roles': ['source-evidence'], 'persistence': 'required', 'retention': {'metadata': 'retain', 'bytes': 'retain', 'provenance': 'retain'}, 'supersession': True}}},
        ):
            with self.subTest(change=change):
                path.write_text(json.dumps({**valid, **change}))
                with self.assertRaises(ValueError):
                    Core(self.config)
        path.write_text('{"schema_version":1,"schema_version":2}')
        with self.assertRaises(ValueError):
            Core(self.config)

    def test_entity_constraints_and_empty_information_do_not_become_a_checklist(self):
        self.use_contract(entity_types={
            'Subject': {
                'creation': True,
                'attributes': {
                    'label': {'type': 'string', 'enum': ['known', 'other'], 'max_length': 10},
                    'tags': {'type': 'array', 'items': {'type': 'string'}, 'max_length': 2},
                    'detail': {'type': 'object', 'properties': {'enabled': {'type': 'boolean'}}},
                },
            },
            'ExistingOnly': {'creation': False, 'attributes': {}},
        })
        for i, attributes in enumerate(({}, {'label': 'known', 'tags': ['one'], 'detail': {'enabled': True}})):
            self.message['idempotency_key'] = f'allowed-attributes-{i}'
            result, _ = self.propose((EntityOperation('s1', 'Subject', attributes),))
            self.assertEqual(result['status'], 'completed')
        for i, operation in enumerate((
            EntityOperation('s1', 'Subject', {'label': 'unknown'}),
            EntityOperation('s1', 'Subject', {'extra': 'invented'}),
            EntityOperation('s1', 'Subject', {'tags': ['one', 'two', 'three']}),
            EntityOperation('s1', 'Subject', {'detail': {'enabled': 1}}),
            EntityOperation('s1', 'ExistingOnly'),
            EntityOperation('s1', 'Subject', action='erase'),
            ContextOperation('ctx', ('missing',)),
            ContextOperation('ctx', action='resolve'),
        )):
            self.message['idempotency_key'] = f'invalid-attributes-{i}'
            result, outcome = self.propose((operation,))
            self.assertEqual(result['status'], 'rejected')
            self.assertIsNone(outcome['outbound'])
        self.message['idempotency_key'] = 'no-information'
        result, _ = self.propose()
        self.assertEqual(result['status'], 'completed')

    def test_malformed_typed_fields_and_unknown_versions_cannot_bypass_validation(self):
        self.use_contract()
        for i, (operations, changes) in enumerate((
            ((EntityOperation('duplicate', 'Subject'), ContextOperation('duplicate')), {}),
            ((ContextOperation('ctx', ('s1',)), EntityOperation('s1', {})), {}),
            ((EntityOperation('s1', 'Subject'),), {'intent': 'semantic_commit'}),
            ((EntityOperation('s1', 'Subject'),), {'intent': 'change_contract'}),
            ((), {'schema_version': True}),
            ((), {'schema_version': 2}),
            ([], {}),
        )):
            with self.subTest(operations=operations, changes=changes):
                self.message['idempotency_key'] = f'malformed-{i}'
                result, outcome = self.propose(operations, **changes)
                self.assertEqual(result['status'], 'rejected')
                self.assertEqual(outcome['trace']['validation_result'], 'rejected')
                self.assertEqual(outcome['trace']['delivery_result'], 'not_attempted')
                self.assertIsNone(outcome['outbound'])
                if changes.get('intent') == 'semantic_commit':
                    self.assertEqual(outcome['trace']['commit_result'], 'rejected')

    def test_deployment_contract_path_is_config_relative(self):
        data = self.use_contract()
        config_path = Path(self.directory.name) / 'bootstrap.json'
        config_path.write_text(json.dumps({
            'database': 'state.sqlite3', 'contract': 'contract.json',
            'contract_version': data['version'], 'identities': {'development:s1': 'a1'},
        }))
        config = Bootstrap.from_file(config_path)
        result = Core(config).accept(self.message)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(Core(config).inspect(result['communication_id'])['trace']['contract_version'], data['version'])

    def test_contract_declarations_cannot_hide_duplicate_keys_or_reserved_types(self):
        data = self.use_contract()
        self.config.contract.write_text(json.dumps(data).replace(
            '"creation": true', '"creation": false, "creation": true'
        ))
        with self.assertRaises(ValueError):
            Core(self.config)
        for reserved in ('$context', '$claim', '$artifact'):
            self.use_contract(entity_types={reserved: {'creation': True, 'attributes': {}}})
            with self.subTest(reserved=reserved), self.assertRaises(ValueError):
                Core(self.config)

    def test_reprocessing_does_not_reuse_a_previous_semantic_result_after_model_failure(self):
        class StaleModel:
            def propose(self, inbound, contract_version, context_pack):
                return SemanticProposal(1, 'old', inbound.id, (), 'A draft')

        class FailingModel:
            def propose(self, inbound, contract_version, context_pack):
                raise OSError('temporary failure')

        stale = Core(self.config, model=StaleModel()).accept(self.message)
        failed = Core(self.config, model=FailingModel()).accept(self.message)
        self.assertEqual(failed['communication_id'], stale['communication_id'])
        self.assertEqual(failed['status'], 'retryable')
        trace = Core(self.config).inspect(failed['communication_id'])['trace']
        self.assertIsNone(trace['validation_result'])
        self.assertEqual(trace['validation_reasons'], [])
        self.assertEqual(trace['failure_stage'], 'model')

    def test_numeric_constraints_handle_large_integers_and_reject_nonfinite_values(self):
        self.use_contract(
            grounding_policies={'explicit': {'acceptance': ['explicit']}},
            claim_concepts={'amount': {'target_types': ['Subject'], 'value': {'type': 'number', 'minimum': 0}, 'grounding_policy': 'explicit'}},
        )
        entity = EntityOperation('s1', 'Subject')
        result, _ = self.propose((entity, ClaimOperation('c1', 's1', 'amount', 10 ** 400, 'explicit')))
        self.assertEqual(result['status'], 'completed')
        for i, value in enumerate((float('inf'), float('nan'), -1, False)):
            with self.subTest(value=value):
                self.message['idempotency_key'] = f'number-{i}'
                result, outcome = self.propose((entity, ClaimOperation('c1', 's1', 'amount', value, 'explicit')))
                self.assertEqual(result['status'], 'rejected')
                self.assertEqual(outcome['trace']['validation_reasons'], ['claim_value_not_allowed'])
                self.assertIsNone(outcome['outbound'])

    def test_claim_provenance_and_grounding_policy_cannot_be_reassigned(self):
        self.use_contract(
            grounding_policies={'explicit': {'acceptance': ['explicit']}, 'loose': {'acceptance': ['implicit']}},
            claim_concepts={'note': {'target_types': ['Subject'], 'value': {'type': 'string'}, 'grounding_policy': 'explicit'}},
        )
        graph = (EntityOperation('s1', 'Subject'), ClaimOperation('c1', 's1', 'note', 'A note', 'explicit'))
        for i, (operations, reason) in enumerate((
            ((graph[0], replace(graph[1], source_communication_id='someone-else')), 'invalid_claim_provenance'),
            (graph + (GroundingPlanOperation('loose', ('c1',), 'implicit'),), 'grounding_policy_not_allowed'),
        )):
            self.message['idempotency_key'] = f'provenance-{i}'
            result, outcome = self.propose(operations)
            self.assertEqual(result['status'], 'rejected')
            self.assertEqual(outcome['trace']['validation_reasons'], [reason])

    def test_deep_invalid_model_values_leave_a_durable_rejection_trace(self):
        self.use_contract(
            grounding_policies={'explicit': {'acceptance': ['explicit']}},
            claim_concepts={'note': {'target_types': ['Subject'], 'value': {'type': 'string'}, 'grounding_policy': 'explicit'}},
        )
        value = 'deep'
        for _ in range(1100):
            value = {'nested': value}
        result, outcome = self.propose((
            EntityOperation('s1', 'Subject'),
            ClaimOperation('c1', 's1', 'note', value, 'explicit'),
        ))
        self.assertEqual(result['status'], 'rejected')
        self.assertEqual(outcome['trace']['validation_reasons'], ['claim_value_not_allowed'])
        self.assertEqual(outcome['trace']['proposal_capture'], 'unserializable')
        self.assertIsNone(outcome['proposal'])
        self.assertIsNone(outcome['outbound'])

    def test_free_text_candidate_envelope_has_a_bounded_count(self):
        class ManyCandidatesModel:
            def __init__(model, count):
                model.count = count

            def propose(model, inbound, contract_version, context_pack):
                return SemanticProposal(
                    1, contract_version, inbound.id,
                    tuple(CandidateClaim(inbound.id, f'Interpretation {i}') for i in range(model.count)),
                    'A bounded draft',
                )

        for count, expected in ((256, 'completed'), (257, 'rejected')):
            self.message['idempotency_key'] = f'candidate-count-{count}'
            result = Core(self.config, model=ManyCandidatesModel(count)).accept(self.message)
            self.assertEqual(result['status'], expected)
            if expected == 'rejected':
                outcome = Core(self.config).inspect(result['communication_id'])
                self.assertEqual(outcome['trace']['validation_reasons'], ['too_many_candidate_claims'])
                self.assertIsNone(outcome['outbound'])

    def test_entity_resolution_is_distinct_from_forbidden_creation(self):
        self.use_contract()
        result, outcome = self.propose((EntityOperation('s1', 'Subject', action='resolve'),))
        self.assertEqual(result['status'], 'rejected')
        self.assertEqual(outcome['trace']['validation_reasons'], ['entity_resolution_unavailable'])

    def test_typed_context_requests_cannot_fetch_uncontrolled_memory(self):
        for i, (operation, reason) in enumerate((
            (ContextRequestOperation(('ctx1',), 'continuity', 10), 'context_scope_not_allowed'),
            (ContextRequestOperation(('ctx1',), 'historical_retrieval', 10), 'invalid_context_request'),
            (ContextRequestOperation((), 'grounding', 10), 'invalid_context_request'),
            (ContextRequestOperation(('ctx1',), 'grounding', False), 'invalid_context_request'),
        )):
            self.message['idempotency_key'] = f'context-request-{i}'
            result, outcome = self.propose((operation,))
            self.assertEqual(result['status'], 'rejected')
            self.assertEqual(outcome['trace']['validation_reasons'], [reason])
            self.assertEqual(outcome['proposal']['operations'][0]['kind'], 'context_request')
            self.assertIsNone(outcome['outbound'])

    def test_contract_provider_boundary_enforces_the_selected_version(self):
        class StaticContractProvider:
            def get(provider, version):
                return DomainContract(version, entity_types={
                    'Subject': {'creation': True, 'attributes': {}},
                })

        result, outcome = self.propose(
            (EntityOperation('s1', 'Subject'),),
            contract_provider=StaticContractProvider(),
        )
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(outcome['trace']['contract_version'], 'development-v1')

        class WrongVersionProvider:
            def get(provider, version):
                return DomainContract('wrong-version')

        with self.assertRaisesRegex(ValueError, 'different Contract version'):
            Core(self.config, contract_provider=WrongVersionProvider())

    def test_concept_names_cannot_be_used_as_entity_type_sentinels(self):
        self.use_contract(
            grounding_policies={'explicit': {'acceptance': ['explicit']}},
            emergent_concepts={
                'allowed': True, 'target_types': ['Subject'],
                'value': {'type': 'string'}, 'grounding_policy': 'explicit',
            },
        )
        graph = (
            EntityOperation('s1', 'Subject'),
            EmergentConceptOperation('$claim', 'Vocabulary is separate from types'),
            ClaimOperation('c1', 's1', '$claim', 'An interpretation', 'explicit'),
        )
        result, _ = self.propose(graph)
        self.assertEqual(result['status'], 'completed')
        self.message['idempotency_key'] = 'sentinel-type-attempt'
        result, outcome = self.propose(graph + (EntityOperation('s2', '$claim'),))
        self.assertEqual(result['status'], 'rejected')
        self.assertEqual(outcome['trace']['validation_reasons'], ['entity_type_not_allowed'])

    def test_malformed_envelope_metadata_is_an_operational_provider_failure(self):
        for field in ('contract_version', 'communication_id', 'intent'):
            with self.subTest(field=field):
                self.message['idempotency_key'] = f'metadata-{field}'
                result, outcome = self.propose(**{field: object()})
                self.assertEqual(result['status'], 'retryable')
                self.assertIsNone(outcome['trace']['validation_result'])
                self.assertEqual(outcome['trace']['failure_stage'], 'model')
                self.assertIsNone(outcome['outbound'])


if __name__ == '__main__':
    unittest.main()
