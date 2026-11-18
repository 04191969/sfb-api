import copy,json,sys,unittest,types,hashlib
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'seeker'))
from discovery_reader import adapter,contract
FIX=Path(__file__).parent/'fixtures/discovery_leg'

class Tests(unittest.TestCase):
 def setUp(self):
  self.request=json.loads((FIX/'request.json').read_text())
  self.packet=json.loads(self.request['input'])
  self.raw=json.loads((FIX/'response.json').read_text())
  self.output=contract.response_object(contract.encode(self.raw))
 def test_existing_request_preserved_except_preexisting_allowance_and_quote_rule(self):
  settings=json.loads((contract.SPEC/'settings.json').read_text())
  want=copy.deepcopy(self.request);want['max_output_tokens']=settings['DISCOVERY_OUTPUT_TOKENS'];want['instructions']+=settings['EXACT_QUOTE_INSTRUCTION']
  self.assertEqual(adapter.payload(self.packet),want)
  self.assertEqual(want['reasoning'],{'effort':'medium'})
  self.assertEqual(want['max_output_tokens'],6000)
 def test_saved_successful_reading_validates_unchanged(self):
  self.assertEqual(contract.validate_output(self.output,self.packet),self.output)
  self.assertTrue(self.output['queries'])
 def test_same_contract_accepts_seller_direction_without_changing_rules(self):
  packet=copy.deepcopy(self.packet);packet['direction']='find_buyers'
  output=copy.deepcopy(self.output);output['direction']='find_buyers'
  self.assertEqual(contract.validate_output(output,packet)['direction'],'find_buyers')
  self.assertEqual(adapter.payload(packet)['reasoning'],self.request['reasoning'])
 def test_invalid_quotes_bindings_restrictions_and_hold_rejected(self):
  for mutation in [lambda o:o['quotations'][0].update(text='invented words'),lambda o:o['source'].update(url='https://wrong.example'),lambda o:o['queries'][0].update(text='site:example.com speakers'),lambda o:o.update(state='hold')]:
   out=copy.deepcopy(self.output);mutation(out)
   with self.assertRaises(ValueError):contract.validate_output(out,self.packet)
 def test_incomplete_response_rejected(self):
  raw=copy.deepcopy(self.raw);raw['status']='incomplete'
  with self.assertRaisesRegex(ValueError,'not completed'):contract.response_object(contract.encode(raw))
 def invoke(self,raw=None,error=None):
  calls=[];settled=[];audits=[]
  source=dict(text=self.packet['author_text'],url=self.packet['source']['url'],source_sha256=self.packet['source']['text_sha256'])
  def send(**body):
   calls.append(body)
   if error:raise error
   # Rebind the frozen valid answer to this packet's source/limitations.
   response=copy.deepcopy(raw or self.raw)
   packet=json.loads(body['input']);out=copy.deepcopy(self.output)
   out['source']=packet['source'];out['source_limitations']=packet['source_limitations']
   for item in response.get('output',[]):
    for c in item.get('content',[]):
     if c.get('type')=='output_text':c['text']=json.dumps(out)
   return types.SimpleNamespace(model_dump=lambda **kw:response)
  client=types.SimpleNamespace(with_options=lambda **kw:types.SimpleNamespace(responses=types.SimpleNamespace(create=send)))
  scope={'_reserve_ai':lambda sb,files:('db','reservation-1'),'_settle_ai':lambda *a:settled.append(a) or True,'ReservationClosed':RuntimeError}
  return lambda:adapter.read(source,'find_sellers',client,'database',scope,lambda r:audits.append(copy.deepcopy(r))),calls,settled,audits
 def test_transport_exact_original_text_and_success_settled_once(self):
  run,calls,settled,audits=self.invoke();result=run()
  self.assertEqual(len(calls),1);self.assertEqual(len(settled),1)
  self.assertEqual(json.loads(calls[0]['input'])['author_text'],self.packet['author_text'])
  self.assertEqual(result['queries'],self.output['queries'])
  self.assertIn('response',audits[-1])
 def test_transport_uncertain_no_retry_no_false_settlement(self):
  run,calls,settled,audits=self.invoke(error=TimeoutError())
  with self.assertRaisesRegex(RuntimeError,'uncertain'):run()
  self.assertEqual(len(calls),1);self.assertEqual(settled,[])
  self.assertEqual(audits[-1]['state'],'transport_failed_or_uncertain')
 def test_received_incomplete_is_settled_but_not_used(self):
  raw=copy.deepcopy(self.raw);raw['status']='incomplete'
  run,calls,settled,audits=self.invoke(raw=raw)
  with self.assertRaisesRegex(ValueError,'not completed'):run()
  self.assertEqual(len(settled),1)
if __name__=='__main__':unittest.main()
