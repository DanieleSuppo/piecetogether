"""Fixed synthetic catalogue diagnostics. Default and --capture are offline/inert.

PYTHONPATH=tests python3 -m jev_catalogue_probe --capture PRIVATE_CAPTURE
Live runs only in a short-lived child with all explicit financial/hash flags.
No counterfactual identity is ever passed to Core.
"""
import argparse
from collections import Counter
from dataclasses import asdict, replace
from datetime import datetime
from decimal import Decimal
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import re
from time import monotonic
import uuid

from piecetogether.context import (Assessment, ContextRecord, SelectionConfig, SelectionRequest,
                                   SelectionResult, encoded, validate_selection)
from piecetogether.contracts import unique_record
from piecetogether.jev import QUESTION, _payload
from piecetogether.jev_smoke import FollowupJevSelector, _new_output, _write_private

_ENV_PATH = Path(__file__).resolve().parents[1] / '.env'
MODEL = 'jev-1.13.0'
RUBRIC = 'context-relevance-v1'
RULE = {'all_position_differences_lte': .01, 'any_content_difference_gte': .10,
        'conclusion': 'position_tracking_suspected; not certified; stop further calls'}
PROFILE = [('canonical_concept', 'note'), ('context', '"context" {"label": "bagno"}'),
    ('entity', '{"label": "bagno"}'), ('claim', '"bagno segreto"'),
    ('context', '"context" {"label": "cucina"}'), ('entity', '{"label": "cucina"}'),
    ('claim', '"cucina segreto"'), ('emergent_concept', 'preferenza opzione favorita'),
    ('emergent_concept', 'preferenza opzione favorita'), ('communication', 'Yes'),
    ('communication', 'bagno'), ('artifact', 'bagno Evidence'), ('communication', 'Yes'),
    ('communication', 'cucina'), ('artifact', 'cucina Evidence')]


