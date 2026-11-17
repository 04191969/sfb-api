import json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from seeker_connector import load_runner

class RelayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=os.environ['SFB_LOCKED_SEEKER_DIR']
        cls.factory=load_runner(root,json.loads(Path(__file__).with_name('five_step_package_hashes.json').read_text()),mode='ebay_api')
    def test_locked_pipeline_via_relay_operations(self):
        from railway_ebay_transport import RailwayEbayTransport,relay_options
        from owner_api_bridge import OwnerApiBridge
        from test_ebay_api import item,ID
        from test_automatic import BrowserFixture,model,fake_handoff,BUYER
        from automatic_run import locked,encode
        calls=[]
        def call(payload):
            calls.append(payload)
            if payload['operation']=='authenticate':return {'ready':True}
            if payload['operation']=='search':return {'itemSummaries':[{'itemId':ID,'itemWebUrl':'https://www.ebay.com/itm/'+ID.split('|')[1],'title':'Acme ZX100 camera for sale'}]}
            return item()
        class Run(OwnerApiBridge,self.factory):pass
        with tempfile.TemporaryDirectory() as tmp:
            opts=relay_options({'SUPABASE_KEY':'worker-test'})
            opts['api_transport'].call=call
            browser=BrowserFixture()
            r=Run(Path(tmp)/'run',public_browser=lambda *a,**kw:browser,transport=model,handoff=fake_handoff,**opts)
            r.execute(key='offline',ceiling='1')
            self.assertEqual(r.r['state'],'completed',r.r.get('reason'))
            self.assertEqual([x['operation'] for x in calls],['authenticate','search','item'])
            self.assertEqual(calls[-1]['item_id'],ID)
            self.assertEqual((r.folder/'models/matcher.request.json').read_bytes(),encode(locked.payload({'buyer_text':BUYER,'seller_text':r.r['pair']['seller']['author_text']},'B')))
    def test_signed_request_no_credentials_in_payload(self):
        from railway_ebay_transport import RailwayEbayTransport,ENDPOINT
        class Response:
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def read(self,*a):return b'{"ready":true}'
        with patch('railway_ebay_transport.build_opener') as opener:
            opener.return_value.open.return_value=Response()
            self.assertTrue(RailwayEbayTransport('SECRET').call({'operation':'authenticate'})['ready'])
            req=opener.return_value.open.call_args.args[0]
            self.assertEqual(req.full_url,ENDPOINT)
            self.assertNotIn('SECRET',str(req.headers)+str(req.data))
    def test_no_arbitrary_destinations(self):
        from railway_ebay_transport import RailwayEbayTransport
        from ebay_api import ApiStop
        t=RailwayEbayTransport('key')
        with self.assertRaises(ApiStop):t('https://evil.example/item',{})
    def test_missing_worker_credential_stops(self):
        from railway_ebay_transport import relay_options
        with self.assertRaises(ValueError):relay_options({})
