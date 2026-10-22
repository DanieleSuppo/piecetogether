"""Explicit single-fixture operator smoke; import/discovery/default are offline.

Run from the repository with PYTHONPATH=tests python3 -m jev_guarded_smoke.
Paid invocation is NOT authorized by running preflight or by having a credential.
"""
import argparse
from contextlib import ExitStack
from dataclasses import replace
from decimal import Decimal
import json
import os
from pathlib import Path
from time import monotonic
from unittest.mock import patch

from piecetogether.context import POLICY_VERSIONS, TRACE_SCHEMA_VERSION, SelectionConfig, encoded
from piecetogether.evaluation import capture, replay
from piecetogether.jev_smoke import GuardedJevSelector, _new_output, _write_private


FIXTURE_VERSION = 'context-cucina-bagno-smoke-v1'


def _expect_domain(actual, before, required):
    """Independent ground truth, frozen before provider scores, on computed state."""
    if len(required) != 2 or len(actual['steps']) != 1:
        raise ValueError('fixture cardinality failed')
    step = actual['steps'][0]
    if step['result']['status'] != 'completed' or len(step['selection']) != 1:
        raise ValueError('smoke processing failed')
    selection = step['selection'][0]
    if (selection['fallback_reason'] is not None or selection['outcome'] != 'completed'
            or set(selection['selected_ids']) != required or len(selection['selected_ids']) != 2
            or step['grounding_items'] != [] or step['proposal']['operations'] != []):
        raise ValueError('independent domain expectations failed')
    records = step['trace']['context_pack']['records']
    if ({record['id'] for record in records} != required
            or not all(record['kind'] == 'context' for record in records)
            or 'segreto' in step['result']['reply']):
        raise ValueError('scope or disclosure expectations failed')
    for name in ('history', 'view', 'events'):
        if encoded(actual[name]) != encoded(before[name]):
            raise ValueError('trusted state changed')


def _offline_replay(case):
    # Existing replay owns captured-model/selector restoration and consumption.
    # Tripwires prove it does not reach any live/model implementation instead.
    with ExitStack() as stack:
        for target in ('socket.socket', 'http.client.HTTPSConnection',
                       'piecetogether.jev.JevSelector.__init__',
                       'piecetogether.jev.JevSelector.select',
                       'piecetogether.openai_responses.OpenAIResponsesModel.__init__',
                       'piecetogether.openai_responses.OpenAIResponsesModel.propose',
                       'piecetogether.core.DeterministicModel.propose',
                       'test_context.OperationsModel.propose'):
            stack.enter_context(patch(target, side_effect=AssertionError('offline replay tripwire')))
        return replay(case)


