"""Seeker-only continuation of the pinned retrieval rules.
Buyer/seller traversal is copied from pinned automatic_run, with returns changed
into yields so rejection resumes the same queues. No locked files are edited.
V2 payloads, judgments, query wording, API transport and retrieval caps stay intact.
"""
from automatic_run import (BROAD, record, now, sha, save, tokens, excluded, WANTED,
    urlsplit, seller_identity, item_id, ebay_directory, PER_DIRECTORY_VISITS,
    SELECTION_DONE, Held, RunLimitReached, SiteBlocked, SearchExtractionError,
    discovery_body as pinned_discovery_body, validate_output, encode, need,
    plan as pinned_plan, money, P, real_transport)
from spending import Ledger
from mercedes_route import Route
import json, re, zipfile
from discovery_contract import response_object
from discovery_contract import locked as original_locked
parse_response = original_locked.parse
import v2_bridge as locked

from protected_ledger import estimate

DISCOVERY_OUTPUT_TOKENS = 6000
EXACT_QUOTE_INSTRUCTION = "\nEvidence copying: Each quotation text must be a nonempty, exact contiguous substring of author_text after JSON decoding. Preserve line breaks, spacing, punctuation and case. Do not join lines or paraphrase within a quotation. Choose a shorter exact substring if needed. This also applies to hold responses."

def discovery_body(packet):
    request = json.loads(pinned_discovery_body(packet))
    request["max_output_tokens"] = DISCOVERY_OUTPUT_TOKENS
    request["instructions"] += EXACT_QUOTE_INSTRUCTION
    return encode(request)

def plan(body, kind):
    result = pinned_plan(body, kind)
    if kind == "discovery":
        need(json.loads(body).get("max_output_tokens") == DISCOVERY_OUTPUT_TOKENS, "Discovery output allowance mismatch")
        result.update(max_output_tokens=DISCOVERY_OUTPUT_TOKENS,
                      maximum=estimate(len(body), DISCOVERY_OUTPUT_TOKENS))
    return result

MODEL_REQUEST_LIMIT = 17  # at most eight readers plus nine seller assessments

class ContinuationLedger(Ledger):
    """Same transactional spending ledger, with a bounded multi-pair request cap."""
    def reserve(self, estimate, purpose, model=False):
        need(purpose in ('discovery', 'matcher'), 'Unexpected model purpose')
        return super().reserve(estimate, purpose, model)

class ContinuationRoute(Route):
    def model(self,body,kind,reservation):
        self.report['stage']=kind+' dispatch'
        need(len(self.report['model_calls'])<MODEL_REQUEST_LIMIT,'model request cap reached')
        prefix=f'{len(self.report["model_calls"])+1:03d}-{kind}'
        (self.folder/(prefix+'.request.json')).write_bytes(body)
        self.ledger.dispatch_frozen(reservation,body)
        event=dict(kind=kind,request_sha256=sha(body),request_bytes=len(body),reservation=reservation)
        self.report['model_calls'].append(event)
        try:raw=self.model_transport(kind,body)
        except Exception as e:
            self.ledger.update(reservation,'charge_unknown',reason=str(e),exception_type=type(e).__name__)
            raise
        try:
            need(type(raw) is bytes,'transport must return original response bytes')
            (self.folder/(prefix+'.response.json')).write_bytes(raw)
        except Exception as e:
            self.ledger.update(reservation,'charge_unknown',reason=str(e));raise
        self.report['stage']=kind+' usage settlement'
        try:
            response=parse_response(raw.decode('utf-8'));usage=response['usage']
            need(all(type(usage.get(k)) is int and usage[k]>=0 for k in ('input_tokens','output_tokens')),'missing or invalid usage')
        except Exception as e:
            self.ledger.update(reservation,'charge_unknown',reason=str(e));raise
        charge=money((__import__('decimal').Decimal(usage['input_tokens'])*__import__('decimal').Decimal('2.50')+__import__('decimal').Decimal(usage['output_tokens'])*15)/1000000)
        self.ledger.settle(reservation,charge,'estimated from reported usage; not a provider invoice',usage,violation='output exceeds frozen cap' if usage['output_tokens']>(DISCOVERY_OUTPUT_TOKENS if kind=='discovery' else 6000) else None)
        self.report['stage']=kind+' output validation'
        return response_object(raw)

