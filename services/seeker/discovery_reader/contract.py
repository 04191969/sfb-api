import hashlib,json,re
from pathlib import Path
from . import shape as locked
SPEC=Path(__file__).resolve().parent
MODEL="gpt-5.4-2026-03-05"
def read(name):return json.loads((SPEC/name).read_text(encoding='utf-8'))

def sha(value):return hashlib.sha256(value).hexdigest()

def encode(value):return json.dumps(value,ensure_ascii=False).encode('utf-8')

def need(ok,why):
    if not ok:raise ValueError(why)

def text(value):return isinstance(value,str) and bool(value.strip())

def validate_input(packet):
    locked.validate_shape(packet,read('discovery_input.schema.json'))
    need(packet['direction'] in ('find_sellers','find_buyers'),'local validation failure: invalid direction')
    need(text(packet['author_text']),'empty original text')
    for value in packet['source'].values():need(text(value),'empty source binding')
    need(packet['source']['text_sha256']==sha(packet['author_text'].encode('utf-8')),'source text fingerprint mismatch')
    need(all(text(v) for v in packet['source_limitations']),'empty source limitation')
    return packet

def validate_output(value,packet):
    locked.validate_shape(value,read('discovery_output.schema.json'))
    need(value['direction']==packet['direction'],'changed direction')
    need(value['source']==packet['source'],'changed source binding')
    need(text(value['interpretation']),'empty interpretation')
    for k in ('unresolved','source_limitations'):
        need(len(value[k])<=16 and all(text(v) for v in value[k]),'invalid '+k)
    need(all(v in value['source_limitations'] for v in packet['source_limitations']),'source limitation omitted')
    quotes=value['quotations'];need(1<=len(quotes)<=16,'quotation count')
    ids=set()
    for q in quotes:
        need(text(q['id']) and len(q['id'])<=40 and q['id'] not in ids,'quotation identity')
        need(text(q['text']) and q['text'] in packet['author_text'],'invented quotation')
        need(text(q['supports']),'empty quote support');ids.add(q['id'])
    queries=value['queries'];need((1<=len(queries)<=3) if value['state']=='ready' else not queries,'query count/state')
    seen=set()
    for q in queries:
        s=q['text'];key=' '.join(s.casefold().split())
        need(text(s) and s==s.strip() and len(s)<=240 and '\n' not in s and '\r' not in s,'invalid query text')
        need(not re.search(r'https?://|www\.|site:|(?:^|\s)-',s,re.I),'forbidden query restriction or URL')
        need(key not in seen,'duplicate query');seen.add(key)
        need(text(q['rationale']),'empty rationale')
        refs=q['quotation_ids'];need(1<=len(refs)<=16 and len(set(refs))==len(refs) and set(refs)<=ids,'invalid quotation references')
    return value

def discovery_body(packet):
    validate_input(packet)
    return encode(dict(model=MODEL,instructions=(SPEC/'discovery_instruction.txt').read_text(encoding='utf-8'),
        input=json.dumps(packet,ensure_ascii=False),reasoning={'effort':'medium'},max_output_tokens=3000,
        store=False,tools=[],text={'format':{'type':'json_schema','name':'sfb_discovery_v1','strict':True,'schema':read('discovery_output.schema.json')}}))

def response_object(raw):
    r=locked.parse(raw.decode('utf-8'))
    need(r.get('status')=='completed','model response not completed')
    need(r.get('model')==MODEL,'response model identity mismatch')
    chunks=[c['text'] for item in r.get('output',[]) if item.get('type')=='message' for c in item.get('content',[]) if c.get('type')=='output_text']
    need(len(chunks)==1,'one output object required')
    return locked.parse(chunks[0])