def _hash(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def _restore(data):
    fields = dict(data)
    for key in ('candidates', 'permitted_context'):
        fields[key] = tuple(ContextRecord(**{**r, 'reasons': tuple(r['reasons']),
            'disclosure_purposes': tuple(r['disclosure_purposes'])}) for r in fields[key])
    return SelectionRequest(**fields)


def _synthetic_request(data):
    if set(data) != set(SelectionRequest.__dataclass_fields__):
        raise ValueError('invalid synthetic profile')
    comm = json.loads(data['communication_json'], object_pairs_hook=unique_record)
    fixed = {'actor_id': 'a1', 'attachments': [], 'channel': 'development', 'direction': 'inbound',
        'idempotency_key': 'm5', 'recipient': None, 'references': [], 'reply_to': None,
        'sender': 's1', 'sent_at': '2026-10-06T12:00:00Z', 'subject': None,
        'text': 'cucina e bagno', 'thread_id': None, 'transport_message_id': None,
        'transport_reply_to': None}
    if (set(comm) != set(fixed) | {'id', 'received_at'}
            or any(comm[k] != v for k, v in fixed.items())):
        raise ValueError('invalid synthetic communication')
    if str(uuid.UUID(comm['id'])) != comm['id']:
        raise ValueError('invalid synthetic identity')
    datetime.fromisoformat(comm['received_at'])
    request = _restore(data)
    if (request.rubric_version != RUBRIC or request.minimum_relevance != .7
            or type(request.remaining_calls) is not int or request.remaining_calls != 3
            or type(request.remaining_tokens) is not int or request.remaining_tokens != 65536
            or type(request.timeout_ms) is not int or not 1 <= request.timeout_ms <= 2000
            or request.permitted_context or len(request.candidates) != 15
            or Counter((r.kind, r.summary) for r in request.candidates) != Counter(PROFILE)
            or [(r.kind, r.summary) for r in request.candidates] != PROFILE):
        raise ValueError('invalid synthetic catalogue')
    ids = [r.id for r in request.candidates]
    if len(set(ids)) != 15:
        raise ValueError('invalid synthetic identities')
    for r in request.candidates:
        if (re.fullmatch(r'[0-9a-f]{64}', r.revision) is None
                or r.mandatory is not False or r.reasons or r.disclosure_purposes or r.payload_json != ''):
            raise ValueError('invalid synthetic record')
        if r.kind == 'canonical_concept':
            valid = r.id == 'canonical:note'
        elif r.kind == 'emergent_concept':
            valid = re.fullmatch(r'concept:[0-9a-f]{64}', r.id) is not None
        elif r.kind == 'artifact':
            valid = re.fullmatch(r'artifact:[0-9a-f]{64}', r.id) is not None
        else:
            valid = str(uuid.UUID(r.id)) == r.id
        if not valid:
            raise ValueError('invalid synthetic record identity')
    # Payloads are not provider-visible. Discard them, never copy snapshots or
    # arbitrary capture fields to a diagnostic plan/output.
    return replace(request, candidates=tuple(replace(r, payload_json='') for r in request.candidates))


def _scores(response, request):
    result = SelectionResult(**{**response, 'ranked_ids': tuple(response['ranked_ids']),
        'assessments': tuple(Assessment(**a) for a in response['assessments'])})
    validate_selection(result, request)
    if (result.provider != 'typesafe' or result.model != MODEL
            or result.input_tokens is None or result.output_tokens is None
            or {a.record_id for a in result.assessments} != {r.id for r in request.candidates}
            or any(a.primitive != 'noul' for a in result.assessments)):
        raise ValueError('invalid control response')
    return {a.record_id: a.value for a in result.assessments}


def build_plan(capture_data):
    """Compile the closed existing fixture; freeze mappings before reading scores."""
    if len(capture_data['steps']) != 1 or len(capture_data['steps'][0]['selection']) != 1:
        raise ValueError('invalid synthetic capture')
    entry = capture_data['steps'][0]['selection'][0]
    original = _synthetic_request(entry['request'])
    original_ids = sorted(r.id for r in original.candidates)
    by_id = {r.id: r for r in original.candidates}
    readable = {old: f'{i:02d}-' + re.sub('[^a-z0-9]+', '-',
                    by_id[old].kind + '-' + by_id[old].summary.casefold()).strip('-')
                for i, old in enumerate(original_ids)}
    opaque_ids = sorted(str(uuid.uuid5(uuid.NAMESPACE_URL, 'piecetogether-jev-catalogue-v1:' + old))
                        for old in original_ids)
    opaque = dict(zip(original_ids, opaque_ids))
    identity = dict(zip(original_ids, original_ids))
    cases = []
    for label, mapping, reverse in (('original-reversed', identity, True),
            ('readable-original', readable, False), ('readable-reversed', readable, True),
            ('opaque-original', opaque, False), ('opaque-reversed', opaque, True)):
        records = tuple(replace(r, id=mapping[r.id]) for r in original.candidates)
        request = replace(original, candidates=tuple(reversed(records)) if reverse else records,
                          timeout_ms=min(original.timeout_ms, 1000))
        inverse = {v: k for k, v in mapping.items()}
        payload = _payload(request, MODEL)
        wire_order = list(json.loads(encoded(payload))['questions'])
        if len(inverse) != 15 or [inverse[k] for k in wire_order] != original_ids:
            raise ValueError('question order changed')
        cases.append({'label': label, 'request': asdict(request), 'to_original': inverse,
                      'request_digest': _hash(asdict(request)), 'provider_body_digest': _hash(payload)})
    baseline_digest = _hash(_payload(original, MODEL))
    if (len({c['provider_body_digest'] for c in cases}) != 5
            or baseline_digest in {c['provider_body_digest'] for c in cases}):
        raise ValueError('repeated provider input')
    required = sorted(r.id for r in original.candidates if r.kind == 'context')
    plan = {'version': 'jev-catalogue-followup-v1', 'model': MODEL, 'rubric': RUBRIC,
            'question': QUESTION, 'minimum_relevance': .7, 'analysis_rule': RULE,
            'scope': 'post-hoc-informed synthetic diagnostics; not held-out; no adoption',
            'max_new_calls': 5, 'prior_recorded_attempts': 1, 'batch_deadline_ms': 15000,
            'source_capture_digest': _hash(capture_data),
            'source_provider_body_digest': baseline_digest, 'required_ids': required,
            'control_order': [r.id for r in original.candidates], 'cases': cases}
    # Only after all logical identities/mappings and variants are frozen.
    plan['control_scores'] = _scores(entry['response'], original)
    plan['plan_hash'] = _hash(plan)
    return plan


def _alignment(left_order, left, right_order, right):
    # Compare decimal representations, without widening the frozen boundaries
    # to compensate for binary float subtraction (e.g. .44 - .43).
    content = [abs(Decimal(str(left[k])) - Decimal(str(right[k]))) for k in left_order]
    position = [abs(Decimal(str(left[a])) - Decimal(str(right[b]))) for a, b in zip(left_order, right_order)]
    suspected = all(v <= Decimal('.01') for v in position) and any(v >= Decimal('.10') for v in content)
    return {'content_differences': [float(v) for v in content],
            'position_differences': [float(v) for v in position],
            'position_tracking_suspected': suspected, 'certified': False}


def _metrics(scores, required, result):
    selected = {k for k, v in scores.items() if v >= .7}
    measured = result.outcome == 'completed'
    return {'required_available': len(required),
            'required_selected': len(selected & set(required)) if measured else None,
            'required_recall': len(selected & set(required)) / len(required) if measured else None,
            'wrong_selected': len(selected - set(required)) if measured else None,
            'false_accept_rate': None}


def _load_key():
    from piecetogether.core import _load_local_env
    class SelectedEnvironment(dict):
        def setdefault(self, name, value=None):
            if name == 'PT_TYPESAFE_API_KEY':
                return super().setdefault(name, value)
            return None
    # The existing loader owns dotenv syntax. Only the selected credential may
    # enter this isolated child's environment; no deployment config is loaded.
    previous = os.environ
    selected = SelectedEnvironment()
    try:
        os.environ = selected
        _load_local_env(_ENV_PATH)
        key = selected.get('PT_TYPESAFE_API_KEY')
    finally:
        os.environ = previous
    if not key:
        raise ValueError('secret unavailable')
    return key


def _run(plan, output, *, trust_primary_group=False):
    directory = _new_output(output, trust_primary_group=trust_primary_group)
    started = monotonic()
    selector = None
    report = {'status': 'failed', 'error': None, 'plan_hash': plan['plan_hash'],
        'scope': plan['scope'], 'model': MODEL, 'rubric': RUBRIC, 'minimum_relevance': .7,
        'sample_count': 0, 'held_out': False, 'production_go': False, 'actual_cost': 'UNRECONCILED',
        'cap_evidence': 'OWNER-CONFIRMED', 'provider_account_independently_verified': False,
        'limit_cents': 500, 'reserved_cents': None, 'reconciled_cents': 0,
        'trust_primary_group': trust_primary_group is True, 'prior_recorded_attempts': None,
        'required_prior_recorded_attempts': 1,
        'invocation_recorded_slots': 0, 'invocation_selector_delegations': 0,
        'global_recorded_attempts': None, 'global_attempt_upper_bound': 6,
        'actual_http_count': None, 'observed_usage': None, 'nominal_usd': None,
        'nominal_rate_usd_per_million_input': '0.042', 'currency': 'USD',
        'false_accept_rate': None, 'cases': [], 'position_tracking_suspected': None,
        'stop_rule': 'not_tested'}
    try:
        _write_private(directory, 'plan.json', plan)
        requests = tuple(_restore(c['request']) for c in plan['cases'])
        selector = FollowupJevSelector(SelectionConfig(adapter='jev', model_id=MODEL,
            secret_reference='typesafe', minimum_relevance=.7), _load_key(), requests,
            plan_hash=plan['plan_hash'], ack_no_off_credit_charges=True,
            trust_primary_group=trust_primary_group)
        prior_order, prior_scores = plan['control_order'], plan['control_scores']
        input_tokens = output_tokens = 0
        for i, (case, request) in enumerate(zip(plan['cases'], requests)):
            if monotonic() + request.timeout_ms / 1000 > started + 15:
                raise ValueError('batch deadline')
            result = selector.select(request)
            scores = {case['to_original'][a.record_id]: a.value for a in result.assessments}
            if set(scores) != set(plan['control_order']):
                raise ValueError('missing assessments')
            order = [case['to_original'][r.id] for r in request.candidates]
            analysis = _alignment(prior_order, prior_scores, order, scores) if i in (0, 2, 4) else None
            if analysis is not None:
                report['stop_rule'] = 'triggered' if analysis['position_tracking_suspected'] else 'not_triggered'
                if analysis['position_tracking_suspected']:
                    report['position_tracking_suspected'] = True
            evidence = {'label': case['label'], 'scores_by_original_id': scores,
                'catalogue_order_by_original_id': order,
                'scores_by_catalogue_position': [scores[k] for k in order], 'analysis': analysis,
                'outcome': result.outcome, 'metrics': _metrics(scores, plan['required_ids'], result),
                'input_tokens': result.input_tokens, 'output_tokens': result.output_tokens,
                'latency_ms': result.latency_ms}
            report['cases'].append(evidence)
            report['sample_count'] = len(report['cases'])
            input_tokens += result.input_tokens
            output_tokens += result.output_tokens
            report['observed_usage'] = {'input_tokens': input_tokens, 'output_tokens': output_tokens,
                                       'scope': 'validated responses only; not billing reconciliation'}
            report['nominal_usd'] = format(Decimal(input_tokens) * Decimal('0.042') / Decimal(1000000), 'f')
            _write_private(directory, f'case-{i + 1}.json', evidence)
            if analysis and analysis['position_tracking_suspected']:
                report['status'] = 'stopped_position_tracking_suspected'
                break
            prior_order, prior_scores = order, scores
        else:
            report['status'] = 'completed_diagnostics_only'
    except Exception:
        report['error'] = 'diagnostic_stopped'
    finally:
        if selector is not None:
            report.update(reserved_cents=selector.reserved_cents,
                invocation_recorded_slots=selector.recorded_slots,
                invocation_selector_delegations=selector.attempted_dispatches,
                selector_failure=selector.failure)
            if selector.reserved_cents == 500:
                report['prior_recorded_attempts'] = 1
            if selector.prior_batch_attempts_known:
                report['global_recorded_attempts'] = 1 + selector.recorded_slots
        report['elapsed_ms'] = round((monotonic() - started) * 1000, 3)
        try:
            _write_private(directory, 'receipt.json', report)
        finally:
            os.close(directory)
    return report


def _child(plan, output, trust):
    try:
        report = _run(plan, output, trust_primary_group=trust)
        code = 0 if report['status'] != 'failed' else 1
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
        # Do not deserialize snapshots or echo arbitrary supplied capture data.
        with args.capture.open() as source:
            data = json.load(source, object_pairs_hook=unique_record)
        plan = build_plan(data)
        if not args.live:
            print(encoded(plan))
            return 0
        if args.expected_plan_hash != plan['plan_hash']:
            raise ValueError('plan mismatch')
        # No credential reaches parent, argv, pipe, logs or artifacts. POSIX fork
        # also lets temp-only tests inherit their private env/ledger overrides.
        child = multiprocessing.get_context('fork').Process(target=_child,
            args=(plan, args.output, args.ack_trusted_primary_group))
        child.start()
        child.join(20)
        if child.is_alive():
            child.terminate()
            child.join()
            return 1
        print('catalogue diagnostics ended; actual cost UNRECONCILED; no adoption; no retry')
        return 0 if child.exitcode == 0 else 1
    except Exception:
        print('catalogue diagnostics refused or stopped; no retry; retain all evidence')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
