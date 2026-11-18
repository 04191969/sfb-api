import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import test_continuation as f
import v2_bridge
DATA=json.loads((Path(__file__).parent/'fixtures/invalid_matcher_quote.json').read_text(encoding='utf-8'))
class Tests(unittest.TestCase):
    def replay(self,next_pair=True,timeout=False):
        original=f.Runner.transport_call;calls=[]
        def pairs(run,key):
            examples=[DATA['pair']]
            if next_pair:examples.append(dict(buyer_text=f.BUYER,seller_text=f.SELLER))
            for i,pair in enumerate(examples):
                yield tuple(dict(url=f'https://example.com/{side}/{i}',capture_id=str(i),author_text=pair[side+'_text'],text_sha256=f.c.sha(pair[side+'_text'].encode()),limitations=[]) for side in ('buyer','seller'))
        def transport(run,kind,body):
            calls.append(kind)
            if timeout:raise TimeoutError('response timeout')
            if len(calls)==1:return json.dumps(DATA['response']).encode()
            return original(run,kind,body)
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        with patch.object(f.Runner,'iter_pairs',pairs),patch.object(f.Runner,'transport_call',transport):
            run=f.Runner(Path(tmp.name)/'run',stocks=[2]);report=run.execute('offline')
        return run,report,calls
    def test_actual_quote_remains_invalid(self):
        reading=json.loads(DATA['response']['output'][0]['content'][0]['text'])
        with self.assertRaisesRegex(v2_bridge.InvalidMatcherReading,'Evidence quote absent'):v2_bridge.wrap(reading,DATA['pair'])
    def test_actual_failure_is_saved_and_next_pair_assessed_once(self):
        run,r,calls=self.replay()
        self.assertEqual(r['state'],'completed',r.get('reason'))
        self.assertEqual(r['decision'],'Send');self.assertEqual(calls,['matcher','matcher'])
        self.assertEqual(len(r['assessments']),1)
        self.assertEqual(len(r['invalid_matcher_readings']),1)
        self.assertIn('Evidence quote absent',r['invalid_matcher_readings'][0]['reason'])
        self.assertTrue((run.folder/'models/001-matcher.response.json').exists())
        self.assertEqual(len(run.ledger.summary()['operations']),2)
    def test_only_invalid_reading_is_incomplete(self):
        _,r,calls=self.replay(False)
        self.assertEqual(r['state'],'assessment_incomplete');self.assertFalse(r['pipeline_completed'])
        self.assertEqual(r['assessments'],[]);self.assertEqual(len(calls),1)
    def test_transport_failure_still_stops_without_retry(self):
        _,r,calls=self.replay(timeout=True)
        self.assertEqual(r['state'],'stopped');self.assertEqual(len(calls),1)
if __name__=='__main__':unittest.main()
