"""Public multi-engine retrieval; listing capture remains the pinned Browser.visit.

Adapted from the supplied v3 engine roster. No paid API, credentials, or
query rewriting. Each HTTP dispatch consumes the existing request allowance.
"""
import json
import hashlib
import random
from urllib.parse import urlencode, urlsplit, parse_qs

import httpx
from bs4 import BeautifulSoup
from browser_driver import Browser, Held, RunLimitReached, SearchExtractionError, SiteBlocked, challenge, destination

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

    @staticmethod
    def parse_bing(html, query, parent):
        soup = BeautifulSoup(html, 'html.parser')
        rows, seen = [], set()
        for block in soup.select('#b_results > li.b_algo'):
            link = block.select_one('h2 a')
            if not link or not link.get_text(' ', strip=True):
                continue
            try:
                url = destination(link.get('href', ''))
            except (Held, ValueError):
                continue
            if urlsplit(url).hostname in ('bing.com', 'www.bing.com') or url in seen:
                continue
            seen.add(url)
            snippet = block.select_one('.b_caption p') or block.select_one('p')
            rows.append(dict(url=url, title=link.get_text(' ', strip=True),
                             snippet=snippet.get_text(' ', strip=True) if snippet else '',
                             parent=parent, query=query, engine='bing'))
        if not rows and not soup.select('#b_results .b_no'):
            raise SearchExtractionError('Bing document has no readable result layout')
        return rows[:10]

    def search_guard(self, route):
        # Search results need the document, not scripts, beacons or styling.
        # This route is installed ONLY on a separate search context.
        if route.request.resource_type != 'document':
            self.requests += 1
            self.blocked += 1
            reason = 'search_subresource'
            self.blocked_reasons[reason] = self.blocked_reasons.get(reason, 0) + 1
            route.abort()
            return
        self.guard(route)

    def bing_document_search(self, query):
        self.boundary()
        if self.pages >= 32:
            raise RunLimitReached('32-page navigation limit reached')
        url = 'https://www.bing.com/search?' + urlencode({'q': query})
        if 'www.bing.com' in self.held_hosts:
            raise SiteBlocked('Previously held Bing host')
        self.pages += 1
        row = dict(sequence=self.pages, url=url, purpose='search', query=query,
                   engine='bing', retrieval='browser_document_only', state='attempted')
        self.trace.append(row)
        stem = self.folder / f'{self.pages:03d}'
        context = None
        try:
            context = self.browser.new_context(java_script_enabled=False,
                accept_downloads=False, service_workers='block')
            context.route('**/*', self.search_guard)
            page = context.new_page()
            response = page.goto(url, wait_until='domcontentloaded', timeout=25000)
            self.boundary()
            if not response:
                raise SearchExtractionError('No Bing navigation response')
            if urlsplit(page.url).hostname not in ('bing.com', 'www.bing.com'):
                raise SiteBlocked('Search redirected off Bing')
            html = page.content()
            text = BeautifulSoup(html, 'html.parser').get_text(' ', strip=True)
            if len(html.encode('utf-8')) > 5_000_000:
                raise SearchExtractionError('Search response size limit')
            stem.with_suffix('.html').write_text(html, encoding='utf-8')
            stem.with_suffix('.txt').write_text(text, encoding='utf-8')
            row.update(status=response.status, final_url=page.url,
                       rendered_sha256=hashlib.sha256(html.encode('utf-8')).hexdigest())
            reason = challenge(page.title(), text, response.status)
            if reason:
                self.held_hosts.add('www.bing.com')
                raise SiteBlocked(reason)
            if response.status != 200:
                raise SearchExtractionError('HTTP ' + str(response.status))
            rows = self.parse_bing(html, query, page.url)
            row.update(state='captured', result_count=len(rows))
            stem.with_suffix('.json').write_text(json.dumps(dict(search=rows, trace=row), indent=2), encoding='utf-8')
            return rows
        except Exception as exc:
            row.update(state='held', reason=str(exc)[:300], error_type=type(exc).__name__)
            raise
        finally:
            if context is not None:
                context.close()
            row['request_counts'] = self.counters()
            (self.folder / 'TRACE.json').write_text(json.dumps(self.trace, indent=2), encoding='utf-8')

    def search(self, query):
        try:
            return self.bing_document_search(query)
        except RunLimitReached:
            raise
        except Exception:
            # Other public engines remain fallbacks; never retry a challenge.
            self.boundary()
            return self.public_engine_search(query)

    def public_engine_search(self, query):
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
