-- Additive isolation: existing API routes/results remain compatible.
-- No job is enabled, cancelled, or requeued by this migration.
create table if not exists public.sfb_component_control (
 component text primary key check(component in ('live_matches','seeker')),
 enabled boolean not null default false,
 updated_at timestamptz not null default now()
);
insert into public.sfb_component_control(component) values ('live_matches'),('seeker') on conflict do nothing;
create table if not exists public.sfb_component_workers (
 worker_id text primary key, component text not null references public.sfb_component_control,
 seen_at timestamptz not null, code_version text not null, v2_version text not null
);
create table if not exists public.sfb_component_executions (
 run_id uuid primary key references public.seeker_runs(id),
 component text not null references public.sfb_component_control,
 worker_id text not null, code_version text not null, v2_version text not null,
 started_at timestamptz not null default now(), recovered boolean not null default false
);
create table if not exists public.sfb_component_ai (
 reservation_id uuid primary key references public.seeker_ai_reservations(id),
 run_id uuid not null references public.seeker_runs(id),
 component text not null references public.sfb_component_control,
 recorded_at timestamptz not null default now()
);
create or replace function public.sfb_run_component(kind text, query text, market uuid)
returns text language sql immutable as $$
 select case when market is null and coalesce(kind,'consumer_search')='consumer_search'
   and length(trim(coalesce(query,'')))>0 then 'live_matches' else 'seeker' end
$$;
create or replace function public.claim_sfb_component_job(component_name text, worker text, release_id text, matcher_id text)
returns jsonb language plpgsql security definer set search_path=public as $$
declare claimed public.seeker_runs; held boolean;
begin
 if component_name not in ('live_matches','seeker') then raise exception 'Invalid component'; end if;
 perform 1 from public.sfb_component_control where component=component_name and enabled for share;
 if not found then return null; end if;
 select coalesce((select paused from public.seeker_global_pause where id='seeker'),true) into held;
 select * into claimed from public.seeker_runs r
 where r.status='pending' and public.sfb_run_component(r.job_kind,r.search_query,r.market_id)=component_name
 and (component_name='live_matches' or not held or coalesce(r.trigger,'user')<>'schedule')
 order by case when coalesce(r.trigger,'user')='user' then 0 else 1 end,r.requested_at
 for update skip locked limit 1;
 if not found then return null; end if;
 update public.seeker_runs set status='running',started_at=now(),claimed_by=worker where id=claimed.id returning * into claimed;
 insert into public.sfb_component_executions(run_id,component,worker_id,code_version,v2_version)
 values(claimed.id,component_name,worker,release_id,matcher_id)
 on conflict(run_id) do update set worker_id=excluded.worker_id,code_version=excluded.code_version,
 v2_version=excluded.v2_version,started_at=now();
 return to_jsonb(claimed);
end $$;
create or replace function public.set_sfb_component_enabled(component_name text, should_enable boolean)
returns void language plpgsql security definer set search_path=public as $$
begin
 update public.sfb_component_control set enabled=should_enable,updated_at=now() where component=component_name;
 if not found then raise exception 'Invalid component'; end if;
end $$;
-- A dead process may have spent money or persisted a partial result. Hold, never
-- silently rerun. Scope recovery to this component and recorded worker lease.
create or replace function public.hold_stale_sfb_component_jobs(component_name text)
returns integer language plpgsql security definer set search_path=public as $$
declare n integer;
begin
 update public.seeker_runs r set status='failed',completed_at=now(),
 result_summary='Search incomplete — component worker stopped; review saved evidence before retrying'
 from public.sfb_component_executions e
 where r.id=e.run_id and r.status='running' and e.component=component_name
 and e.started_at<now()-interval '5 minutes'
 and not exists(select 1 from public.sfb_component_workers w where w.worker_id=e.worker_id and w.component=e.component and w.seen_at>now()-interval '5 minutes');
 get diagnostics n=row_count; return n;
end $$;
alter table public.sfb_component_control enable row level security;
alter table public.sfb_component_workers enable row level security;
alter table public.sfb_component_executions enable row level security;
alter table public.sfb_component_ai enable row level security;
revoke all on public.sfb_component_control,public.sfb_component_workers,public.sfb_component_executions,public.sfb_component_ai from public,anon,authenticated;
grant select,insert,update,delete on public.sfb_component_control,public.sfb_component_workers,public.sfb_component_executions,public.sfb_component_ai to service_role;
revoke all on function public.sfb_run_component(text,text,uuid),public.claim_sfb_component_job(text,text,text,text),public.set_sfb_component_enabled(text,boolean),public.hold_stale_sfb_component_jobs(text) from public,anon,authenticated;
grant execute on function public.sfb_run_component(text,text,uuid),public.claim_sfb_component_job(text,text,text,text),public.set_sfb_component_enabled(text,boolean),public.hold_stale_sfb_component_jobs(text) to service_role;
