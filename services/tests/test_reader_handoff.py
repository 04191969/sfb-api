import sys,json,hashlib,tempfile,shutil,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]/'diagnostics'))
from prove_reader_handoff import run,packet_for
class Tests(unittest.TestCase):
 def source(self):
  text='WTB orange screwdriver';return dict(title=text,text=text,url='https://example.org/thread',source_sha256=hashlib.sha256(text.encode()).hexdigest(),posted_at='2014-01-29')
 def test_hash_guard(self):
  s=self.source();s['text']='changed'
  with self.assertRaises(ValueError):packet_for(s)
 def check(self,fail=False):
  with tempfile.TemporaryDirectory() as d:
   h=Path(d);root=h/'SFB'/'seeker';rel=root/'releases'/'test';rel.mkdir(parents=True);(root/'active.txt').write_text('test')
   shutil.copytree(Path(__file__).parents[1]/'seeker'/'discovery_reader',rel/'discovery_reader')
   src=h/'source.json';src.write_text(json.dumps({'buyers':[self.source()]}));calls=[]
   def send(body):
    calls.append(body)
    if fail:raise TimeoutError('simulated transport')
    packet=json.loads(body['input'])
    answer=dict(state='ready',direction=packet['direction'],interpretation='Buyer wants a screwdriver',quotations=[dict(id='q1',text=packet['author_text'],supports='Buyer text')],queries=[dict(text='orange screwdriver for sale',rationale='Find counterpart',quotation_ids=['q1'])],unresolved=[],source_limitations=packet['source_limitations'],source=packet['source'])
    return dict(status='completed',model=body['model'],usage={},output=[dict(type='message',content=[dict(type='output_text',text=json.dumps(answer))])])
   r=run(h,src,send);self.assertEqual(len(calls),1);self.assertFalse(r['production_changed']);self.assertEqual(r['database_writes'],0)
   self.assertEqual(r['state'],'failed_or_uncertain' if fail else 'reader_handoff_validated')
   self.assertTrue(list(h.glob('SFB_Reader_Handoff_*/SEND_THIS_REPORT.zip')))
   self.assertEqual(r['availability'],'unconfirmed')
   for n in list(sys.modules):
    if n=='discovery_reader' or n.startswith('discovery_reader.'):sys.modules.pop(n)
 def test_valid_contract(self):self.check()
 def test_transport_no_retry(self):self.check(True)
if __name__=='__main__':unittest.main()
