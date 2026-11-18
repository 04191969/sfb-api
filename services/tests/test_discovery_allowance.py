"""Offline request/ledger/response checks for Seeker's reader allowance."""
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import test_continuation as fixture
c=fixture.c

class Tests(unittest.TestCase):
    def run_reader(self,incomplete=False):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        run=fixture.Runner(Path(tmp.name)/'run',stocks=[2])
        original=run.transport_call
        requests=[]
        def transport(kind,body):
            requests.append((kind,body))
            response=json.loads(original(kind,body))
            if kind=='discovery':
                response['usage']['output_tokens']=6000 if incomplete else 3500
                if incomplete:
                    response.update(status='incomplete',incomplete_details={'reason':'max_output_tokens'},output=[])
            return json.dumps(response).encode()
        with patch.object(fixture.Runner,'transport_call',lambda self,kind,body:transport(kind,body)),patch.object(c,'validate_output',lambda out,packet:out):
            run=fixture.Runner(Path(tmp.name)/'final',stocks=[2])
            report=run.execute('offline')
        return requests,report
    def test_request_reservation_and_completed_response_above_old_limit(self):
        requests,report=self.run_reader()
        self.assertEqual(report['state'],'completed',report.get('reason'))
        body=next(body for kind,body in requests if kind=='discovery')
        request=json.loads(body);packet=json.loads(request['input'])
        old=json.loads(c.pinned_discovery_body(packet));old['max_output_tokens']=6000;old['instructions']+=c.EXACT_QUOTE_INSTRUCTION
        self.assertEqual(request,old)
        plan=c.plan(body,'discovery')
        self.assertEqual(plan['max_output_tokens'],6000)
        self.assertEqual(plan['maximum'],c.estimate(len(body),6000))
        self.assertGreater(plan['maximum'],c.pinned_plan(body,'discovery')['maximum'])
        matcher=next(body for kind,body in requests if kind=='matcher')
        self.assertEqual(c.plan(matcher,'matcher'),c.pinned_plan(matcher,'matcher'))
    def test_incomplete_still_stops_without_retry(self):
        requests,report=self.run_reader(True)
        self.assertIn('model response not completed',report['reason'])
        self.assertEqual(len(requests),1)
if __name__=='__main__':unittest.main()
