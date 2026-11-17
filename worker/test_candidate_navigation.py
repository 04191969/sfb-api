import tempfile
import unittest
from pathlib import Path
from candidate_navigation import BuyerNavigationScreen, CandidateNavigationMixin, product_groups, terms
from automatic_run import Run

class FilteredRun(CandidateNavigationMixin, Run):
    pass

class BrowserFixture:
    def __init__(self, rows):
        self.rows=rows; self.queries=[]; self.visits=[]; self.trace=[]; self.requests=0
    def search(self,q): self.queries.append(q); return self.rows
    def visit(self,url,purpose,parent=None):
        self.visits.append(url)
        return dict(url=url,title='Sony CD player for sale',source_text='For sale: Sony CD player in working condition. Asking $75. This is my personal player and it is available for purchase.',single_listing=True,extraction='single_product',capture_id='test',links=[])

class CandidateTests(unittest.TestCase):
    def run_fixture(self,rows):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        run=FilteredRun(Path(tmp.name)/'run');run.browser=BrowserFixture(rows)
        return run
    def test_observed_dictionary_pages_never_opened(self):
        rows=[dict(url='https://www.merriam-webster.com/dictionary/working',title='working',snippet='Definition of working'),dict(url='https://dictionary.cambridge.org/dictionary/english/working',title='WORKING meaning',snippet='working definition'),dict(url='https://example.com/listing/1',title='Sony compact disc player',snippet='For sale $75')]
        r=self.run_fixture(rows)
        q=['CD player for sale','working CD player for sale']
        seller=r.discover_seller(q,{'url':'https://forum.example.com/discussion/wtb'})
        self.assertEqual(seller['url'],rows[2]['url'])
        self.assertEqual(r.browser.visits,[rows[2]['url']])
        self.assertEqual(r.browser.queries,[q[0]])
        self.assertEqual(r.r['seller_counts']['pre_navigation_skips'],2)
    def test_directory_remains_a_lead(self):
        r=self.run_fixture([dict(url='https://www.ebay.com/sch/i.html',title='CD players for sale',snippet='')]);r.product_groups=product_groups(['CD player for sale'])
        self.assertEqual(len(r.search('CD player for sale','seller')),1)
    def test_generic_working_or_player_alone_insufficient(self):
        for title in ['Working for sale','player definition','CD cases for sale']:
            r=self.run_fixture([dict(url='https://example.com/a',title=title)]);r.product_groups=product_groups(['working CD player for sale'])
            self.assertEqual(r.search('working CD player for sale','seller'),[])
    def test_product_in_snippet_is_preserved(self):
        r=self.run_fixture([dict(url='https://example.com/a',title='Sony available',snippet='Compact disc players for sale')]);r.product_groups=product_groups(['CD player for sale'])
        self.assertEqual(len(r.search('CD player for sale','seller')),1)
    def test_model_punctuation(self):
        self.assertEqual(terms('Fender ST-62G-SPL'),terms('Fender ST62G-SPL'))
        self.assertNotEqual(terms('Fender ST62'),terms('Fender ST62G'))
    def test_three_observed_profiles_prevented_by_real_buyer_admission(self):
        r=self.run_fixture([])
        for name in ['pitdogg2','polkguy1994','CH46E']:
            self.assertFalse(r.buyer_screen.pre_navigation('WTB CD players','https://forum.polkaudio.com/profile/'+name)['passed'])
    def test_wtb_thread_and_category_preserved(self):
        r=self.run_fixture([])
        for path in ['discussion/203010/wtb-cd-players','categories/wanted-wtb-classifieds/']:
            self.assertTrue(r.buyer_screen.pre_navigation('WTB CD players','https://forum.polkaudio.com/'+path)['passed'])
    def test_facebook_profile_not_globally_blocked(self):
        r=self.run_fixture([])
        self.assertTrue(r.buyer_screen.pre_navigation('WTB guitar','https://www.facebook.com/profile/seller')['passed'])
    def test_buyer_search_results_not_product_filtered(self):
        rows=[dict(url='https://forum.example.com/categories/wtb',title='Wanted')]
        r=self.run_fixture(rows)
        self.assertEqual(r.search('wanted WTB camera photography classifieds','WTB'),rows)
    def test_search_aliases(self):
        self.assertEqual(terms('CD-player'),terms('compact-disc players'))
    def test_no_candidate_does_not_invent_offer(self):
        r=self.run_fixture([dict(url='https://example.com/working',title='working')])
        self.assertIsNone(r.discover_seller(['CD player for sale'],{'url':'https://example.com/wtb'}))
        self.assertEqual(r.r['state'], 'search_extraction_failed')
        self.assertEqual(r.browser.visits,[])



if __name__=='__main__':unittest.main()
