"""Offline connector checks; real locked package via SFB_LOCKED_SEEKER_DIR."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from owner_api_bridge import OwnerApiBridge, observe_completion, ebay_options
from seeker_connector import load_runner

class BoundaryTests(unittest.TestCase):
    def test_missing_keys_names_only(self):
        with self.assertRaisesRegex(ValueError, 'EBAY_CLIENT_SECRET') as caught:
            ebay_options({'EBAY_CLIENT_ID': 'private-value'})
        self.assertNotIn('private-value', str(caught.exception))

    def test_replaced_transport_counted_even_if_validation_fails(self):
        class Route:
            model_transport = None
            def model(self):
                self.model_transport('matcher', b'payload')
                raise ValueError('validation')
        route = Route(); received = [False]
        with observe_completion(route, received):
            # This is what the owner's ApiRun.model does before Run.model.
            route.model_transport = lambda *args: b'completion'
            with self.assertRaises(ValueError): route.model()
        self.assertTrue(received[0])

    def test_transport_failure_not_counted_as_success(self):
        class Route:
            def model_transport(self, *args): raise TimeoutError()
            def model(self): return self.model_transport()
        route = Route(); received = [False]
        with observe_completion(route, received):
            with self.assertRaises(TimeoutError): route.model()
        self.assertFalse(received[0])

@unittest.skipUnless(os.environ.get('SFB_LOCKED_SEEKER_DIR'), 'Set pinned package path')
class PackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        manifest=json.loads(Path(__file__).with_name('five_step_package_hashes.json').read_text())
        cls.factory=load_runner(os.environ['SFB_LOCKED_SEEKER_DIR'],manifest,mode='ebay_api')

    def test_owner_pipeline_uses_api_search_and_details_and_locked_payload(self):
        from test_ebay_api import ApiFixture
        from test_automatic import BrowserFixture, model, fake_handoff, BUYER
        from automatic_run import locked, encode
        from ebay_api_run import ApiRun, SellerApiBrowser
        class Run(OwnerApiBridge, self.factory): pass
        with tempfile.TemporaryDirectory() as tmp:
            browser=BrowserFixture(); api=ApiFixture()
            # Exact production browser injection point, with offline pages.
            run=Run(Path(tmp)/'run',public_browser=lambda *a,**k:browser,
                    ebay_client_id='test',ebay_client_secret='test',api_transport=api,
                    transport=model,handoff=fake_handoff)
            self.assertIs(Run.search, ApiRun.search)
            self.assertIsInstance(run.browser_factory(),SellerApiBrowser)
            run.execute(key='offline',ceiling='1')
            self.assertEqual(run.r['state'],'completed',run.r.get('reason'))
            self.assertEqual(len(api.calls),3)
            self.assertFalse(any(x.get('purpose')=='seller' for x in browser.trace))
            self.assertEqual((run.folder/'models/matcher.request.json').read_bytes(),
                encode(locked.payload({'buyer_text':BUYER,'seller_text':run.r['pair']['seller']['author_text']},'B')))
            self.assertEqual(run.r['introductions'],0)

if __name__=='__main__': unittest.main()
