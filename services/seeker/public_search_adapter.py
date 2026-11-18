"""Public multi-engine retrieval; pinned listing capture followed by page cleanup.

Adapted from the supplied v3 engine roster. No paid API, credentials, or
query rewriting. Each HTTP dispatch consumes the existing request allowance.
"""
import json
import hashlib
import random
from urllib.parse import urlencode, urlsplit, parse_qs, urljoin

import httpx
from bs4 import BeautifulSoup
from playwright.sync_api import Error as BrowserError
from browser_driver import Browser, Held, RunLimitReached, SearchExtractionError, SiteBlocked, challenge, destination

INSTANCES = ('https://search.bus-hit.me', 'https://searx.tiekoetter.com',
             'https://search.sapti.me', 'https://searx.be',
             'https://search.ononoki.org', 'https://searx.mha.fi',
             'https://paulgo.io', 'https://search.disroot.org',
             'https://searx.namejeff.xyz', 'https://searx.work')


class PublicSearchBrowser(Browser):
    def visit(self, url, purpose, parent=None):
        try:
            return super().visit(url, purpose, parent)
        finally:
            # Captures are already saved. Unload the content document so scripts
            # cannot consume the run budget during subsequent search/model work.
            try:
                self.page.goto('about:blank', wait_until='commit', timeout=5000)
            except Exception as exc:
                raise RunLimitReached('Could not stop captured page background activity') from exc

    def visit_directory(self, url, parent=None):
        self.boundary()
        if self.pages >= 32:
            raise RunLimitReached('32-page navigation limit reached')
        self.address(url)
        self.pages += 1
        row = dict(sequence=self.pages, url=url, purpose='directory', parent=parent,
                   retrieval='document_only_navigation', state='attempted')
        self.trace.append(row)
        context = None
        try:
            context = self.browser.new_context(java_script_enabled=False,
                accept_downloads=False, service_workers='block')
            context.route('**/*', self.search_guard)
            page = context.new_page()
            response = page.goto(url, wait_until='domcontentloaded', timeout=25000)
            self.boundary()
            if not response: raise Held('No directory response')
            self.address(page.url)
            html = page.content()
            if len(html.encode('utf-8')) > 5_000_000: raise Held('Directory size limit')
            soup = BeautifulSoup(html, 'html.parser')
            title = page.title()
            reason = challenge(title, soup.get_text(' ', strip=True), response.status)
            if reason:
                self.held_hosts.add(urlsplit(page.url).hostname)
                raise Held(reason)
            if response.status != 200: raise Held('HTTP '+str(response.status))
            links = []
            for anchor in soup.select('a[href]'):
                target = urljoin(page.url, anchor.get('href', ''))
                if urlsplit(target).scheme in ('http','https'):
                    links.append(dict(url=target,title=anchor.get_text(' ',strip=True)))
            result = dict(url=page.url,title=title,links=links,
                capture_id='directory-'+str(self.pages),source_text='',
                extraction='navigation_only',single_listing=False)
            stem=self.folder/f'{self.pages:03d}'
            stem.with_suffix('.html').write_text(html,encoding='utf-8')
            stem.with_suffix('.json').write_text(json.dumps(result),encoding='utf-8')
            row.update(state='captured',final_url=page.url,link_count=len(links))
            return result
        except Exception as exc:
            row.update(state='held',reason=str(exc)[:300],error_type=type(exc).__name__)
            if isinstance(exc, BrowserError):
                # A browser refusal is a source failure, not a broken run.
                # Preserve global limit/cancellation outcomes before normalizing.
                self.boundary()
                raise Held('Directory navigation failed: '+str(exc)[:300]) from exc
            raise
        finally:
            if context is not None: context.close()
            row['request_counts']=self.counters()
            (self.folder/'TRACE.json').write_text(json.dumps(self.trace,indent=2),encoding='utf-8')

    def fetch(self, url):
        # Follow ordinary redirects only. Validate/count every destination;
        # challenges and refusals are never retried through another route.
        seen=set()
        with httpx.Client(timeout=10, follow_redirects=False) as client:
            for hop in range(4):
                self.boundary();self.address(url)
                if url in seen:raise Held('Search redirect loop')
                seen.add(url)
                self.requests += 1;self.dispatched += 1
                with client.stream('GET', url) as response:
                    if response.status_code in (301,302,303,307,308):
                        location=response.headers.get('location')
                        if not location:raise Held('Search redirect missing destination')
                        target=urljoin(url,location)
                        self.address(target)
                        if any(word in urlsplit(target).path.lower() for word in ('captcha','challenge','consent','login')):
                            raise Held('Search redirect requires challenge or consent')
                        url=target
                        continue
                    if response.status_code != 200:raise Held('HTTP '+str(response.status_code))
                    chunks=[];size=0
                    for chunk in response.iter_bytes():
                        self.boundary();size+=len(chunk)
                        if size>5_000_000:raise Held('Search response size limit')
                        chunks.append(chunk)
                    text=b''.join(chunks).decode('utf-8',errors='replace')
                    reason=challenge('',text,response.status_code)
                    if reason or 'anomaly.js' in text or 'anomaly-modal' in text:raise Held(reason or 'Search challenge')
                    return text
        raise Held('Search redirect limit')

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
        # Every query visits the public engine roster; Bing never gates it.
        groups = []
        failures = []
        try:
            groups.append(self.public_engine_search(query))
        except RunLimitReached:
            raise
        except SearchExtractionError as exc:
            failures.append(str(exc))
        try:
            groups.append(self.bing_document_search(query))
        except RunLimitReached:
            raise
        except Exception as exc:
            failures.append(str(exc))
        merged, seen = [], set()
        # Interleave engines so one provider cannot fill the candidate allowance.
        by_engine = {}
        for group in groups:
            for row in group:
                by_engine.setdefault(row.get('engine', 'bing'), []).append(row)
        for index in range(max([len(g) for g in by_engine.values()] or [0])):
            for group in by_engine.values():
                if index >= len(group): continue
                row = group[index]
                if row['url'] in seen: continue
                seen.add(row['url']); merged.append(row)
        if not merged and failures:
            raise SearchExtractionError('Public multi-engine search incomplete; see browser/TRACE.json')
        return merged

    def public_engine_search(self, query):
        rows, seen, attempts = [], set(), []
        if not hasattr(self,'engine_failures'):self.engine_failures={}
        def attempt(engine, base, params, parse):
            self.boundary()
            url = base + '?' + urlencode(params)
            entry = dict(purpose='public_search', engine=engine, url=url, query=query)
            attempts.append(entry)
            self.trace.append(entry)
            try:
                if urlsplit(url).hostname in self.engine_failures:
                    entry['cached_failure']=True
                    raise SiteBlocked('Earlier search failure this run: '+self.engine_failures[urlsplit(url).hostname])
                if urlsplit(url).hostname in self.held_hosts:
                    raise SiteBlocked('Previously refused engine host')
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
                if not entry.get('cached_failure'):self.engine_failures[urlsplit(url).hostname]=str(exc)[:200]
                if any(code in str(exc) for code in ('HTTP 403', 'HTTP 429', 'challenge', 'Challenge')):
                    self.held_hosts.add(urlsplit(url).hostname)
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

        def html_results(text, selector, heading_selector, snippet_selector):
            soup = BeautifulSoup(text, 'html.parser')
            found = []
            for block in soup.select(selector):
                link = block.select_one(heading_selector)
                if not link: continue
                snippet = block.select_one(snippet_selector)
                u = link.get('href', '')
                if u.startswith('/url?'):
                    u = parse_qs(urlsplit(u).query).get('q', [''])[0]
                if not u.startswith(('http://', 'https://')): continue
                found.append(dict(url=u, title=link.get_text(' ', strip=True),
                    snippet=snippet.get_text(' ', strip=True) if snippet else ''))
            if not found:
                raise SearchExtractionError('Public HTML result layout empty or unreadable')
            return found

        def mojeek(text):
            return html_results(text, 'ul.results-standard > li', 'h2 a', 'p.s')
        attempt('mojeek', 'https://www.mojeek.com/search', dict(q=query), mojeek)
        def brave(text):
            return html_results(text, 'div.snippet', 'a.heading-serpresult, a[href]:has(.title)', '.snippet-description, .description')
        attempt('brave', 'https://search.brave.com/search', dict(q=query), brave)
        def google(text):
            soup = BeautifulSoup(text, 'html.parser'); found = []
            for heading in soup.select('h3'):
                link = heading.find_parent('a')
                if not link: continue
                u = link.get('href', '')
                if u.startswith('/url?'): u = parse_qs(urlsplit(u).query).get('q', [''])[0]
                if u.startswith(('http://', 'https://')):
                    found.append(dict(url=u, title=heading.get_text(' ', strip=True), snippet=''))
            if not found: raise SearchExtractionError('Google result layout empty or unreadable')
            return found
        attempt('google', 'https://www.google.com/search', dict(q=query), google)
        def yahoo(text):
            from urllib.parse import unquote
            import re
            found = html_results(text, '#web .algo', 'h3 a', '.compText')
            for item in found:
                wrapped = re.search(r'/RU=([^/]+)/', item['url'])
                if wrapped: item['url'] = unquote(wrapped[1])
            return found
        attempt('yahoo', 'https://search.yahoo.com/search', dict(p=query), yahoo)
        if not rows and any(a['state'] == 'held' for a in attempts):
            raise SearchExtractionError('Public search incomplete: engines blocked, unreadable, or timed out; see browser/TRACE.json')
        return rows
