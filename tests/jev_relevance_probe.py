"""Five fixed relevance-v2 diagnostics; default/import/capture are offline.

PYTHONPATH=tests python3 -m jev_relevance_probe --capture PRIVATE_SYNTHETIC_CAPTURE
Only explicit live mode starts a bounded child. No arbitrary cases or namespaces.
"""
import argparse
from dataclasses import asdict, replace
from decimal import Decimal
import json
import multiprocessing
import os
from pathlib import Path
from time import monotonic
import uuid

from piecetogether.context import ContextRecord, SelectionConfig, encoded
from piecetogether.contracts import unique_record
from piecetogether.jev import V2_BOUNDARY, V2_QUESTION, V2_RULES, _payload
from piecetogether.jev_smoke import JevV2BoundedSelector, _new_output, _write_private
from piecetogether.proposals import ClaimOperation
import jev_catalogue_probe as catalogue

MODEL = 'jev-1.13.0'
RUBRIC = 'context-relevance-v2'
ROUTING = 'adapter yes>=0.7; no<=0.3; any middle -> application fallback'
TEXTS = ('cucina e bagno', 'Riprendiamo il contesto cucina, non quello bagno.',
         'Buongiorno, grazie.',
         "Mi riferisco all'entità chiamata bagno, non al contesto della conversazione.",
         'Sì, confermo.')


def _identity(label):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, 'piecetogether-jev-relevance-v2:' + label))


def _compile(original, source_digest, prior_hash, prior_digests):
    by_identity = {(r.kind, r.summary): r.id for r in original.candidates
                   if r.kind in ('context', 'entity')}
    cucina = by_identity['context', '"context" {"label": "cucina"}']
    bagno = by_identity['context', '"context" {"label": "bagno"}']
    entity = by_identity['entity', '{"label": "bagno"}']
    expectations = ((cucina, bagno), (cucina,), (), (entity,), ())
    # Same shape/summary/revision as Core's pending_grounding projection. No raw
    # payload is added to the provider boundary or synthetic capture catalogue.
    candidate = asdict(ClaimOperation(_identity('pending-claim'), entity, 'note', 'bagno sintetico', 'p'))
    pending_payload = {'id': _identity('pending-item'), 'grounding_id': _identity('pending-reply'),
        'policy': 'p', 'candidate': candidate, 'source_communication_id': _identity('pending-source')}
    pending = ContextRecord(pending_payload['id'], 'pending_grounding', encoded(candidate),
        catalogue._hash(pending_payload), '', ('structural',), ('grounding',), True)
    cases = []
    for i, (text, expected) in enumerate(zip(TEXTS, expectations)):
        communication = json.loads(original.communication_json)
        if i:
            communication.update(id=_identity('communication-' + str(i + 1)),
                                 idempotency_key='jev-relevance-v2-' + str(i + 1), text=text)
        if i == 4:
            communication['reply_to'] = pending_payload['grounding_id']
        request = replace(original, communication_json=encoded(communication), rubric_version=RUBRIC,
            timeout_ms=min(2000, original.timeout_ms), permitted_context=(pending,) if i == 4 else ())
        body = _payload(request, MODEL)
        cases.append({'label': 'case-' + str(i + 1), 'request': asdict(request),
            'request_digest': catalogue._hash(asdict(request)), 'provider_body': body,
            'provider_body_digest': catalogue._hash(body), 'expected_positive_ids': sorted(expected),
            'expected_negative_ids': sorted(r.id for r in request.candidates if r.id not in expected)})
    if len({c['provider_body_digest'] for c in cases}) != 5:
        raise ValueError('repeated provider input')
    plan = {'version': 'jev-relevance-v2-followup', 'model': MODEL, 'rubric': RUBRIC,
        'question': V2_QUESTION, 'application_boundary': V2_BOUNDARY, 'selection_rules': dict(V2_RULES),
        'yes_threshold': .7, 'no_threshold': .3, 'routing_policy': ROUTING,
        'threshold_basis': 'vendor starting heuristics; not locally calibrated guarantees',
        'scope': 'post-hoc-informed synthetic adapter diagnostics; not held-out; no adoption',
        'max_new_calls': 5, 'required_prior_recorded_attempts': 6, 'global_attempt_upper_bound': 11,
        'batch_deadline_ms': 15000, 'child_lifetime_seconds': 20,
        'source_capture_digest': source_digest, 'source_request': asdict(original),
        'prior_plan_hash': prior_hash, 'prior_request_digests': list(prior_digests), 'cases': cases}
    plan['plan_hash'] = catalogue._hash(plan)
    return plan


