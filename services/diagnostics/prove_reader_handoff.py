"""One saved original post -> installed Seeker discovery reader. Isolated ledger."""
import argparse,hashlib,importlib,json,os,sys,zipfile
from pathlib import Path
from datetime import datetime

def packet_for(source):
 text=source['text'];digest=hashlib.sha256(text.encode()).hexdigest()
 if digest!=source['source_sha256']:raise ValueError('Saved source fingerprint differs')
 return dict(direction='find_sellers',author_text=text,source=dict(url=source['url'],capture_id=digest,text_sha256=digest),source_limitations=['Automatically selected original-post region; source age and current availability are unconfirmed.'])

def run(home,source_path,send=None,output=None):
 root=home/'SFB'/'seeker';release=(root/'active.txt').read_text().strip()
 if Path(release).name!=release:raise ValueError('Invalid active release name')
 component=root/'releases'/release
 sys.path.insert(0,str(component))
 from discovery_reader.adapter import payload
 from discovery_reader import contract
 source=json.loads(source_path.read_text(encoding='utf-8-sig'))['buyers'][0]
 packet=packet_for(source);body=payload(packet)
 out=output or home/('SFB_Reader_Handoff_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f'));out.mkdir()
 report=dict(state='prepared',release=release,source_report=str(source_path),source_url=source['url'],posted_at=source.get('posted_at') or source.get('posted_at_raw'),availability='unconfirmed',paid_calls_dispatched=0,database_writes=0,production_changed=False)
 def save():(out/'REPORT.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
 (out/'REQUEST.json').write_text(json.dumps(body,indent=2),encoding='utf-8');save()
 try:
  if send is None:
   from openai import OpenAI
   key=os.environ.get('OPENAI_KEY') or os.environ.get('OPENAI_API_KEY')
   if not key:raise ValueError('OpenAI key not available; no request sent')
   client=OpenAI(api_key=key,max_retries=0,timeout=90)
   send=lambda request:client.responses.create(**request).model_dump(mode='json')
  print('Reading saved original post: '+source['title'],flush=True)
  print('One paid call maximum; 90-second request timeout; no automatic retry.',flush=True)
  report.update(state='dispatched',paid_calls_dispatched=1);save()
  raw=send(body)
  (out/'RESPONSE.json').write_text(json.dumps(raw,indent=2),encoding='utf-8')
  report['usage']=raw.get('usage')
  value=contract.validate_output(contract.response_object(contract.encode(raw)),packet)
  report.update(state='reader_handoff_validated',reading=value)
  print(value['interpretation'],flush=True)
  for q in value['queries']:print('Seller query: '+q['text'],flush=True)
 except Exception as e:
  report.update(state='failed_or_uncertain',error=type(e).__name__+': '+str(e));print(report['error'],flush=True)
 finally:
  save()
  with zipfile.ZipFile(out/'SEND_THIS_REPORT.zip','w',zipfile.ZIP_DEFLATED) as z:
   for f in out.glob('*.json'):z.write(f,f.name)
  print('Results: '+str(out/'SEND_THIS_REPORT.zip'),flush=True)
 return report
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--source',type=Path);a=p.parse_args();home=Path.home()
 candidates=sorted(home.glob('SFB_Combined_Buyer_Proof_*/REPORT.json'),key=lambda p:p.stat().st_mtime,reverse=True)
 source=a.source or next((p for p in candidates if json.loads(p.read_text(encoding='utf-8-sig')).get('buyers')),None)
 if source is None:raise SystemExit('No saved original buyer post found. No call sent.')
 run(home,source)
