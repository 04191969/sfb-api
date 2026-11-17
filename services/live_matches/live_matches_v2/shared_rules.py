"""One deterministic qualification policy for ALL experimental reading adapters."""
from decimal import Decimal, InvalidOperation
import math
POLICY_VERSION='v2-qualification-trade-status-v1'


def amount(v):
    if v is None:return None
    if not isinstance(v,str):raise ValueError('Amounts must be decimal strings or null')
    try:d=Decimal(v)
    except InvalidOperation:raise ValueError('Invalid amount') from None
    if not d.is_finite() or d<0:raise ValueError('Invalid amount')
    return d


def qualify(r):
    """Input must pass reading_contract.validate first. No model name enters this function."""
    reasons=[];disclosures=list(r['disclosures'])
    def finish(decision,reason):return {'policy':POLICY_VERSION,'decision':decision,'reason':reason,'product':r['product']['status'],'disclosures':disclosures}
    if r['buyer_role']['value'] not in ('buy','mixed_buy_sell'):
        return finish('Incomplete','No established buying request in buyer text')
    if r['offer']['kind']!='individual':return finish('Incomplete','No identifiable individual offer')
    if r['availability']['value']=='trade_only':return finish('No Send','Trade only — no purchase offer established for this buying request')
    if r['availability']['value']=='ended':return finish('Reject','Selected offer is ended/unavailable')
    if r['product']['status']=='conflict':return finish('No Send','Established requested-product conflict')
    if r['product']['status']=='unknown':reasons.append('Requested product identity unresolved')
    if r['availability']['value'] not in ('available','listed') or r['availability']['basis'] not in ('listing_text','api'):
        reasons.append('Offer availability unconfirmed by eligible evidence')
    # Hard conflicts outrank missing facts; preferences and permissions never become gates.
    for c in r['checks']:
        if c['priority']!='hard':
            if c['status']!='satisfied':disclosures.append(c['description']+': '+c['status'])
            continue
        if c['status']=='conflict':
            return finish('Reject' if c['relation']=='prohibition' else 'No Send','Required conflict: '+c['description'])
        if c['status']=='unknown':reasons.append('Required fact unknown: '+c['description'])
    q=r['quantity'];required=q['requested'];per=q['items_per_unit'];stock=q['units_available'];limit=q['max_units_per_buyer']
    units=None
    if required is None or per is None:reasons.append('Purchase quantity/contents unresolved')
    else:
        units=(required+per-1)//per
        if stock is not None and stock<units:return finish('No Send','Insufficient stock for required quantity')
        if limit is not None and limit<units:return finish('No Send','Buyer purchase limit prevents required quantity')
        if units>1 and stock is None:reasons.append('Multiple purchasable units not established')
    budget=r['budget'];cost=r['cost'];price=amount(cost['unit_item_price']);ship=amount(cost['shipping_for_required_purchase']);total=amount(cost['delivered_total_for_required_purchase']);cap=amount(budget['limit'])
    if price is None:disclosures.append('Item price unknown')
    if ship is None:disclosures.append('Shipping cost unknown')
    if budget['mode']=='unknown':reasons.append('Buyer budget meaning unresolved')
    elif budget['mode']=='firm':
        if budget['currency'] is None or cost['currency'] is None or budget['currency']!=cost['currency']:
            reasons.append('Budget and offer currency not established as comparable')
        item=price*units if price is not None and units is not None else None
        if budget['basis']=='item':value=item
        elif budget['basis']=='delivered':value=total if total is not None else item+ship if item is not None and ship is not None else None
        else:value=None
        if value is None:reasons.append('Relevant cost unknown for firm budget')
        elif budget['currency'] is not None and budget['currency']==cost['currency'] and value>cap:return finish('No Send','Required purchase exceeds firm '+budget['basis']+' budget')
    elif budget['mode']=='flexible':disclosures.append('Budget is a preference, not a firm ceiling')
    if reasons:return finish('Incomplete','; '.join(reasons))
    return finish('Send','Requested purchase meets the shared qualification checks on supplied evidence')