def build_plan(capture_data):
    """Closed old profile only; no snapshot deserialization or new external data."""
    # The existing compiler validates the 15-record source, typed control scores
    # and canonical prior plan. Labels below never depend on those scores.
    prior = catalogue.build_plan(capture_data)
    original = catalogue._synthetic_request(capture_data['steps'][0]['selection'][0]['request'])
    return _compile(original, catalogue._hash(capture_data), prior['plan_hash'],
                    [c['request_digest'] for c in prior['cases']])


def _validate_plan(plan):
    original = catalogue._synthetic_request(plan['source_request'])
    expected = _compile(original, plan['source_capture_digest'], plan['prior_plan_hash'],
                        plan['prior_request_digests'])
    if encoded(plan) != encoded(expected):
        raise ValueError('frozen plan changed')


def _run(plan, output, *, trust_primary_group=False):
    # Validate the whole canonical plan before output, key, NSS or ledger access.
    _validate_plan(plan)
    directory = _new_output(output, trust_primary_group=trust_primary_group)
    started = monotonic()
    selector = None
    report = {'status': 'failed', 'error': None, 'plan_hash': plan['plan_hash'],
        'scope': plan['scope'], 'model': MODEL, 'rubric': RUBRIC, 'yes_threshold': .7, 'no_threshold': .3,
        'routing_policy': ROUTING, 'sample_count': 0, 'held_out': False, 'core_end_to_end': False,
        'production_go': False, 'false_accept_rate': None, 'p95_ms': None,
        'actual_cost': 'UNRECONCILED', 'cap_evidence': 'OWNER-CONFIRMED',
        'provider_account_independently_verified': False, 'limit_cents': 500, 'reserved_cents': None,
        'reconciled_cents': 0, 'new_allocation_cents': 0, 'trust_primary_group': trust_primary_group is True,
        'required_prior_recorded_attempts': 6, 'prior_recorded_attempts': None,
        'invocation_recorded_slots': 0, 'invocation_selector_delegations': 0,
        'global_recorded_attempts': None, 'global_attempt_upper_bound': 11, 'actual_http_count': None,
        'observed_usage': None, 'nominal_usd': None, 'nominal_rate_usd_per_million_input': '0.042',
        'currency': 'USD', 'cases': []}
    try:
        _write_private(directory, 'plan.json', plan)
        requests = tuple(catalogue._restore(c['request']) for c in plan['cases'])
        selector = JevV2BoundedSelector(SelectionConfig(adapter='jev', model_id=MODEL,
            rubric_version=RUBRIC, secret_reference='typesafe', minimum_relevance=.7),
            catalogue._load_key(), requests, plan_hash=plan['plan_hash'],
            prior_plan_hash=plan['prior_plan_hash'], prior_request_digests=tuple(plan['prior_request_digests']),
            ack_no_off_credit_charges=True, trust_primary_group=trust_primary_group)
        inputs = outputs = 0
        for i, (case, request) in enumerate(zip(plan['cases'], requests)):
            if monotonic() + request.timeout_ms / 1000 > started + 15:
                raise ValueError('batch deadline')
            result = selector.select(request)
            if monotonic() >= started + 15:
                raise ValueError('batch deadline')
            probabilities = {a.record_id: a.value for a in result.assessments}
            if set(probabilities) != {r.id for r in request.candidates}:
                raise ValueError('missing assessments')
            positive = sorted(k for k, v in probabilities.items() if v >= .7)
            negative = sorted(k for k, v in probabilities.items() if v <= .3)
            ambiguous = sorted(k for k, v in probabilities.items() if .3 < v < .7)
            evidence = {'label': case['label'], 'native_probabilities': probabilities,
                'native_provider_response': result.raw_decisions, 'positive_ids': positive,
                'negative_ids': negative, 'ambiguous_ids': ambiguous, 'routing_policy': ROUTING,
                'adapter_outcome': result.outcome, 'application_fallback': result.outcome == 'abstained',
                'adapter_ranked_ids': list(result.ranked_ids),
                'expected_positive_ids': case['expected_positive_ids'],
                'expected_negative_ids': case['expected_negative_ids'],
                'positive_set_match': positive == case['expected_positive_ids'],
                'negative_set_match': negative == case['expected_negative_ids'],
                'expected_set_match': (sorted(result.ranked_ids) == case['expected_positive_ids']
                                       if result.outcome == 'completed' else None),
                'mandatory_ids_supplied': [r.id for r in request.permitted_context],
                'input_tokens': result.input_tokens, 'output_tokens': result.output_tokens,
                'adapter_latency_ms': result.latency_ms, 'false_accept_rate': None}
            report['cases'].append(evidence)
            report['sample_count'] = len(report['cases'])
            inputs += result.input_tokens
            outputs += result.output_tokens
            report['observed_usage'] = {'input_tokens': inputs, 'output_tokens': outputs,
                'scope': 'validated responses only; not billing reconciliation'}
            report['nominal_usd'] = format(Decimal(inputs) * Decimal('0.042') / Decimal(1000000), 'f')
            _write_private(directory, f'case-{i + 1}.json', evidence)
        report['status'] = 'completed_diagnostics_only'
    except Exception:
        report['error'] = 'diagnostic_stopped'
    finally:
        if selector is not None:
            report.update(reserved_cents=selector.reserved_cents,
                prior_recorded_attempts=selector.verified_prior_recorded_attempts,
                invocation_recorded_slots=selector.recorded_slots,
                invocation_selector_delegations=selector.attempted_dispatches, selector_failure=selector.failure)
            if selector.prior_batch_attempts_known:
                report['global_recorded_attempts'] = 6 + selector.recorded_slots
        report['elapsed_ms'] = round((monotonic() - started) * 1000, 3)
        try:
            _write_private(directory, 'receipt.json', report)
        finally:
            os.close(directory)
    return report


