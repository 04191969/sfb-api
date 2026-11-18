"""Unpaid public-seller retrieval probe. Does not import or run a worker.

Uses Seeker's existing public search, navigation classifier, token ranking and
source-record validator. No model, eBay API, database, scheduler or matcher.
A readable page is evidence to review, never an automatic qualification.
"""
import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

CASES = [
    ('titleist', 'Titleist GTS 4 9* head for sale'),
    ('ventus', 'OG Ventus Velocore Blue 6s driver shaft for sale'),
]


def run_case(browser, query, classify, tokens, record, wanted, limit=9):
    result = dict(query=query, state='started', returned=[], ranked=[], opened=[],
                  skipped=[], error=None, scope='public seller retrieval only')
    try:
        rows = browser.search(query)
        result['returned'] = rows
        terms = tokens(query)
        seen = set()
        for row in rows:
            if row['url'] in seen:
                continue
            seen.add(row['url'])
            kind, reason = classify(row)
            score = len(tokens(row.get('title', '')+' '+row.get('snippet', '')) & terms)
            if kind != 'ad' or not score or wanted.search(row.get('title', '')):
                result['skipped'].append(dict(url=row['url'], reason=reason if kind != 'ad' else 'No overlap or wanted title'))
                continue
            result['ranked'].append((score, row))
        from owner_api_bridge import diverse_leads
        result['ranked'] = diverse_leads(sorted(result['ranked'], key=lambda r: -r[0]))
        for score, row in result['ranked'][:limit]:
            entry = dict(url=row['url'], title=row.get('title'), score=score)
            result['opened'].append(entry)
            try:
                page = browser.visit(row['url'], 'seller', row.get('parent'))
                source = record(page, 'seller')
                if not tokens(source['author_text']) & terms:
                    raise ValueError('No source subject overlap with reader query')
                entry.update(state='readable_source_requires_review', source=source)
            except Exception as exc:
                entry.update(state='held', error=type(exc).__name__+': '+str(exc))
                # Preserve the real browser's global limits; do not continue after one.
                if type(exc).__name__ in ('RunLimitReached', 'Cancelled'):
                    raise
        result['unopened_candidates'] = max(0, len(result['ranked'])-limit)
        result['state'] = 'evidence_ready_for_review' if any(x['state']=='readable_source_requires_review' for x in result['opened']) else 'no_readable_source'
    except Exception as exc:
        result.update(state='incomplete', error=type(exc).__name__+': '+str(exc))
    return result


def verify_component(component, manifest):
    # Same CRLF normalization as runtime.load_release and build-release.py.
    for name, digest in manifest['files'].items():
        data = (component/name).read_bytes().replace(b'\r\n', b'\n')
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError('Component integrity mismatch: '+name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--component', type=Path)
    parser.add_argument('--package', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--executable')
    parser.add_argument('--headless', action='store_true')
    args = parser.parse_args()
    base = Path.home()/'SFB'/'seeker'
    component = args.component or base/'releases'/(base/'active.txt').read_text(encoding='utf-8').strip()
    package = args.package or base/'package'
    output = args.output or Path.home()/('SFB_Seller_Discovery_Proof_'+datetime.now().strftime('%Y%m%d_%H%M%S'))
    output.mkdir(parents=True, exist_ok=False)
    report = dict(created_at=datetime.now(timezone.utc).isoformat(), component=str(component),
                  state='initializing', paid_model_calls=0, database_writes=0,
                  production_changed=False, cases=[], acceptance='NOT PROVEN: review actual seller evidence and source coverage')
    try:
        sys.path.insert(0, str(component))
        from seeker_connector import verify_package
        verify_package(package, json.loads((component/'five_step_package_hashes.json').read_text(encoding='utf-8')))
        manifest = json.loads((component/'release.json').read_text(encoding='utf-8'))
        report['release'] = manifest
        # Match the installed immutable release, not another checkout's adapter.
        verify_component(component, manifest)
        sys.path.insert(1, str(package))
        from public_search_adapter import PublicSearchBrowser
        from lead_navigation import classify
        from automatic_run import tokens, record, WANTED
        for name, query in CASES:
            print('Public retrieval: '+query, flush=True)
            try:
                with PublicSearchBrowser(output/name, executable=args.executable, headless=args.headless) as browser:
                    case = run_case(browser, query, classify, tokens, record, WANTED)
                    case['browser_counts'] = browser.counters()
                    case['engine_trace'] = [r for r in browser.trace if r.get('purpose') in ('search','public_search')]
            except Exception as exc:
                case = dict(query=query, state='incomplete', error=type(exc).__name__+': '+str(exc))
            report['cases'].append(case)
            print(case['state'], flush=True)
            (output/'REPORT.json').write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
        report['state'] = 'incomplete' if any(c['state']=='incomplete' for c in report['cases']) else 'evidence_collected_not_qualified'
    except Exception as exc:
        report.update(state='setup_failed', error=type(exc).__name__+': '+str(exc))
    finally:
        (output/'REPORT.json').write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
        import zipfile
        archive = output/'SEND_THIS_REPORT.zip'
        with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as bundle:
            for path in output.rglob('*'):
                if path.is_file() and path != archive:
                    bundle.write(path,path.relative_to(output))
        print('Results: '+str(archive), flush=True)
    return 1 if report['state']=='setup_failed' else 0

if __name__=='__main__':
    raise SystemExit(main())
