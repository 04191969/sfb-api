"""Broad public discovery and up to three existing-reader calls. No worker changes."""
import argparse,json,zipfile
from datetime import datetime,timezone,timedelta
from pathlib import Path
import combined_buyer_proof as discovery
from prove_reader_handoff import run as read_one
from buyer_candidate_selection import bootstrap

def recent(source,now):
 stamp=source.get('posted_at')
 if not stamp:return 'date_unconfirmed'
 try:
  date=datetime.fromisoformat(stamp.replace('Z','+00:00'))
  if date.tzinfo is None:return 'date_unconfirmed'
 except (ValueError,TypeError):return 'date_unconfirmed'
 age=now-date.astimezone(timezone.utc)
 if age.total_seconds()<0:return 'future_date_unconfirmed'
 return 'within_90_days' if age<=timedelta(days=90) else 'older_than_90_days'

def execute(home,out):
 now=datetime.now(timezone.utc)
 # Product-free phrases retained; date cues supplement, never prove an ad's age.
 discovery.QUERIES=[f'"WTB" "{now.strftime("%B %Y")}"',f'"want to buy" "{now.year}"',f'"looking to buy" "{now.strftime("%B %Y")}"','"WTB" forum','"want to buy" classifieds','"looking to buy" forum']
 state_path=home/'SFB_Recent_Buyer_Proof_State'/'state.json'
 bootstrap(home,state_path)
 report=discovery.execute(out,state_path)
 report['recency_rule']='90 days from original-post timestamp; recency does not confirm current buying intent.'
 report['reader_checks']=[];report['paid_calls_dispatched']=0
 def save():
  (out/'REPORT.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
  with zipfile.ZipFile(out/'SEND_THIS_REPORT.zip','w',zipfile.ZIP_DEFLATED) as z:
   for f in out.rglob('*'):
    if f.is_file() and f.suffix!='.zip':z.write(f,str(f.relative_to(out)))
 try:
  for i,buyer in enumerate(report['buyers']):
   buyer['recency']=recent(buyer,datetime.now(timezone.utc))
   print('Post date: '+str(buyer.get('posted_at'))+' — '+buyer['recency'],flush=True)
   if buyer['recency']!='within_90_days' or report['paid_calls_dispatched']>=3:continue
   source=out/f'reader-input-{i}.json';source.write_text(json.dumps({'buyers':[buyer]}),encoding='utf-8')
   result=read_one(home,source,output=out/f'reader-{i}')
   report['reader_checks'].append(result)
   report['paid_calls_dispatched']+=result.get('paid_calls_dispatched',0)
   save()
   if result['state']=='failed_or_uncertain':break
  report['recent_posts_read']=sum(r['state']=='reader_handoff_validated' for r in report['reader_checks'])
  report['proof']='recent_original_posts_read' if report['recent_posts_read'] else 'no_recent_reader_success_proven'
 except Exception as e:report['reader_error']=type(e).__name__+': '+str(e)
 finally:
  save();print('FINAL combined report: '+str(out/'SEND_THIS_REPORT.zip'),flush=True)
 return report
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args();execute(Path.home(),a.output)
