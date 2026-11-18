"""Replay the saved vest/jacket failure without paid calls or database writes."""
import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import test_continuation as fixture
from seeker_v2.adapter import assess
from seeker_v2.reading_contract import validate,shape
from seeker_v2.shared_rules import qualify
DATA=json.loads((Path(__file__).parent/'fixtures/negotiable_budget.json').read_text(encoding='utf-8'))
PAIR=DATA['pair']
READING=json.loads(DATA['response']['output'][-1]['content'][0]['text'])

class Tests(unittest.TestCase):
    def test_exact_failed_response_now_no_send(self):
        _,q=assess(DATA['response'],PAIR)
        self.assertEqual(q['decision'],'No Send')
        self.assertEqual(q['reason'],'Established requested-product conflict')
    def test_flexible_missing_quote_still_invalid(self):
        r=copy.deepcopy(READING);r['budget']['buyer_quote']=''
        with self.assertRaisesRegex(ValueError,'buyer quote'):validate(r,PAIR,shape)
    def test_firm_missing_amount_still_invalid(self):
        r=copy.deepcopy(READING);r['budget']['mode']='firm'
        with self.assertRaisesRegex(ValueError,'Budget needs amount'):validate(r,PAIR,shape)
    def test_negotiable_without_amount_is_not_a_price_cap(self):
        r=fixture.readout(2);pair={'buyer_text':fixture.BUYER+' Price negotiable.','seller_text':fixture.SELLER}
        r['budget'].update(mode='flexible',limit=None,currency=None,basis='unknown',buyer_quote='Price negotiable.')
        validate(r,pair,shape)
        q=qualify(r);self.assertEqual(q['decision'],'Send')
        self.assertIn('Price is negotiable; no numeric budget stated',q['disclosures'])
        r['budget']['limit']='1'
        validate(r,pair,shape);self.assertEqual(qualify(r)['decision'],'Send')
    def test_saved_mismatch_continues_to_next_pair(self):
        original=fixture.Runner.transport_call
        def pairs(run,key):
            for index,pair in enumerate([PAIR,{'buyer_text':fixture.BUYER,'seller_text':fixture.SELLER}]):
                yield tuple({'url':f'https://example.com/{side}/{index}','capture_id':str(index),'author_text':pair[side+'_text'], 'text_sha256':fixture.c.sha(pair[side+'_text'].encode()),'limitations':[]} for side in ('buyer','seller'))
        def transport(run,kind,body):
            if PAIR['buyer_text']==json.loads(json.loads(body)['input'])['buyer_text']:
                return json.dumps(DATA['response']).encode()
            return original(run,kind,body)
        with tempfile.TemporaryDirectory() as tmp,patch.object(fixture.Runner,'iter_pairs',pairs),patch.object(fixture.Runner,'transport_call',transport):
            run=fixture.Runner(Path(tmp)/'run',stocks=[2]);report=run.execute('offline')
            self.assertEqual(report['state'],'completed',report.get('reason'))
            self.assertEqual([a['matcher_answer']['decision'] for a in report['assessments']],['No Send','Send'])
if __name__=='__main__':unittest.main()
