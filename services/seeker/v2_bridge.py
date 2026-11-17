"""Seeker-owned V2 boundary. Never imports Live Matches files."""
import json
from pathlib import Path
from seeker_v2.adapter import template
from seeker_v2.reading_contract import validate, shape
from seeker_v2.shared_rules import qualify


def payload(pair, unused=None):
    return dict(template(), input=json.dumps(pair, ensure_ascii=False))


def wrap(reading, pair):
    validate(reading, pair, shape)
    qualification = qualify(reading)
    return {'reading': reading, 'qualification': qualification,
            'decision': qualification['decision']}


def validate_answer(answer, pair):
    expected = wrap(answer['reading'], pair)
    if answer != expected:raise ValueError('V2 qualification differs from supplied evidence')
    return {'Decision': expected['decision'], 'Reason': expected['qualification']['reason'],
            'SellerNote': '\n'.join(expected['qualification']['disclosures']), 'SystemNotes': ''}


def handoff(source, folder):
    data = json.loads(Path(source).read_text(encoding='utf-8'))
    pair = {side+'_text': data['pair'][side]['author_text'] for side in ('buyer','seller')}
    validated = validate_answer(data['matcher_answer'], pair)
    if validated != data['validated']:raise ValueError('V2 handoff validation mismatch')
    folder = Path(folder);folder.mkdir(parents=True, exist_ok=False)
    (folder/'HANDOFF_REPORT.json').write_text(json.dumps({'decision': data['matcher_answer']['decision'],
        'approved': False, 'introductions': 0, 'validated': validated}), encoding='utf-8')


def install_market(scope):
    """Market comparisons already supply the selected buyer/seller source text."""
    def evaluate(buyer_text, seller_text, client, sb=None, allow_file_counter=False):
        pair = {'buyer_text': buyer_text, 'seller_text': seller_text}
        transport = client.with_options(max_retries=0, timeout=300)
        kind, reservation = scope['_reserve_ai'](sb, False)
        try:raw = transport.responses.create(**payload(pair)).model_dump(mode='json')
        except Exception:
            raise scope['ReservationClosed']('Seeker V2 request uncertain; no automatic retry') from None
        if not scope['_settle_ai'](sb, reservation, 'succeeded'):
            raise scope['ReservationClosed']('Seeker V2 usage settlement uncertain')
        from seeker_v2.adapter import assess
        try:reading, qualification = assess(raw, pair)
        except (ValueError, TypeError, KeyError):
            raise scope['ReservationClosed']('Seeker V2 response incomplete or invalid; no retry') from None
        answer = wrap(reading, pair)
        return {'match': qualification['decision']=='Send', 'confidence': None,
                'matcher':'seeker-v2', 'decision':qualification['decision'],
                'assessment':answer, 'validated':validate_answer(answer,pair),
                'reason':'Seeker V2: '+qualification['decision']+'. '+qualification['reason']}
    scope['ai_evaluate_match'] = evaluate
    scope['matcher_accepts'] = lambda result: bool(result and result.get('matcher')=='seeker-v2' and result.get('match') and result.get('decision')=='Send')
