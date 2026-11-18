"""Isolated public DDGS connection check. No model, DB or worker imports."""
import argparse
import collections
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import zipfile
from datetime import datetime, timezone
from urllib.parse import urlsplit
ENGINES=('yahoo','brave','mojeek','startpage','duckduckgo')
QUERIES=('"want to buy" classifieds','"WTB" forum','"looking to buy" forum','"wanted" marketplace','"in search of" classifieds')

def write(path,value):
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8');tmp.replace(path)

def child(engine,query,trace):
    from ddgs import DDGS
    from ddgs.http_client import HttpClient
    original=HttpClient.request
    def emit(event):
        with open(trace,'a',encoding='utf-8') as f:f.write(json.dumps(event)+'\n')
    def request(self,*args,**kwargs):
        url=kwargs.get('url') or (args[1] if len(args)>1 else '')
        event={'host':urlsplit(str(url)).hostname,'url':str(url),'state':'started'};emit(event)
        try:
            response=original(self,*args,**kwargs)
            emit(dict(event,state='received',http_status=response.status_code));return response
        except Exception as exc:
            emit(dict(event,state='failed',error=type(exc).__name__+': '+str(exc)));raise
    HttpClient.request=request
    d=DDGS(timeout=10)
    chosen=d._get_engines('text',engine)
    names=[e.name for e in chosen]
    if names!=[engine]:
        return dict(state='unsupported',error='Requested engine unavailable; automatic substitution refused',selected=names,results=[])
    # Bind the already-verified engine: never permit DDGS auto substitution.
    d._get_engines=lambda category,backend:chosen
    try:
        rows=list(d.text(query,max_results=5,backend=engine))
        return dict(state='results' if rows else 'empty',results=rows,selected=names)
    except Exception as exc:
        return dict(state='error',error=type(exc).__name__+': '+str(exc),results=[],selected=names)

def attempt(engine,query,folder,index,deadline=25):
    trace=folder/f'{index:02d}-requests.jsonl';start=time.monotonic()
    command=[sys.executable,'-u',str(Path(__file__).resolve()),'--child',engine,'--query',query,'--trace',str(trace)]
    try:
        result=subprocess.run(command,capture_output=True,encoding='utf-8',errors='replace',timeout=deadline)
        if result.returncode:row=dict(state='process_error',error=result.stderr[-2000:],results=[])
        else:
            try:row=json.loads(result.stdout)
            except ValueError:row=dict(state='invalid_output',error=result.stdout[-2000:],results=[])
        if result.stderr:row['diagnostic']=result.stderr[-2000:]
    except subprocess.TimeoutExpired:row=dict(state='deadline',error=f'{deadline}-second wall-clock deadline reached; child terminated',results=[])
    events=[json.loads(line) for line in trace.read_text(encoding='utf-8').splitlines()] if trace.exists() else []
    row.update(engine=engine,query=query,seconds=round(time.monotonic()-start,2),request_events=events,result_count=len(row['results']))
    return row

def summarize(rows):
    out={}
    for engine in ENGINES:
        selected=[r for r in rows if r['engine']==engine]
        out[engine]=dict(attempts=len(selected),states=dict(collections.Counter(r['state'] for r in selected)),results=sum(r['result_count'] for r in selected),seconds=round(sum(r['seconds'] for r in selected),2))
    return out

def run(folder):
    folder.mkdir(parents=True,exist_ok=False)
    report=dict(started_at=datetime.now(timezone.utc).isoformat(),platform=sys.platform,python=sys.version,ddgs_version=None,paid_model_calls=0,database_writes=0,production_changes=False,queries=list(QUERIES),engines=list(ENGINES),rounds=2,attempts=[],state='running',limits='25-second process deadline per search; 5 results requested; no application retries',interpretation='Search results are leads, not verified ads. No result pages are opened. Review title, URL and snippet to classify buyer ad, directory or unrelated; unverified means not yet classified.')
    try:
        report['ddgs_version']=importlib.metadata.version('ddgs')
        write(folder/'REPORT.json',report)
        for round_number in (1,2):
            for qi,query in enumerate(QUERIES):
                # Rotate engine order across queries and rounds.
                offset=(qi+round_number-1)%len(ENGINES)
                for engine in ENGINES[offset:]+ENGINES[:offset]:
                    index=len(report['attempts'])+1
                    print(f'{index}/50 round {round_number}: {engine} | {query}',flush=True)
                    row=attempt(engine,query,folder,index);row['round']=round_number
                    for item in row['results']:item['review_class']='unreviewed'
                    report['attempts'].append(row);report['summary']=summarize(report['attempts']);write(folder/'REPORT.json',report)
                    print(f"  {row['state']}: {row['result_count']} results, {row['seconds']} seconds",flush=True)
                    time.sleep(2)
        report['state']='finished'
    except KeyboardInterrupt:report['state']='interrupted'
    except Exception as exc:report.update(state='setup_or_runner_error',error=type(exc).__name__+': '+str(exc))
    finally:
        report['finished_at']=datetime.now(timezone.utc).isoformat();report['summary']=summarize(report['attempts']);write(folder/'REPORT.json',report)
        with zipfile.ZipFile(folder/'SEND_THIS_REPORT.zip','w',zipfile.ZIP_DEFLATED) as z:
            for p in folder.iterdir():
                if p.is_file() and p.suffix!='.zip':z.write(p,p.name)
        print('Report: '+str(folder/'SEND_THIS_REPORT.zip'),flush=True)
    return report

def main():
    p=argparse.ArgumentParser();p.add_argument('--child',choices=ENGINES);p.add_argument('--query');p.add_argument('--trace');p.add_argument('--output',type=Path);a=p.parse_args()
    if a.child:
        try:result=child(a.child,a.query,a.trace)
        except Exception as exc:result=dict(state='setup_error',error=type(exc).__name__+': '+str(exc),results=[])
        print(json.dumps(result,ensure_ascii=True));return
    folder=a.output or Path.home()/('SFB_Search_Connections_'+datetime.now().strftime('%Y%m%d_%H%M%S'))
    print('50 public searches; no model calls or worker changes. Maximum about 23 minutes; each result prints immediately.',flush=True)
    run(folder)
if __name__=='__main__':main()