def _run(output, *, trust_primary_group: bool = False):
    trust_primary_group = trust_primary_group is True
    directory = _new_output(output, trust_primary_group=trust_primary_group)
    selector = None
    case = None
    started = monotonic()
    report = {
        'status': 'failed', 'error': None, 'scope': 'synthetic compatibility only; not deployment adoption',
        'trust_primary_group': trust_primary_group,
        'fixture_version': FIXTURE_VERSION, 'model': 'jev-1.13.0', 'rubric': 'context-relevance-v1',
        'policy_versions': POLICY_VERSIONS, 'trace_schema_version': TRACE_SCHEMA_VERSION,
        'minimum_relevance': .7, 'configured_sample_count': 1, 'sample_count': 0,
        'required_available': 2, 'required_selected': None, 'missing_from_catalogue': None,
        'wrong_selected': None, 'fallback_reason': None, 'core_calls_used': None,
        'attempted_selector_delegations': 0, 'http_attempt_ceiling': 1,
        'actual_http_count': 'UNKNOWN; core_calls_used includes local work',
        'validated_response_received': False, 'observed_usage': None, 'nominal_usd': None,
        'nominal_rate_usd_per_million_input': '0.042', 'currency': 'USD',
        'limit_cents': 500, 'reserved_cents': 0, 'reconciled_cents': 0,
        'actual_cost': 'UNRECONCILED', 'cap_evidence': 'OWNER-CONFIRMED',
        'provider_account_independently_verified': False,
        'account_confirmation': 'invoker confirms this key belongs to the owner-limited prepaid account; '
                                'remaining <= USD 5, no auto-refill, service halts at credit exhaustion',
        'replay_independent_expectations_passed': False,
        'generation': 'deterministic zero operations', 'channel': 'local development',
        'artifact_bytes': False, 'production_go': False,
    }
    try:
        # Test fixture imports and setup occur only on explicit invocation. No real
        # Bootstrap/config/env files or deployment database are opened.
        import test_context
        fixture = test_context.ContextTests('runTest')
        try:
            fixture.setUp()
            fixture.seed('cucina')
            history = fixture.seed('bagno')
            required = frozenset(record['id'] for record in history['contexts'])
            baseline = fixture.core(test_context.OperationsModel(()))
            before = {'history': baseline.inspect_history(), 'view': baseline.current_view(),
                      'events': baseline.trusted_events()}
            if len(required) != 2:
                raise ValueError('fixture cardinality failed')
            config = SelectionConfig(adapter='jev', model_id='jev-1.13.0',
                                     secret_reference='typesafe', minimum_relevance=.7)
            # Only the explicitly acknowledged live path resolves a process secret.
            # This fixed mapping never loads .env or the deployment's configuration.
            references = {'typesafe': 'PT_TYPESAFE_API_KEY'}
            key = os.environ.get(references['typesafe'])
            if not key:
                report['error'] = 'secret_unavailable'
                return report
            selector = GuardedJevSelector(config, key, trust_primary_group=trust_primary_group)
            fixture.config = replace(fixture.config, selection=config, secret_references=references)
            core = fixture.core(test_context.OperationsModel(()), selector=selector)
            payload = fixture.message(text='cucina e bagno')
            case = capture(core, lambda recorder: recorder.accept(payload))
            report['sample_count'] = len(case.steps)
            entries = case.steps[0]['selection']
            if entries:
                entry = entries[0]
                catalogue = set(entry.get('candidate_revisions', {}))
                selected = set(entry.get('selected_ids', ()))
                report.update(required_selected=len(required & selected),
                              missing_from_catalogue=len(required - catalogue),
                              wrong_selected=len(selected - required),
                              fallback_reason=entry.get('fallback_reason'),
                              core_calls_used=entry.get('calls_used'),
                              selector_latency_ms=entry.get('latency_ms'))
                response = entry.get('response')
                if response and response.get('input_tokens') is not None:
                    report['validated_response_received'] = True
                    report['observed_usage'] = {'input_tokens': response['input_tokens'],
                                                'output_tokens': response['output_tokens']}
                    report['nominal_usd'] = format(Decimal(response['input_tokens']) *
                                                   Decimal('0.042') / Decimal(1000000), 'f')
            _expect_domain(case.expected, before, required)
            recomputed = _offline_replay(case)
            if not recomputed.matched:
                raise ValueError('replay mismatch')
            _expect_domain(recomputed.actual, before, required)
            report['replay_independent_expectations_passed'] = True
            report['status'] = 'passed'
        finally:
            fixture.doCleanups()
    except Exception:
        # Never serialize/print provider exception payloads, chained causes or keys.
        report['error'] = 'domain_or_replay_failed'
    finally:
        if selector is not None:
            report['reserved_cents'] = selector.reserved_cents
            report['attempted_selector_delegations'] = selector.attempted_dispatches
            report['selector_failure'] = selector.failure
            report['reservation_refusal_reason'] = selector.reservation_refusal_reason
        report['elapsed_ms'] = round((monotonic() - started) * 1000, 3)
        try:
            if case is not None:
                _write_private(directory, 'capture.json', case.to_dict())
            _write_private(directory, 'receipt.json', report)
        finally:
            os.close(directory)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='explicit paid attempt; separately approved invocation required')
    parser.add_argument('--output', type=Path, help='new private directory; existing paths are refused')
    parser.add_argument('--ack-owner-prepaid-usd5', action='store_true', help=(
        'confirm PT_TYPESAFE_API_KEY belongs to the owner-confirmed prepaid account: remaining <= USD 5, '
        'no auto-refill, service stops at exhaustion; not independently verified; full USD 5 held permanently'))
    parser.add_argument('--ack-trusted-primary-group', action='store_true', help=(
        'trust the OS account primary group like the owner on owned nonprivate ancestry; '
        'group members can rename/delete ancestry; private directories remain 0700'))
    args = parser.parse_args(argv)
    if not args.live:
        if args.output is not None or args.ack_owner_prepaid_usd5 or args.ack_trusted_primary_group:
            parser.error('--output and account/group acknowledgements require --live')
        print(json.dumps({'status': 'offline preflight only', 'live_authorized': False,
                          'cap_evidence': 'OWNER-CONFIRMED', 'provider_account_independently_verified': False,
                          'reservation_created': False, 'secret_resolved': False,
                          'paid_execution': 'requires separate approval, matching-account acknowledgement and new private output'}))
        return 0
    if args.output is None or not args.ack_owner_prepaid_usd5:
        parser.error('--live requires --output and --ack-owner-prepaid-usd5 for the matching owner-limited account')
    try:
        report = _run(args.output, trust_primary_group=args.ack_trusted_primary_group)
        print('synthetic smoke ' + report['status'] + '; actual cost UNRECONCILED; no adoption decision')
        return 0 if report['status'] == 'passed' else 1
    except Exception:
        print('synthetic smoke failed; no retry; retain any reservation; inspect private evidence')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
