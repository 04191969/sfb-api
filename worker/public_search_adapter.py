"""Public multi-engine retrieval; listing capture remains the pinned Browser.visit.

Adapted from the supplied v3 engine roster. No paid API, credentials, or
query rewriting. Each HTTP dispatch consumes the existing request allowance.
"""
import json
import random
from urllib.parse import urlencode, urlsplit, parse_qs

import httpx
from bs4 import BeautifulSoup
from browser_driver import Browser, Held, RunLimitReached, SearchExtractionError, challenge, destination

INSTANCES = ('https://search.bus-hit.me', 'https://searx.tiekoetter.com',
             'https://search.sapti.me', 'https://searx.be',
             'https://search.ononoki.org', 'https://searx.mha.fi',
             'https://paulgo.io', 'https://search.disroot.org',
             'https://searx.namejeff.xyz', 'https://searx.work')


class PublicSearchBrowser(Browser):
    def fetch(self, url):
        self.boundary()
        self.address(url)
        self.requests += 1
        self.dispatched += 1
        # Redirects/challenges are reported, never followed around a refusal.
        with httpx.Client(timeout=10, follow_redirects=False) as client:
            with client.stream('GET', url) as response:
                if response.status_code != 200:
                    raise Held('HTTP ' + str(response.status_code))
                chunks = []
                size = 0
                for chunk in response.iter_bytes():
                    self.boundary()
                    size += len(chunk)
                    if size > 5_000_000:
                        raise Held('Search response size limit')
                    chunks.append(chunk)
                text = b''.join(chunks).decode('utf-8', errors='replace')
                reason = challenge('', text, response.status_code)
                if reason or 'anomaly.js' in text or 'anomaly-modal' in text:
                    raise Held(reason or 'Search challenge')
                return text

    def search(self, query):
        rows, seen, attempts = [], set(), []
        def attempt(engine, base, params, parse):
            self.boundary()
            url = base + '?' + urlencode(params)
            entry = dict(purpose='public_search', engine=engine, url=url, query=query)
            attempts.append(entry)
            self.trace.append(entry)
            try:
                found = parse(self.fetch(url))
                entry.update(state='captured', result_count=len(found))
                for item in found[:10]:
                    try:
                        u = destination(item['url'])
                    except (Held, ValueError):
                        continue
                    if u in seen or not item.get('title', '').strip():
                        continue
                    seen.add(u)
                    rows.append(dict(item, url=u, parent=url, query=query, engine=engine))
                return bool(found)
            except RunLimitReached:
                raise
            except Exception as exc:
                entry.update(state='held', reason=str(exc)[:300], error_type=type(exc).__name__)
                return False
            finally:
                entry['request_counts'] = self.counters()
                (self.folder / 'TRACE.json').write_text(json.dumps(self.trace, indent=2), encoding='utf-8')

        instances = list(INSTANCES)
        random.shuffle(instances)
        for instance in instances[:3]:
            def searx(text):
                data = json.loads(text)
                if not isinstance(data.get('results'), list):
                    raise SearchExtractionError('SearXNG result layout unknown')
                return [dict(url=r.get('url', ''), title=r.get('title', ''), snippet=r.get('content', '')) for r in data['results']]
            if attempt('searxng', instance + '/search', dict(q=query, format='json', engines='google,bing,duckduckgo,brave,yahoo,mojeek', language='en'), searx):
                break

        def ddg(text):
            soup = BeautifulSoup(text, 'html.parser')
            found = []
            for block in soup.select('.result'):
                link = block.select_one('a.result__a')
                if not link:
                    continue
                url = link.get('href', '')
                url = parse_qs(urlsplit(url).query).get('uddg', [url])[0]
                snippet = block.select_one('.result__snippet')
                found.append(dict(url=url, title=link.get_text(' ', strip=True), snippet=snippet.get_text(' ', strip=True) if snippet else ''))
            if not found and not soup.select('.no-results'):
                raise SearchExtractionError('DuckDuckGo result layout unknown')
            return found
        attempt('duckduckgo', 'https://html.duckduckgo.com/html/', {'q': query}, ddg)

        def mojeek(text):
            data = json.loads(text).get('response', {})
            if not isinstance(data.get('results'), list):
                raise SearchExtractionError('Mojeek result layout unknown')
            return [dict(url=r.get('url', ''), title=r.get('title', ''), snippet=r.get('desc', '')) for r in data['results']]
        attempt('mojeek', 'https://www.mojeek.com/search', dict(q=query, fmt='json'), mojeek)
        if not rows and any(a['state'] == 'held' for a in attempts):
            raise SearchExtractionError('Public search incomplete: engines blocked, unreadable, or timed out; see browser/TRACE.json')
        return rows
