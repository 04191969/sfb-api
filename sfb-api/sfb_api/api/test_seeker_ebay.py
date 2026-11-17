import json,time
from unittest.mock import patch,MagicMock
from django.test import SimpleTestCase,override_settings
from .seeker_ebay import signature,PATH

@override_settings(SUPABASE_KEY='test-worker-key')
class RelayTests(SimpleTestCase):
    def send(self,data,key='test-worker-key',age=0):
        body=json.dumps(data).encode();stamp=str(int(time.time())-age)
        return self.client.post(PATH,body,content_type='application/json',
            HTTP_X_SFB_TIME=stamp,HTTP_X_SFB_SIGNATURE=signature(key,stamp,body))
    @patch('sfb_api.api.seeker_ebay.EbayBrowseClient')
    def test_authentication_no_token_disclosure(self,c):
        c.return_value.token='private-token'
        r=self.send({'operation':'authenticate'})
        self.assertEqual(r.status_code,200);self.assertEqual(r.json(),{'ready':True})
    @patch('sfb_api.api.seeker_ebay.EbayBrowseClient')
    def test_reject_unauthorized_expired_and_arbitrary_url(self,c):
        for kw in [{'key':'wrong'},{'age':121}]:
            self.assertEqual(self.send({'operation':'authenticate'},**kw).status_code,401)
        self.assertEqual(self.send({'operation':'item','item_id':'https://internal/'}).status_code,400)
        self.assertEqual(self.send({'operation':'other'}).status_code,400)
        c.assert_not_called()
    @patch('sfb_api.api.seeker_ebay.EbayBrowseClient')
    def test_query_and_raw_evidence_preserved(self,c):
        raw={'itemSummaries':[{'itemId':'v1|123456789|0','title':'Polk speakers'}]}
        c.return_value._request.return_value.json.return_value=raw
        r=self.send({'operation':'search','query':'Polk RT35i speakers for sale'})
        self.assertEqual(r.json(),raw)
        params=c.return_value._request.call_args.kwargs['params']
        self.assertEqual(params,{'q':'Polk RT35i speakers for sale','limit':50,'filter':'buyingOptions:{FIXED_PRICE|AUCTION}'})
    @patch('sfb_api.api.seeker_ebay.EbayBrowseClient')
    def test_detail_fresh_not_parts_cache(self,c):
        c.return_value._request.return_value.json.return_value={'description':'exact seller text','price':{'value':'10'}}
        r=self.send({'operation':'item','item_id':'v1|123456789|0'})
        self.assertEqual(r.json()['description'],'exact seller text')
        c.return_value.get_item.assert_not_called()
        self.assertTrue(c.return_value._request.call_args.args[0].endswith('v1%7C123456789%7C0'))
    @patch('sfb_api.api.seeker_ebay.EbayBrowseClient')
    def test_errors_redacted(self,c):
        c.return_value._request.side_effect=RuntimeError('private-token')
        r=self.send({'operation':'search','query':'guitar'})
        self.assertEqual(r.status_code,502);self.assertNotIn(b'private-token',r.content)
