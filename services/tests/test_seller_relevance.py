import sys,unittest,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'seeker'))
from seller_relevance import score,terms
class Tests(unittest.TestCase):
    def test_recorded_failed_searches_have_no_ad_candidates(self):
        cases=json.loads((Path(__file__).parent/'fixtures/seller_irrelevant_results.json').read_text(encoding='utf-8'))
        self.assertEqual(sum(len(c['rows']) for c in cases),14)
        for case in cases:
            for row in case['rows']:
                with self.subTest(url=row['url']):
                    self.assertEqual(score(case['query'],row)[0],0)
    def row(self,title):return dict(url='https://dealer.example/products/1',title=title,snippet='')
    def test_model_spacing(self):
        self.assertGreater(score('Titleist GTS 4 9* head for sale',self.row('Titleist GTS4 9 head'))[0],0)
    def test_wrong_model(self):
        self.assertEqual(score('Titleist GTS 4 9* head for sale',self.row('Titleist GTS3 9 head'))[0],0)
    def test_catalog_lacks_model(self):
        self.assertEqual(score('Titleist GTS 4 9* head for sale',self.row('Shop Titleist Golf Clubs: GTS Metals'))[0],0)
    def test_short_flex(self):
        self.assertGreater(score('OG Ventus Velocore Blue 6s driver shaft for sale',self.row('Ventus Blue Velocore 6-S shaft'))[0],0)
    def test_og_definition(self):
        self.assertEqual(score('OG Ventus Velocore Blue 6s driver shaft for sale',self.row('What OG means in slang'))[0],0)
    def test_url_cannot_supply_evidence(self):
        self.assertEqual(score('Titleist GTS4',dict(url='https://dealer.example/Titleist-GTS4',title='Home'))[0],0)
    def test_wikipedia_not_offer(self):
        self.assertEqual(score('Titleist GTS4',dict(url='https://en.wikipedia.org/wiki/Titleist',title='Titleist GTS4'))[0],0)
    def test_other_product(self):
        self.assertGreater(score('Polk RT35i speakers for sale',self.row('Pair Polk RT35i speakers'))[0],0)
    def test_product_without_model(self):
        self.assertGreater(score('Gibson Southern Jumbo for sale',self.row('Gibson Southern Jumbo acoustic guitar'))[0],0)
if __name__=='__main__':unittest.main()
