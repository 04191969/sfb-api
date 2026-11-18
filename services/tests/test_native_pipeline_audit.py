"""Offline full-path wiring audit. Synthetic seller/answer, not live accuracy proof."""
import copy,html,json,sys,tempfile,types,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'seeker'))
import native_reader,native_discovery as n
from discovery_reader import contract
FIX=Path(__file__).parent/'fixtures/discovery_leg'
class Tests(unittest.TestCase):
 def test_real_reader_discovery_validator_matcher_and_publish_boundary(self):
  packet=json.loads(json.loads((FIX/'request.json').read_text())['input'])
  discovery=contract.response_object(contract.encode(json.loads((FIX/'response.json').read_text())))
  buyer=packet['author_text'];title,post=buyer.split('\n',1)
  seller='Polk RT35i speakers pair for sale. Two speakers. Asking $100 USD. Add to cart. This listing is for a single pair of Polk RT35i bookshelf speakers, offered together as one purchase. The cabinets and drivers are included.'
  evidence=lambda side,quote:dict(source=side,quote=quote)
  b=evidence('buyer',title);s=evidence('seller','Two speakers.')
  reading=dict(buyer_role=dict(value='buy',evidence=[b]),selected_request=dict(description=title,buyer_quote=title),offer=dict(kind='individual',description='Pair of Polk RT35i',evidence=[s]),product=dict(status='matches',explanation='Same speakers, pair.',evidence=[b,s]),availability=dict(value='available',basis='listing_text',evidence=[evidence('seller','Add to cart.')]),budget=dict(mode='none',limit=None,currency=None,basis='unknown',buyer_quote=''),quantity=dict(requested=2,items_per_unit=2,units_available=1,max_units_per_buyer=None,buyer_quote=title,evidence=[s]),cost=dict(unit_item_price='100',shipping_for_required_purchase=None,delivered_total_for_required_purchase=None,currency='USD',evidence=[evidence('seller','Asking $100 USD.')]),checks=[],disclosures=[],assumptions=[])
  fake={}
  for name,attr in [('supabase','create_client'),('openai','OpenAI'),('ddgs','DDGS')]:
   fake[name]=types.ModuleType(name);setattr(fake[name],attr,object)
  calls=[];published=[];settled=[];queries=[]
  with patch.dict(sys.modules,fake),tempfile.TemporaryDirectory() as tmp:
   scope=native_reader.load(tmp)
   url='https://reverb.com/item/123456-polk-rt35i-speakers'
   def search(q,**kw):
    queries.append(q)
    return [dict(title='WTB Two Polk RT35i speakers',href=packet['source']['url'],body='Wanted speakers')] if q=='WTB broad' else [dict(title='Polk RT35i speakers pair for sale',href=url,body=seller)]
   scope['ddg_text']=search
   scope['read_listing_html']=lambda u:('<h1>'+html.escape(title)+'</h1><div class="message-body">'+html.escape(post)+'</div>') if u==packet['source']['url'] else '<html><h1>Polk RT35i speakers</h1><p>'+seller+'</p></html>'
   scope['time']=types.SimpleNamespace(sleep=lambda _:None)
   scope['_reserve_ai']=lambda *a:('db','reservation')
   scope['_settle_ai']=lambda *a:settled.append(a) or True
   def send(**body):
    calls.append(body);inp=json.loads(body['input'])
    if 'author_text' in inp:
     answer=copy.deepcopy(discovery);answer['source']=inp['source'];answer['source_limitations']=inp['source_limitations']
    else:
     self.assertEqual(inp['buyer_text'],buyer);self.assertIn(seller,inp['seller_text']);answer=reading
    return types.SimpleNamespace(model_dump=lambda **kw:dict(model='gpt-5.4-2026-03-05',status='completed',output=[dict(type='message',role='assistant',content=[dict(type='output_text',text=json.dumps(answer))])]))
   client=types.SimpleNamespace(with_options=lambda **kw:types.SimpleNamespace(responses=types.SimpleNamespace(create=send)))
   report=dict(state='running',searches=[],buyers=[],seller_searches=[],assessments=[],skipped=[],incomplete=[])
   n.cycle(scope,client,{},report,lambda:None,lambda *a:published.append(a) or ('local-review',True),['WTB broad'])
   self.assertEqual(report['state'],'completed',report)
   self.assertEqual(len(calls),2);self.assertEqual(len(settled),2);self.assertEqual(len(published),1)
   self.assertEqual(queries,['WTB broad']+[q['text'] for q in discovery['queries']])
 def test_plain_wanted_ad_layout_is_currently_rejected(self):
  with self.assertRaisesRegex(ValueError,'could not be isolated'):
   n.buyer_page('<h1>WTB Polk RT35i speakers</h1><article>I want two speakers.</article>','https://example.com/ad/1')
 def test_directory_lead_is_currently_skipped(self):
  from lead_navigation import classify_buyer
  kind,_=classify_buyer(dict(url='https://example.com/forums/wanted',title='Wanted classifieds',snippet='WTB speakers'))
  self.assertEqual(kind,'directory')
if __name__=='__main__':unittest.main()
