"""Whole-text matching candidate. Standard library only; CHECK is offline."""
import datetime, getpass, hashlib, json, os, sys, urllib.error, urllib.request, zipfile
from pathlib import Path
ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'OUTPUT'
RESULTS = ROOT / 'RESULTS_UNSEEN_CROSS_CATEGORY_24.zip'

def duplicate_guard(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON property: ' + key)
        result[key] = value
    return result

def parse(text):
    return json.loads(text, object_pairs_hook=duplicate_guard)

def read(name):
    return parse((ROOT / name).read_text(encoding='utf8'))

def write(path, value):
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf8')
    os.replace(temp, path)

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf8')

def manifest_id():
    return hashlib.sha256(canonical(read('MANIFEST.json'))).hexdigest()

def check_freeze():
    manifest = read('MANIFEST.json')
    for name, expected in manifest['files'].items():
        if digest(ROOT / name) != expected:
            raise ValueError('Frozen file changed: ' + name)
    data = read('DATA/pairs.json')
    if len(data) != read('settings.json')['maximum_requests']:
        raise ValueError('Pair/request count mismatch')
    if len({p['id'] for p in data}) != len(data):
        raise ValueError('Duplicate pair ID')
    for p in data:
        if not p['id'].isalnum():
            raise ValueError('Unsafe pair ID')
        inputs(p)

def check_lock():
    lock = read('REVIEW_LOCK.json')
    if lock.get('status') != 'reviewed_before_run' or lock.get('manifest_sha256') != manifest_id():
        raise ValueError('Review lock does not match this freeze')

def inputs(pair):
    # Only these two original strings enter the model. No prior readings or key.
    return {'buyer_text': read('DATA/buyers.json')[pair['buyer']],
            'seller_text': read('DATA/sellers.json')[pair['seller']]}

def validate_shape(value, schema, path='$'):
    t = schema['type']
    if t == 'object':
        if not isinstance(value, dict): raise ValueError(path + ': object required')
        if set(value) != set(schema['required']): raise ValueError(path + ': missing/extra fields')
        for key, sub in schema['properties'].items(): validate_shape(value[key], sub, path+'.'+key)
    elif t == 'array':
        if not isinstance(value, list): raise ValueError(path + ': array required')
        for i, v in enumerate(value): validate_shape(v, schema['items'], path+f'[{i}]')
    elif t == 'string':
        if not isinstance(value, str): raise ValueError(path + ': string required')
        if 'enum' in schema and value not in schema['enum']: raise ValueError(path + ': invalid enum')
    else: raise ValueError('Unsupported schema type')

def validate_answer(answer, data):
    validate_shape(answer, read('schema.json'))
    for name in ['reason', 'requested_purchase', 'offered_purchase']:
        if not answer[name].strip(): raise ValueError(name + ' is empty')
    records = {}
    for evidence in answer['evidence']:
        eid, quote, source = evidence['id'], evidence['quote'], evidence['source']
        if not eid.strip() or eid in records: raise ValueError('Empty/duplicate evidence ID')
        if not quote.strip() or quote not in data[source+'_text']:
            raise ValueError('Evidence quote absent from original: ' + eid)
        if not evidence['supports'].strip(): raise ValueError('Empty evidence explanation')
        records[eid] = evidence
    decision = answer['decision']
    if decision != 'Incomplete' and {e['source'] for e in records.values()} != {'buyer','seller'}:
        raise ValueError('Completed judgment requires buyer and seller evidence')
    ids = answer['seller_note_ids']
    if len(ids) != len(set(ids)): raise ValueError('Duplicate note reference')
    for eid in ids:
        if eid not in records or records[eid]['source'] != 'seller':
            raise ValueError('Seller note references missing or buyer evidence')
    if decision != 'Send' and (ids or answer['system_notes']):
        raise ValueError('Withheld result must have empty buyer notes')
    if decision == 'Send' and not ids: raise ValueError('Send requires seller disclosure evidence')
    if any(not x.strip() for name in ['system_notes','unresolved','assumptions'] for x in answer[name]):
        raise ValueError('Empty text entry')
    # Deduplicate exact passages, preserve seller order. No semantic reinterpretation.
    quotes = sorted({records[eid]['quote'] for eid in ids}, key=lambda q: data['seller_text'].index(q))
    # Selected nested passages are already present in the longer selected passage.
    quotes = [q for q in quotes if not any(q != other and q in other for other in quotes)]
    return {'Decision':decision, 'Reason':answer['reason'], 'SellerNote':'\n'.join(quotes),
            'SystemNotes':answer['system_notes'], 'Unresolved':answer['unresolved'],
            'Assumptions':answer['assumptions'],
            'Validation':'format and exact evidence only; semantic correctness unscored'}

def payload(data, arm="B"):
    settings = read('settings.json')
    return {'model':settings['model'], 'instructions':(ROOT/'instruction.txt').read_text(encoding='utf8'),
            'input':json.dumps(data,ensure_ascii=False), 'reasoning':{'effort':settings['reasoning_arms'][arm]},
            'max_output_tokens':settings['max_output_tokens'], 'store':False, 'tools':[],
            'text':{'format':{'type':'json_schema','name':'whole_text_match','strict':True,'schema':read('schema.json')}}}

def reservation(body):
    s = read('settings.json')
    # Conservative byte-based planning allowance, not billed usage or a billing cap.
    return (len(body)+4096)*s['assumed_input_per_million']/1e6 + s['max_output_tokens']*s['assumed_output_per_million']/1e6

def send_request(key, body):
    request = urllib.request.Request('https://api.openai.com/v1/responses', data=body,
              headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'})
    with urllib.request.urlopen(request, timeout=300) as response:
        return response.read()

def package():
    with zipfile.ZipFile(RESULTS,'w',zipfile.ZIP_DEFLATED) as z:
        names = list(read('MANIFEST.json')['files']) + ['MANIFEST.json','REVIEW_LOCK.json']
        for name in names:
            if (ROOT/name).is_file(): z.write(ROOT/name,name)
        if OUT.exists():
            for p in sorted(OUT.glob('*')):
                if p.is_file(): z.write(p,'OUTPUT/'+p.name)

def execute(key, caller=send_request):
    # Exclusive mkdir prevents accidental repeats and concurrent second runs.
    OUT.mkdir()
    pairs = read('DATA/pairs.json')
    rows = [{'pair':p['id'],'status':'not_run','raw_decision':None,'accepted_result':None,'error':''} for p in pairs]
    calls = []
    reserved = 0.0
    write(OUT/'RESULTS.json',rows)
    write(OUT/'RUN_CONTEXT.json',{'started_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
          'manifest_sha256':manifest_id(), 'provenance':'Fresh cross-category 24; medium budget-range candidate; one attempt per pair',
          'semantic_score':'not scored'})
    try:
        for i, pair in enumerate(pairs):
            pid, row = pair['id'], rows[i]
            body = json.dumps(payload(inputs(pair),pair["arm"]),ensure_ascii=False).encode('utf8')
            cost = reservation(body)
            settings = read('settings.json')
            if len(calls) >= settings['maximum_requests'] or reserved+cost > settings['local_reserve_usd']:
                row['error']='Local reservation/request limit; no call made'
                break
            (OUT/(pid+'.request.json')).write_bytes(body)
            reserved += cost
            row['status']='request_started'
            calls.append({'pair':pid,'status':'attempted','reservation_usd':cost})
            write(OUT/'CALLS.json',calls); write(OUT/'RESULTS.json',rows)
            print('Reading',pid,flush=True)
            try:
                raw = caller(key,body)
            except Exception as e:
                reason = 'HTTP '+str(e.code) if isinstance(e,urllib.error.HTTPError) else type(e).__name__
                row.update(status='transport_failure',error=reason+'; no retry; no model decision recorded')
                calls[-1]['status']='failed_or_unknown_completion'
                global_stop = isinstance(e,urllib.error.HTTPError) and e.code in (400,401,403,404)
                if isinstance(e,urllib.error.HTTPError) and e.code == 429:
                    try:
                        err = json.loads(e.read().decode('utf8')).get('error',{})
                        global_stop = err.get('code') in ('insufficient_quota','billing_hard_limit_reached') or err.get('type') == 'insufficient_quota'
                    except Exception:
                        pass
                if global_stop: row['status']='request_stop'
                write(OUT/'CALLS.json',calls); write(OUT/'RESULTS.json',rows)
                if global_stop:
                    write(OUT/'RUN_STOP.json',{'pair':pid,'error':reason,'no_retry':True,'remaining':'not_run'})
                    break
                continue
            (OUT/(pid+'.response.json')).write_bytes(raw)
            try:
                response = parse(raw.decode('utf8'))
                calls[-1].update(status=response.get('status','unknown'),usage=response.get('usage'))
                if response.get('status') != 'completed':
                    detail=response.get('incomplete_details') or {}
                    row.update(status='truncation' if detail.get('reason')=='max_output_tokens' else 'response_failure', error='API response '+str(response.get('status'))+': '+str(detail.get('reason','')),accepted_result=None)
                    write(OUT/'CALLS.json',calls); write(OUT/'RESULTS.json',rows)
                    continue
                text = ''.join(c['text'] for m in response.get('output',[]) for c in m.get('content',[]) if c.get('type')=='output_text')
                (OUT/(pid+'.answer.txt')).write_text(text,encoding='utf8')
                answer = parse(text)
                write(OUT/(pid+'.answer.json'),answer)
                if isinstance(answer,dict): row['raw_decision']=answer.get('decision')
                row['accepted_result']=validate_answer(answer,inputs(pair))
                row['status']='validated_unscored'
                print(pid, row['accepted_result']['Decision'], '|', row['accepted_result']['Reason'],flush=True)
            except Exception as e:
                row.update(status='validation_failure',error=str(e),accepted_result=None)
            write(OUT/'CALLS.json',calls); write(OUT/'RESULTS.json',rows)
    finally:
        write(OUT/'CALLS.json',calls); write(OUT/'RESULTS.json',rows)
        from report import make_report
        make_report(ROOT)
        package()
        print('Saved',RESULTS.name)

def check():
    check_freeze()
    import test_offline
    result = test_offline.run_tests()
    if not result.wasSuccessful(): raise ValueError('Offline tests failed')
    bodies = [json.dumps(payload(inputs(p),p["arm"]),ensure_ascii=False).encode('utf8') for p in read('DATA/pairs.json')]
    total = sum(reservation(b) for b in bodies)
    if total > read('settings.json')['local_reserve_usd']: raise ValueError('Reservation too small for planned calls')
    print('Offline checks pass. AI accuracy unmeasured. Planned calls:',len(bodies))
    print('Estimated reservation at assumed rates: $%.2f; local limit $%.2f.'%(total,read('settings.json')['local_reserve_usd']))

def main():
    from launch import main as launch_main
    launch_main()

if __name__ == '__main__':
    main()
