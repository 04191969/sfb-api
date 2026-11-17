import unittest
from five_step_host import persist_assessment

class Query:
    def __init__(self,db,name):self.db=db;self.name=name;self.filters=[];self.action='select';self.row=None
    def select(self,*a):return self
    def eq(self,k,v):self.filters.append((k,v));return self
    def upsert(self,row,**kw):self.action='upsert';self.row=row;return self
    def update(self,row):self.action='update';self.row=row;return self
    def execute(self):
        rows=self.db.rows.setdefault(self.name,[])
        selected=[r for r in rows if all(r.get(k)==v for k,v in self.filters)]
        if self.action=='upsert':
            if not any(r.get('id')==self.row['id'] for r in rows):rows.append(dict(self.row))
        elif self.action=='update':
            for r in selected:r.update(self.row)
        return type('Response',(),{'data':selected})()
class DB:
    def __init__(self):self.rows={'seeker_runs':[{'id':'run'}]}
    def table(self,n):return Query(self,n)
class Tests(unittest.TestCase):
    def envelope(self):return {'handoff':{'pair':{s:{'url':'https://example.com/'+s,'author_text':'original '+s,'captured_at':'2026-11-16T00:00:00Z'} for s in ['buyer','seller']},'matcher_answer':{'decision':'Send'}},'report':{'availability':'unconfirmed'}}
    def test_storage_preserves_evidence_not_false_availability(self):
        db=DB();e=self.envelope();persist_assessment(db,'run',e)
        self.assertEqual(db.rows['seller_listings'][0]['listing_text'],'original seller')
        self.assertIsNone(db.rows['seller_listings'][0]['price'])
        self.assertEqual(db.rows['matches'][0]['match_decision'],'no_send')
        self.assertIsNone(db.rows['matches'][0]['confidence'])
        self.assertIn('"decision": "Send"',db.rows['matches'][0]['ai_reasoning'])
    def test_duplicate_does_not_add_pair(self):
        db=DB();persist_assessment(db,'run',self.envelope());persist_assessment(db,'run',self.envelope())
        for table in ['matches','buyer_posts','seller_listings']:self.assertEqual(len(db.rows[table]),1)
    def test_existing_original_and_review_not_overwritten(self):
        db=DB();persist_assessment(db,'run',self.envelope());db.rows['matches'][0]['status']='approved'
        original=db.rows['matches'][0].copy();e=self.envelope();e['handoff']['pair']['buyer']['author_text']='changed'
        persist_assessment(db,'run',e)
        self.assertEqual(db.rows['buyer_posts'][0]['post_text'],'original buyer');self.assertEqual(db.rows['matches'][0],original)
if __name__=='__main__':unittest.main()
