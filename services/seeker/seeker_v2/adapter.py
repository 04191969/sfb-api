"""Compatibility boundary; discovery and the frozen historical worker stay intact."""
import hashlib
import json
from pathlib import Path

from .reading_contract import validate, shape
from .shared_rules import qualify

ROOT = Path(__file__).resolve().parent


def template():
    for name, expected in json.loads((ROOT / 'INTEGRITY.json').read_text(encoding='utf-8')).items():
        raw = (ROOT / name).read_bytes().replace(b'\r\n', b'\n')
        if hashlib.sha256(raw).hexdigest() != expected:
            raise RuntimeError('Live Matches V2 integrity check failed: ' + name)
    return json.loads((ROOT / 'request_template.json').read_text(encoding='utf-8'))


def assess(raw, pair):
    if raw.get('status') != 'completed':
        raise ValueError('V2 response did not complete')
    text = ''.join(part.get('text', '') for item in raw.get('output', [])
                   for part in item.get('content', []) if part.get('type') == 'output_text')
    reading = json.loads(text)
    validate(reading, pair, shape)
    return reading, qualify(reading)


def install(scope):
    """Install only in the per-run restored consumer namespace, never global Seeker."""
    body_template = template()
    original_search = scope['search_listings']
    records = {}
    assessments = []
    original_outcome = scope['search_outcome']
    original_print = scope.get('print', print)

    def log(*args, **kwargs):
        if args and isinstance(args[0], str) and args[0].startswith('    Not a qualifying product:') and assessments:
            latest = assessments[-1]
            args = ('    Assessment withheld: ' + latest['reason'], *args[1:])
        original_print(*args, **kwargs)

    def outcome(listings, incomplete=''):
        summary = original_outcome(listings, incomplete)
        held = [a for a in assessments if a['product'] == 'matches' and a['decision'] == 'Incomplete']
        if held:
            reasons = '; '.join(dict.fromkeys(a['reason'] for a in held))
            summary = f'{len(held)} product match(es) held for review: {reasons}. ' + summary
        return summary

    def search(*args, **kwargs):
        result = original_search(*args, **kwargs)
        records.clear()
        for record in result['listings']:
            records.setdefault(record.get('text', ''), []).append(record)
        return result

    def evaluate(buyer_text, seller_text, client, sb=None, allow_file_counter=True):
        # A search excerpt is not the opened page. Give V2 the full current page
        # already fetched by the unchanged reader, with no new navigation.
        matches = records.get(seller_text, [])
        if len(matches) != 1:
            raise scope['ReservationClosed']('V2 assessment incomplete: seller evidence is not uniquely associated with an opened listing')
        record = matches[0]
        page = record.get('page_text') or ''
        if not page.strip():
            raise scope['ReservationClosed']('V2 assessment incomplete: current listing page text is missing')
        pair = {'buyer_text': buyer_text, 'seller_text': page}
        body = dict(body_template, input=json.dumps(pair, ensure_ascii=False))
        # Construct the no-retry client before reserving; no call is made here.
        transport = client.with_options(max_retries=0, timeout=300.0)
        kind, reservation = scope['_reserve_ai'](sb, allow_file_counter)
        try:
            response = transport.responses.create(**body)
            raw = response.model_dump(mode='json')
        except Exception:
            # A timeout can be billed. Leave the reservation open for existing
            # uncertain-call recovery, instead of falsely recording a failure.
            raise scope['ReservationClosed']('V2 assessment incomplete: model request failed or its outcome is uncertain; no automatic retry') from None
        if kind == 'db':
            if not scope['_settle_ai'](sb, reservation, 'succeeded'):
                raise scope['ReservationClosed']('V2 assessment incomplete: usage settlement could not be confirmed')
        else:
            scope['record_ai_call']()
        try:
            reading, qualification = assess(raw, pair)
        except (ValueError, TypeError, KeyError):
            raise scope['ReservationClosed']('V2 assessment incomplete: response failed evidence/schema validation or did not complete; no automatic retry') from None
        accepted = qualification['decision'] == 'Send'
        assessments.append(qualification)
        reason = 'V2: ' + qualification['decision'] + '. ' + qualification['reason']
        reason += ' Product evidence: ' + reading['product']['explanation']
        if qualification['disclosures']:
            reason += ' Disclosures: ' + '; '.join(qualification['disclosures'])
        # V2 does not output a probability. The old card requires a numeric
        # score; explicitly disclose this binary compatibility value.
        reason += ' Score is a binary qualification indicator, not model confidence.'
        print('    ' + reason, flush=True)
        item = scope['requested_item'](buyer_text).splitlines()[0]
        identity = {'make': '', 'model': item, 'part': ''}
        if accepted and sb is not None:
            # Reuse existing exact market identity; never guess a manufacturer
            # or split a model name using the first word of the request.
            key = scope['market_label']('', item)[2]
            rows = sb.table('sections').select('id,name,make,model').execute().data or []
            found = [r for r in rows if scope['market_label'](r.get('make'), r.get('model'), r.get('name'))[2] == key]
            if found:
                identity.update(make=found[0].get('make') or '', model=found[0].get('model') or item)
        return dict(identity, match=accepted, confidence=100 if accepted else 0, reason=reason, reading=reading, qualification=qualification)

    scope['search_listings'] = search
    scope['ai_evaluate_match'] = evaluate
    scope['search_outcome'] = outcome
    scope['print'] = log
