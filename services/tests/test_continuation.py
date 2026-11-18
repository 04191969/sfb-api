"""Offline continuation replay: real queues, V2 validation and ledger; no network."""
import copy,json,os,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,os.environ['SFB_TEST_PACKAGE'])
sys.path.insert(0,str(ROOT/'seeker'))
sys.path.insert(1,str(ROOT.parent/'worker'))
import continuation as c
from automatic_run import Run
import v2_bridge
from test_live_matches_v2 import reading

BUYER='Looking for a pair of Example Q2 guitar instruments.'
SELLER='Example Q2 guitar. One guitar for sale. Buy It Now. Tested and working musical instrument for sale.'
def row(i):return {'url':f'https://www.ebay.com/itm/{800000000000+i}','title':'Example Q2 guitar for sale','snippet':'Example Q2 guitar'}
def readout(stock):
    r=reading();b={'source':'buyer','quote':BUYER};s={'source':'seller','quote':SELLER}
    r['buyer_role']['evidence']=[b];r['selected_request']['buyer_quote']=BUYER
    r['offer']['evidence']=[s];r['product']['evidence']=[b,s];r['availability']['evidence']=[s]
    r['quantity'].update(requested=2,units_available=stock if isinstance(stock,int) else 2,buyer_quote=BUYER,evidence=[b,s])
    if isinstance(stock,str):r['availability']['value']=stock
    return r
class Browser:
    trace=[];requests=0
    def __enter__(self):return self
    def __exit__(self,*a):pass
    def boundary(self):pass
    def visit(self,url,purpose,parent=None):
        return {'url':url,'capture_id':url.rsplit('/',1)[-1], 'source_text':SELLER,
            'title':'Example Q2 guitar for sale','extraction':'single_article','single_listing':True,'product':True,'links':[]}
class Runner(c.ContinuationMixin,Run):
    def __init__(self,folder,queries=None,stocks=None,buyers=1,error=False):
        self.calls=[];self.queries=queries or {'Example guitar for sale':[row(1),row(2)],'Example guitar used':[]}
        self.stocks=iter(stocks or [1,2]);self.nbuyers=buyers;self.error=error
        super().__init__(folder,browser_factory=Browser,transport=self.transport_call,handoff=v2_bridge.handoff)
        self.api=SimpleNamespace(authenticate=lambda:None)
    def iter_buyers(self):
        for i in range(self.nbuyers):
            yield {'url':f'https://example.com/wtb/{i}','capture_id':str(i),'author_text':BUYER,
                   'text_sha256':c.sha(BUYER.encode()),'limitations':[]}
    def search(self,query,purpose):
        self.calls.append(('search',query));return self.queries[query]
    def transport_call(self,kind,body):
        self.calls.append(('model',kind))
        if self.error and kind=='matcher':raise TimeoutError('offline timeout')
        answer={'state':'ready','queries':[{'text':q} for q in self.queries]} if kind=='discovery' else readout(next(self.stocks))
        return json.dumps({'model':json.loads(body)['model'],'status':'completed','usage':{'input_tokens':10,'output_tokens':10},
            'output':[{'type':'message','content':[{'type':'output_text','text':json.dumps(answer)}]}]}).encode()
    def model(self,kind,body,key,reservation):
        answer=super().model(kind,body,key,reservation)
        return v2_bridge.wrap(answer,json.loads(json.loads(body)['input'])) if kind=='matcher' else answer