def _child(plan, output, trust):
    try:
        report = _run(plan, output, trust_primary_group=trust)
        code = 0 if report['status'] == 'completed_diagnostics_only' else 1
    except BaseException:
        code = 1
    raise SystemExit(code)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture', type=Path)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--expected-plan-hash')
    parser.add_argument('--ack-owner-prepaid-usd5', action='store_true')
    parser.add_argument('--ack-no-off-credit-charges', action='store_true')
    parser.add_argument('--ack-trusted-primary-group', action='store_true')
    args = parser.parse_args(argv)
    if not args.live and (args.output or args.expected_plan_hash or args.ack_owner_prepaid_usd5
                         or args.ack_no_off_credit_charges or args.ack_trusted_primary_group):
        parser.error('paid flags require --live')
    if args.live and not (args.capture and args.output and args.expected_plan_hash
                         and args.ack_owner_prepaid_usd5 and args.ack_no_off_credit_charges):
        parser.error('--live requires capture, new output, expected plan hash and both financial acknowledgements')
    try:
        if args.capture is None:
            print(encoded({'status': 'offline preflight only', 'live_authorized': False}))
            return 0
        with args.capture.open() as source:
            data = json.load(source, object_pairs_hook=unique_record)
        plan = build_plan(data)
        if not args.live:
            print(encoded(plan))
            return 0
        if args.expected_plan_hash != plan['plan_hash']:
            raise ValueError('plan mismatch')
        child = multiprocessing.get_context('fork').Process(target=_child,
            args=(plan, args.output, args.ack_trusted_primary_group))
        child.start()
        child.join(20)
        if child.is_alive():
            child.terminate()
            child.join()
            return 1
        print('v2 diagnostics ended; actual cost UNRECONCILED; no adoption; no retry')
        return 0 if child.exitcode == 0 else 1
    except Exception:
        print('v2 diagnostics refused or stopped; no retry; retain all evidence')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
