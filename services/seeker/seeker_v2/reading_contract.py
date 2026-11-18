"""Common reading schema and exact-evidence checks. These do not prove semantic truth."""
import json
from .shared_rules import amount

def obj(properties):return {'type':'object','properties':properties,'required':list(properties),'additionalProperties':False}
def enum(*values):return {'type':'string','enum':list(values)}
STRING={'type':'string'}
NULL_STRING={'type':['string','null']}
INT={'type':['integer','null']}
def arr(x):return {'type':'array','items':x}
EVIDENCE=arr(obj({'source':enum('buyer','seller'),'quote':STRING}))
SCHEMA=obj({
 'buyer_role':obj({'value':enum('buy','sell','mixed_buy_sell','unclear'),'evidence':EVIDENCE}),
 'selected_request':obj({'description':STRING,'buyer_quote':STRING}),
 'offer':obj({'kind':enum('individual','catalog','profile','multiple_unresolved','unknown'),'description':STRING,'evidence':EVIDENCE}),
 'product':obj({'status':enum('matches','conflict','unknown'),'explanation':STRING,'evidence':EVIDENCE}),
 'availability':obj({'value':enum('available','listed','trade_only','ended','unknown'),'basis':enum('listing_text','api','historical','snippet','unknown'),'evidence':EVIDENCE}),
 'budget':obj({'mode':enum('none','firm','flexible','unknown'),'limit':NULL_STRING,'currency':NULL_STRING,'basis':enum('item','delivered','unknown'),'buyer_quote':STRING}),
 'quantity':obj({'requested':INT,'items_per_unit':INT,'units_available':INT,'max_units_per_buyer':INT,'buyer_quote':STRING,'evidence':EVIDENCE}),
 'cost':obj({'unit_item_price':NULL_STRING,'shipping_for_required_purchase':NULL_STRING,'delivered_total_for_required_purchase':NULL_STRING,'currency':NULL_STRING,'evidence':EVIDENCE}),
 'checks':arr(obj({'description':STRING,'buyer_quote':STRING,'priority':enum('hard','preference','permission'),'relation':enum('positive','prohibition'),'status':enum('satisfied','conflict','unknown'),'evidence':EVIDENCE,'explanation':STRING})),
 'disclosures':arr(STRING),'assumptions':arr(STRING)
})


def validate(r,pair,shape_validator):
    shape_validator(r,SCHEMA)
    def walk(v):
        if isinstance(v,dict):
            if set(v)=={'source','quote'}:
                if not v['quote'] or v['quote'] not in pair[v['source']+'_text']:raise ValueError('Evidence quote absent from source')
            for k,x in v.items():
                if k=='buyer_quote' and x and x not in pair['buyer_text']:raise ValueError('Buyer quote absent from source')
                walk(x)
        elif isinstance(v,list):
            for x in v:walk(x)
    walk(r)
    def evidence(items,source):return any(e['source']==source for e in items)
    if r['buyer_role']['value']!='unclear' and not evidence(r['buyer_role']['evidence'],'buyer'):raise ValueError('Role needs buyer-source evidence')
    if r['buyer_role']['value'] in ('buy','mixed_buy_sell') and not r['selected_request']['buyer_quote']:raise ValueError('Selected request needs buyer quote')
    if r['offer']['kind']!='unknown' and not evidence(r['offer']['evidence'],'seller'):raise ValueError('Offer kind needs source evidence')
    if r['product']['status']!='unknown' and not all(evidence(r['product']['evidence'],s) for s in ('buyer','seller')):raise ValueError('Product relation needs both sources')
    if r['availability']['value']!='unknown' and not evidence(r['availability']['evidence'],'seller'):raise ValueError('Availability needs source evidence')
    bu=r['budget'];cap=amount(bu['limit'])
    if bu['mode']=='firm' and (cap is None or not bu['buyer_quote']):raise ValueError('Budget needs amount and buyer quote')
    if bu['mode']=='flexible' and not bu['buyer_quote']:raise ValueError('Flexible budget needs buyer quote')
    if bu['mode']=='none' and cap is not None:raise ValueError('No-budget reading contains a cap')
    q=r['quantity']
    for field in ('requested','items_per_unit','units_available','max_units_per_buyer'):
        v=q[field]
        if v is not None and (type(v)!=int or v<(1 if field in ('requested','items_per_unit') else 0)):raise ValueError('Invalid quantity')
    if q['requested'] is not None and q['requested']>1 and not q['buyer_quote']:raise ValueError('Multiple-item requirement needs buyer quote')
    if any(q[k] is not None for k in ('items_per_unit','units_available','max_units_per_buyer')) and not evidence(q['evidence'],'seller'):raise ValueError('Quantity needs seller evidence')
    for k in ('unit_item_price','shipping_for_required_purchase','delivered_total_for_required_purchase'):
        if amount(r['cost'][k]) is not None and not evidence(r['cost']['evidence'],'seller'):raise ValueError('Cost needs source evidence')
    for c in r['checks']:
        if not c['buyer_quote']:raise ValueError('Requirement needs buyer quote')
        buyer_permission = c['priority']=='permission' and c['status']=='satisfied' and any(e['source']=='buyer' and e['quote']==c['buyer_quote'] for e in c['evidence'])
        if c['status']!='unknown' and not evidence(c['evidence'],'seller') and not buyer_permission:raise ValueError('Satisfied/conflicting requirement needs seller evidence')
    return r


def shape(value,schema):
    types=schema['type'];types=types if isinstance(types,list) else [types]
    actual='null' if value is None else 'boolean' if type(value)==bool else 'integer' if type(value)==int else 'string' if isinstance(value,str) else 'array' if isinstance(value,list) else 'object' if isinstance(value,dict) else 'unsupported'
    if actual not in types:raise ValueError('Schema type mismatch')
    if 'enum' in schema and value not in schema['enum']:raise ValueError('Schema enum mismatch')
    if actual=='object':
        if set(value)!=set(schema['properties']):raise ValueError('Schema properties mismatch')
        for k,v in value.items():shape(v,schema['properties'][k])
    if actual=='array':
        for v in value:shape(v,schema['items'])