class ContinuationTests(unittest.TestCase):
    def run_case(self,**kw):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        run=Runner(Path(tmp.name)/'run',**kw)
        with patch.object(c,'validate_output',lambda output,packet:output):report=run.execute('offline')
        return run,report
    def test_no_send_then_send_without_restarting_search(self):
        run,r=self.run_case()
        self.assertEqual(r['state'],'completed',r.get('reason'))
        self.assertEqual([x['matcher_answer']['decision'] for x in r['assessments']],['No Send','Send'])
        self.assertEqual(run.calls.count(('search','Example guitar for sale')),1)
        self.assertNotIn(('search','Example guitar used'),run.calls)
        self.assertEqual(len(r['model_calls']),3)
        self.assertEqual(len(list((run.folder/'models').glob('*.response.json'))),3)
    def test_next_query_and_duplicate_skipped(self):
        run,r=self.run_case(queries={'Example guitar for sale':[row(1)],'Example guitar used':[row(1),row(2)]})
        self.assertEqual(r['state'],'completed',r.get('reason'))
        self.assertEqual(len(r['assessments']),2)
        self.assertIn(('search','Example guitar used'),run.calls)
    def test_next_buyer_after_exhaustion(self):
        run,r=self.run_case(queries={'Example guitar for sale':[row(1)]},stocks=[1,2],buyers=2)
        self.assertEqual(r['state'],'completed',r.get('reason'))
        self.assertEqual(r['buyers_evaluated'],2)
        self.assertEqual(len(r['model_calls']),4)
    def test_exhaustion_keeps_all_rejections(self):
        run,r=self.run_case(stocks=[1,1])
        self.assertEqual(r['state'],'no_qualifying_match',r.get('reason'))
        self.assertEqual(len(r['assessments']),2)
        self.assertIn(('search','Example guitar used'),run.calls)
    def test_nine_seller_limit_is_not_reset(self):
        run,r=self.run_case(queries={'Example guitar for sale':[row(i) for i in range(11)]},stocks=[1]*11)
        self.assertEqual(r['state'],'run_limit_reached',r.get('reason'))
        self.assertEqual(len(r['assessments']),9)
        self.assertEqual(r['seller_counts']['page_visits_attempted'],9)
    def test_transport_failure_stops_without_retry_or_next_candidate(self):
        run,r=self.run_case(error=True)
        self.assertEqual(r['state'],'stopped')
        self.assertEqual(run.calls.count(('model','matcher')),1)
        self.assertTrue(any(x['state']=='charge_unknown' for x in run.ledger.summary()['operations']))
    def test_reject_and_incomplete_continue_without_rule_changes(self):
        for stock,decision in [('ended','Reject'),('unknown','Incomplete')]:
            run,r=self.run_case(stocks=[stock,2])
            self.assertEqual(r['state'],'completed',r.get('reason'))
            self.assertEqual([x['matcher_answer']['decision'] for x in r['assessments']],[decision,'Send'])
    def test_cap_remains_shared_across_buyers(self):
        run,r=self.run_case(queries={'Example guitar for sale':[row(i) for i in range(5)]},stocks=[1]*10,buyers=2)
        self.assertEqual(r['state'],'run_limit_reached')
        self.assertEqual(len(r['assessments']),9)
    def test_invalid_reading_stops_instead_of_skipping_model_failure(self):
        with patch.object(v2_bridge,'wrap',side_effect=ValueError('invalid quote')):
            run,r=self.run_case()
        self.assertEqual(r['state'],'stopped')
        self.assertEqual(run.calls.count(('model','matcher')),1)
    def test_connector_keeps_partial_evidence_and_incomplete_status(self):
        from seeker_connector import connect_run
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        job={'id':'00000000-0000-0000-0000-000000000111','status':'running','search_query':None}
        saved=[];finished=[]
        factory=lambda folder:Runner(folder,queries={'Example guitar for sale':[row(i) for i in range(11)]},stocks=[1]*11)
        with patch.object(c,'validate_output',lambda out,packet:out):
            result=connect_run(job,tmp.name,factory,'offline','1.00',lambda *args:saved.append(args),lambda *args:finished.append(args),v2_bridge)
        self.assertEqual(finished[0][1],'failed')
        self.assertIn('Search incomplete',finished[0][2])
        self.assertEqual(len(saved[0][1]['report']['assessments']),9)
        self.assertTrue(result['published'])
    def test_money_limit_stops_before_dispatch(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        run=Runner(Path(tmp.name)/'run')
        r=run.execute('offline',ceiling='0.001')
        self.assertEqual(r['state'],'run_limit_reached',r.get('reason'))
        self.assertFalse(any(x[0]=='model' for x in run.calls))
if __name__=='__main__':unittest.main()