class ContinuationMixin:
    def iter_buyers(self):
     admitted=set();visited=set();frontier=[];opened=0
     counts=self.r['buyer_counts']
     def reject(row,verdict,phase):
      counts['screened_out']+=1
      self.r['screening'].append(dict(side='buyer',url=row['url'],phase=phase,**verdict));self.persist()
     def admit(row,depth):
      key=self.buyer_screen.canonical_url(row['url'])
      if key in admitted:
       counts['duplicates_skipped']+=1;return None
      admitted.add(key);counts['candidate_links']+=1
      verdict=self.buyer_screen.pre_navigation(row.get('title',''),row['url'])
      if not verdict['passed']:reject(row,verdict,'pre_navigation');return None
      if excluded(' '.join(str(row.get(k,'')) for k in ('title','snippet','url'))):
       self.r['scope_skipped'].append(dict(url=row['url'],side='buyer',reason='Excluded category'))
       reject(row,dict(passed=False,reason='Excluded test category',matched_rule='existing_test_scope'),'scope');return None
      return (row,depth)
     for q in BROAD:
      for row in self.search(q,'WTB'):
       candidate=admit(row,0)
       if candidate:frontier.append(candidate)
      while frontier and opened<8:
       row,depth=frontier.pop(0);url=row['url'];key=self.buyer_screen.canonical_url(url)
       if key in visited:
        counts['duplicates_skipped']+=1;continue
       visited.add(key);opened+=1;self.boundary();self.event('Reading wanted candidate',url=url)
       page=None
       try:
        page=self.browser.visit(url,'buyer',row.get('parent'))
        visited.add(self.buyer_screen.canonical_url(page['url']))
        # Existing capture-size, source-boundary and category checks stay separate.
        buyer=record(page,'buyer',screened_buyer=True)
        verdict=self.buyer_screen.screen(page.get('title',''),page.get('source_text',''),page['url'])
        if verdict['passed']:
         counts['screened_passes']+=1;self.r['buyer_screening_evidence']=verdict
         self.r['buyer_navigation']=dict(discovered_link=row,depth=depth)
         self.r['buyer']=buyer;self.r['steps']['find_wtb']='candidate_captured_validation_pending'
         self.event('WTB candidate captured—validation pending');yield buyer
         continue
        reject(row,verdict,'post_capture')
       except RunLimitReached:raise
       except (Held,ValueError) as e:
        counts['capture_failures']+=1
        self.r['screening'].append(dict(side='buyer',url=url,phase='capture_validation',reason=str(e)));self.persist()
       if page is not None and depth==0:
        children=[]
        for link in page.get('links',[]):
         if urlsplit(link['url']).hostname!=urlsplit(page['url']).hostname:continue
         # Directory navigation uses request anchors or item/model leads. Local
         # request evidence is still required after capture.
         if not self.buyer_screen.has_anchor(link.get('title','')) and not self.buyer_screen.has_item(link.get('title','')):continue
         candidate=admit(dict(link,parent=page['url']),1)
         if candidate:children.append(candidate)
        frontier=children+frontier
      if opened>=8 and (frontier or q != BROAD[-1]):raise RunLimitReached('Eight-buyer-page limit reached; buyer discovery remains incomplete')
     return None
    def iter_sellers(self,queries,buyer):
     seen={seller_identity(buyer['url'])};processed=set();deferred={};counts=self.r['seller_counts'];opened=0
     self.r['directory_traversal']={'rule':'ebay_srp_cards_v1','depth':1,'per_directory_visit_cap':PER_DIRECTORY_VISITS,'directories':[]}
     counts.update(directory_visits_attempted=0,individual_listing_visits_attempted=0)
     terms=tokens(' '.join(queries));self.r['seller_link_count']=0;self.r['seller_candidates']=[]
     for query_number,q in enumerate(queries,1):
      self.boundary()
      if self.r['seller_counts']['page_visits_attempted']+self.r.get('public_page_counts',{}).get('seller',0)>=9:raise RunLimitReached('Nine-seller-page visit limit reached; remaining queries were not evaluated')
      need(not excluded(q),'Reader query is outside the test categories')
      self.event('Seller search started',query=q,query_number=query_number)
      rows=self.search(q,'seller');counts['returned_rows']+=len(rows)
      self.event('Seller search completed',query=q,query_number=query_number,returned_rows=len(rows))
      leads=[]
      for row in rows:
       key=seller_identity(row['url'])
       if item_id(row['url'])=='123456':continue
       if key in seen:continue
       seen.add(key)
       if excluded(' '.join(str(row.get(k,'')) for k in ('title','snippet','url'))):
        self.r['scope_skipped'].append(dict(url=row['url'],side='seller',reason='Excluded category'));continue
       score=len(tokens(row.get('title','')+' '+row.get('snippet',''))&terms)
       if score and not WANTED.search(row.get('title','')):leads.append((score,row))
      counts['eligible_candidates']+=len(leads);self.r['seller_link_count']=counts['eligible_candidates']
      leads=sorted(leads,key=lambda x:-x[0]);position=0
      while position<len(leads):
       score,row=leads[position];position+=1
       self.boundary()
       if self.r['seller_counts']['page_visits_attempted']+self.r.get('public_page_counts',{}).get('seller',0)>=9:raise RunLimitReached('Nine-seller-page visit limit reached; remaining candidates were not evaluated')
       processed.add(seller_identity(row['url']));deferred.pop(seller_identity(row['url']),None)
       counts['candidates_considered']+=1
       self.event('Seller candidate processing started',url=row['url'],query_number=query_number,query_candidate_position=position,overall_processing_position=counts['candidates_considered'])
       verdict=self.seller_screen.check(row['url'])
       is_directory=ebay_directory(row['url']) and not row.get('directory_parent')
       if not verdict['passed'] and not is_directory:
        counts['pre_navigation_skips']+=1
        self.r['screening'].append(dict(side='seller',url=row['url'],title=row.get('title',''),phase='pre_navigation',outcome='pre_navigation_skip',**verdict))
        self.event('Seller candidate outcome',url=row['url'],outcome='pre_navigation_skip');continue
       opened+=1;counts['page_visits_attempted']+=1
       self.event('Opening seller directory' if is_directory else 'Reading seller candidate',url=row['url'])
       page=None;phase='navigation'
       try:
        if is_directory:
         counts['directory_visits_attempted']+=1
         summary=dict(url=row['url'],query_number=query_number,status='opening',links_returned=0,eligible_links=0,duplicates=0,filtered=0,followed=0,passed_validation=0,deferred_item_ids=[])
         self.r['directory_traversal']['directories'].append(summary)
         page=self.browser.visit(row['url'],'seller_directory',row.get('parent'));phase='directory_links'
         info=page.get('directory_links',{})
         summary.update(capture_id=page.get('capture_id'),extraction=info,status='unsupported')
         if not ebay_directory(page['url']) or not info.get('supported'):
          summary['reason']=info.get('reason','Observed directory layout unavailable')
          self.event('Seller directory outcome',**summary);continue
         children=[];local_seen=set()
         for child in info.get('rows',[]):
          summary['links_returned']+=1
          ident=item_id(child.get('url',''));key=seller_identity(child.get('url',''))
          if not ident or ident=='123456' or ident!=child.get('item_id'):
           summary['filtered']+=1;continue
          if key in seen or key in local_seen:
           summary['duplicates']+=1;continue
          local_seen.add(key)
          subject=' '.join(str(child.get(k,'')) for k in ('title','snippet','url'))
          score_child=len(tokens(child.get('title','')+' '+child.get('snippet',''))&terms)
          if excluded(subject) or WANTED.search(child.get('title','')) or not score_child:
           summary['filtered']+=1;continue
          children.append((score_child,dict(child,parent=page['url'],directory_parent=row['url'],directory_index=len(self.r['directory_traversal']['directories'])-1)))
         children=sorted(children,key=lambda x:-x[0]);summary['eligible_links']=len(children)
         queued=children[:PER_DIRECTORY_VISITS];omitted=children[PER_DIRECTORY_VISITS:]
         for _,child in omitted:deferred[seller_identity(child['url'])]=child
         summary['deferred_item_ids']=[item_id(child['url']) for _,child in omitted]
         summary['queued']=len(queued);summary['status']='processing' if queued else 'directory_exhausted'
         for _,child in queued:seen.add(seller_identity(child['url']))
         counts['eligible_candidates']+=len(queued);self.r['seller_link_count']=counts['eligible_candidates']
         leads[position:position]=queued
         self.event('Seller directory links queued',**summary);continue
        counts['individual_listing_visits_attempted']+=1
        if 'directory_index' in row:
         ds=self.r['directory_traversal']['directories'][row['directory_index']];ds['followed']+=1
        page=self.browser.visit(row['url'],'seller',row.get('parent'));phase='source_validation'
        need(not ebay_directory(page['url']),'Destination redirected to a directory; not seller evidence')
        final_key=seller_identity(page['url'])
        if final_key!=seller_identity(row['url']):
         need(final_key not in processed,'Redirect destination already processed')
         processed.add(final_key);seen.add(final_key);deferred.pop(final_key,None)
        seller=record(page,'seller')
        need(seller['url']!=buyer['url'],'Buyer source cannot be seller')
        rank=len(tokens(seller['author_text'])&terms)
        need(rank>0,'No source subject overlap with reader queries')
        counts['readable_individual_candidates']+=1
        if 'directory_index' in row:
         ds['passed_validation']+=1;ds['status']='seller_selected'
        self.r['seller_candidates']=[dict(source=seller,discovered_link=row)]
        self.r['seller']=seller
        before={k:counts[k] for k in ('pre_navigation_skips','access_failures','capture_failures','post_capture_rejections')}
        before['candidates_considered_before_selection']=counts['candidates_considered']-1
        self.r['selection'].update(status='selected',disclosure=SELECTION_DONE,selected_url=seller['url'],query=q,query_number=query_number,query_candidate_position=position,overall_processing_position=counts['candidates_considered'],before_selection=before)
        self.event('Seller candidate outcome',url=row['url'],outcome='selected')
        self.event('Seller selected',url=row['url'],query_number=query_number,remaining_queries_not_processed=len(queries)-query_number,remaining_eligible_candidates_not_processed=len(leads)-position)
        seller['source_coverage']='eBay item API response; seller description plus labeled returned item fields'
        seller['limitations']=['Retrieved through eBay Browse API, not a rendered browser page.',
          'Description HTML converted to text without summarization; raw item response retained.',
          'Only one search page per reader query; variation-specific items unsupported.',
          'API fields are source evidence, not independent confirmation of availability or suitability.']
        yield seller
       except RunLimitReached as e:
        if is_directory:summary.update(status='run_limit_reached',reason=str(e))
        elif 'directory_index' in row:ds.update(status='run_limit_reached',reason=str(e))
        self.r['screening'].append(dict(side='seller',url=row['url'],phase=phase,reason=str(e),limit=str(e),outcome='run_limit_reached'))
        self.event('Seller candidate outcome',url=row['url'],outcome='run_limit_reached');raise
       except (Held,ValueError) as e:
        reason=str(e)
        if is_directory:summary.update(status='directory_failed',reason=reason)
        if phase=='navigation':outcome='capture_failure' if reason.startswith('Page size limit') else 'access_failure'
        elif reason.startswith(('Empty source:','Too-short source:','Oversized source:','Ambiguous original source region','Missing product title')):outcome='capture_failure'
        else:outcome='post_capture_rejection'
        counter={'access_failure':'access_failures','capture_failure':'capture_failures','post_capture_rejection':'post_capture_rejections'}[outcome]
        counts[counter]+=1
        status=re.search(r'\bHTTP (\d{3})\b',reason)
        detail=dict(side='seller',url=row['url'],phase=phase,reason=reason,outcome=outcome)
        if status:detail['http_status']=int(status.group(1))
        if page:detail['capture_id']=page.get('capture_id');detail['extraction']=page.get('extraction');detail['listing_binding']=page.get('listing_binding');detail['layout_diagnostic']=page.get('layout_diagnostic');detail['source_validation_reason']=reason;detail['source_validation_code']=next((code for prefix,code in [('Empty source:','empty'),('Too-short source:','too_short'),('Oversized source:','oversized'),('Unbound source:','unbound')] if reason.startswith(prefix)),None)
        self.r['screening'].append(detail)
        self.r['seller_failure_counts']={k:counts[k] for k in ('pre_navigation_skips','access_failures','capture_failures','post_capture_rejections')}
        self.event('Seller candidate outcome',url=row['url'],outcome=outcome)
       finally:
        if 'directory_index' in row and 'ds' in locals() and ds['followed']==ds['queued'] and ds['status']=='processing':
         ds['status']='directory_cap_reached' if ds['deferred_item_ids'] else 'directory_exhausted'
         self.event('Seller directory processing completed',**ds)
      self.event('Seller query queue exhausted',query=q,query_number=query_number,deferred_candidates=len(deferred))
     self.boundary()
     # A browser interruption must not be mislabeled as completed discovery.
     if hasattr(self.browser,'boundary'):self.browser.boundary()
     remaining=[key for key in deferred if key not in processed]
     self.r['directory_traversal']['unvisited_due_to_directory_caps']=remaining
     if remaining:raise RunLimitReached('Three-listing directory cap reached; eligible individual listings remain unvisited')
     self.event('Seller queries exhausted for buyer',buyer_url=buyer['url'])
     self.persist();return None

    def iter_pairs(self, key):
        for buyer in self.iter_buyers():
            self.r['buyers_evaluated'] += 1
            packet = dict(direction='find_sellers', author_text=buyer['author_text'],
                source={k:buyer[k] for k in ('url','capture_id','text_sha256')},
                source_limitations=buyer['limitations'])
            body = discovery_body(packet)
            res = self.ledger.reserve(plan(body,'discovery'),'discovery',True)
            self.r['steps']['reader'] = self.r['reader_execution'] = 'running'
            output = validate_output(self.model('discovery',body,key,res),packet)
            self.r['reader'] = output
            self.r['steps']['reader'] = self.r['reader_execution'] = 'completed'
            self.r['reader_outcome'] = output['state']
            if output['state'] != 'ready':
                self.event('Buyer held by reader; continuing', buyer_url=buyer['url'])
                continue
            self.r['buyer_counts']['reader_ready_results'] += 1
            self.r['steps']['find_wtb'] = 'completed'
            self.r['steps']['find_seller'] = 'running'
            for seller in self.iter_sellers([q['text'] for q in output['queries']],buyer):
                yield buyer, seller

    def execute(self, key='', ceiling='1.00', search_only=False):
        need(not self.closed, 'Run already attempted; implicit resume or retry prohibited')
        self.closed = True
        self.r.update(assessments=[], buyers_evaluated=0,
            scope='Continue seller candidates and buyers until V2 Send, exhaustion, or a shared run limit.',
            continuation_policy='continue_after_non_send', version='seeker_continuation_v1')
        self.r['caps']['model_requests'] = MODEL_REQUEST_LIMIT
        self.r['selection']['policy'] = 'first_v2_send_within_existing_limits'
        self.r['selection']['disclosure'] = 'Candidates are assessed in order; non-Send decisions continue the queue.'
        attempted_pairs = set()
        pairs = None
        try:
            if not search_only:
                need(bool(key.strip()), 'API key is required in local setup; not written to reports')
                need(money(ceiling)>0, 'Positive estimated budget required')
                self.ledger = ContinuationLedger(self.folder/'ledger.sqlite', ceiling, 'USD',
                    MODEL_REQUEST_LIMIT, ['bounded reader and matcher continuation'],
                    ['public browser search is unpaid', 'estimates are not a provider invoice'])
                self.ledger.configure_estimate()
                self.route = ContinuationRoute(self.folder/'models',
                    model_transport=self.transport or (lambda kind, body: real_transport(key)(body)))
                self.route.ledger = self.ledger
                self.route.report['provenance'] = self.r['testing_mode']+' Seeker continuation'
            self.r['state'] = 'running'
            self.event('Opening public browser')
            with self.browser_factory() as browser:
                self.browser = browser
                self.event('Checking eBay API authentication')
                self.api.authenticate()
                self.r['steps']['find_wtb'] = 'running'
                if search_only:
                    found = next(self.iter_buyers(), None)
                    self.r.update(state="search_only_complete",reason="WTB diagnostic only",buyer=found)
                    return self.r
                pairs = self.iter_pairs(key)
                for buyer, seller in pairs:
                    self.boundary()
                    identity = sha(encode([buyer['url'],buyer['text_sha256'],seller['url'],seller['text_sha256']]))
                    if identity in attempted_pairs:
                        self.event('Duplicate pair skipped', pair_id=identity)
                        continue
                    attempted_pairs.add(identity)
                    pair = dict(pair_id=identity,buyer=buyer,seller=seller)
                    facts = dict(buyer_text=buyer['author_text'],seller_text=seller['author_text'])
                    body = encode(locked.payload(facts,'B'))
                    res = self.ledger.reserve(plan(body,'matcher'),'matcher',True)
                    self.r['steps']['matcher'] = 'running'
                    answer = self.model('matcher',body,key,res)
                    valid = locked.validate_answer(answer,facts)
                    self.r['steps']['matcher'] = 'completed'
                    assessment = dict(pair=pair,matcher_answer=answer,validated=valid)
                    self.r['assessments'].append(assessment)
                    self.r.update(pair=pair,matcher_answer=answer,validated=valid,decision=answer['decision'])
                    save(self.folder/'HANDOFF_INPUT.json',assessment)
                    number = len(self.r['assessments'])
                    self.handoff(self.folder/'HANDOFF_INPUT.json',self.folder/f'admin_review_{number:03d}')
                    self.r['admin_review'] = json.loads((self.folder/f'admin_review_{number:03d}'/'HANDOFF_REPORT.json').read_text())
                    self.persist()
                    if answer['decision'] == 'Send':
                        self.r['selection']['disclosure'] = 'First V2 Send selected for administrator review; later offers were not compared.'
                        self.r.update(state='completed',pipeline_completed=True,
                            reason=f'V2 Send saved for review after {number} assessments; no introduction sent')
                        return self.r
                    self.r['selection'].update(status='continuing_after_non_send',
                        disclosure='Non-Send assessment saved; continuing remaining candidates and queries.')
                    self.event('Assessment withheld; continuing seller queue',
                        buyer_url=buyer['url'],seller_url=seller['url'],decision=answer['decision'],reason=valid['Reason'])
                self.r.update(state='no_qualifying_match',pipeline_completed=True,
                    reason=f'No V2 Send in {len(self.r["assessments"])} assessments across {self.r["buyers_evaluated"]} buyers; checked queues exhausted. This does not establish no sellers exist.')
                return self.r
        except Exception as exc:
            from spending import BudgetHold
            reason = str(exc).replace(key,'[redacted]') if key else str(exc)
            limit = isinstance(exc,(RunLimitReached,BudgetHold))
            state = 'run_limit_reached' if limit else ('search_extraction_failed' if isinstance(exc,SearchExtractionError) else 'stopped')
            self.r.update(state=state,reason=reason[:1500],error_type=type(exc).__name__)
            return self.r
        finally:
            if pairs is not None: pairs.close()
            for step,value in self.r['steps'].items():
                if value == 'running': self.r['steps'][step] = self.r['state']
            if self.r['reader_execution'] == 'running':
                self.r['reader_execution'] = 'failed'
            if self.ledger:
                for operation in self.ledger.summary()['operations']:
                    if operation['state'] == 'reserved': self.ledger.release_unsent(operation['id'])
            if self.route: self.route.save()
            self.event('Run finished',state=self.r['state'],assessed_pairs=len(self.r['assessments']))
            self.r['finished_at'] = now()
            self.persist()
            with zipfile.ZipFile(self.folder/'SEND_THIS_REPORT.zip','w',zipfile.ZIP_DEFLATED) as archive:
                for path in sorted(self.folder.rglob('*')):
                    if path.is_file() and path.name != 'SEND_THIS_REPORT.zip': archive.write(path,path.relative_to(self.folder))
