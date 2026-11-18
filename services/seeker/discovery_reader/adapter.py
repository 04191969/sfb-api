"""Transport and handoff only; preserves existing discovery reasoning/contract."""
import json
from . import contract

def payload(packet):
    body=json.loads(contract.discovery_body(packet))
    settings=json.loads((contract.SPEC/'settings.json').read_text(encoding='utf-8'))
    body['max_output_tokens']=settings['DISCOVERY_OUTPUT_TOKENS']
    body['instructions']+=settings['EXACT_QUOTE_INSTRUCTION']
    return body

def read(source,direction,client,sb,scope,audit=lambda record:None):
    packet=dict(direction=direction,author_text=source['text'],source=dict(url=source['url'],capture_id=source['source_sha256'],text_sha256=source['source_sha256']),source_limitations=['Automatically selected original-post region; source age and current availability are unconfirmed.'])
    body=payload(packet)
    record={'request':body};audit(record)
    transport=client.with_options(max_retries=0,timeout=300.0)
    kind,reservation=scope['_reserve_ai'](sb,False)
    if kind!='db':raise scope['ReservationClosed']('Discovery requires database usage reservation')
    try:
        response=transport.responses.create(**body)
        raw=response.model_dump(mode='json')
    except Exception:
        record['state']='transport_failed_or_uncertain';audit(record)
        raise scope['ReservationClosed']('Discovery response uncertain; no automatic retry') from None
    record['response']=raw;audit(record)
    if not scope['_settle_ai'](sb,reservation,'succeeded'):
        raise scope['ReservationClosed']('Discovery usage settlement could not be confirmed')
    value=contract.response_object(contract.encode(raw))
    return contract.validate_output(value,packet)
