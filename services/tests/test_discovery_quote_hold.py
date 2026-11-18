import json,unittest
from pathlib import Path
from unittest.mock import patch
import test_broad_discovery as f
from continuation import validate_output
from lead_navigation import classify,classify_buyer
DATA=json.loads((Path(__file__).parent/'fixtures/invalid_discovery_quote.json').read_text(encoding='utf-8'))
class Tests(unittest.TestCase):
    def test_saved_quote_still_rejected_without_fuzzy_matching(self):
        with self.assertRaisesRegex(ValueError,'invented quotation'):validate_output(DATA['output'],DATA['packet'])
    def test_company_page_skipped_but_real_wtb_ads_preserved(self):
        for classifier in (classify,classify_buyer):
            self.assertEqual(classifier({'url':DATA['packet']['source']['url'],'title':'About WTB'})[0],'skip')
        for url in ('https://example.com/threads/wtb-about-us-jacket.123/','https://example.com/item/about-us-book'):
            self.assertEqual(classify_buyer({'url':url,'title':'WTB jacket'})[0],'ad')
    def run_invalid(self,all_invalid=False,transport_failure=False):
        fixture=f.Tests();self.addCleanup(fixture.doCleanups)
        original=f.BroadRunner.transport_call;count=[]
        def transport(run,kind,body):
            if kind=='discovery':
                count.append(kind)
                if transport_failure:raise TimeoutError('offline transport timeout')
            raw=original(run,kind,body)
            if kind=='discovery' and (all_invalid or len(count)==1):
                response=json.loads(raw);content=response['output'][0]['content'][0];out=json.loads(content['text'])
                out['quotations'][0]['text']='This text does not exist in the source.'
                content['text']=json.dumps(out);return json.dumps(response).encode()
            return raw
        with patch.object(f.BroadRunner,'transport_call',transport):run,report,_=fixture.run_broad()
        return run,report,count
    def test_invalid_reading_held_then_next_source_reaches_matcher(self):
        run,r,count=self.run_invalid()
        self.assertEqual(r['state'],'completed',r.get('reason'))
        self.assertEqual(len(r['invalid_discovery_readings']),1)
        self.assertEqual(r['decision'],'Send')
        self.assertEqual(len(count),3)
        self.assertEqual(len(r['assessments']),1)
    def test_no_supported_readings_is_incomplete_not_no_matches(self):
        _,r,_=self.run_invalid(all_invalid=True)
        self.assertEqual(r['state'],'search_extraction_failed')
        self.assertIn('invalid readings',r['reason'])
        self.assertEqual(r['assessments'],[])
    def test_transport_error_is_not_swallowed_or_retried(self):
        _,r,count=self.run_invalid(transport_failure=True)
        self.assertEqual(r['state'],'stopped')
        self.assertIn('transport timeout',r['reason']);self.assertEqual(len(count),1)
if __name__=='__main__':unittest.main()
