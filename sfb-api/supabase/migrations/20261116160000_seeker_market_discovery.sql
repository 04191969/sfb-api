-- Stage B for the worker project fyxglozykvhrfejrcrqz.
-- Run this in that project's SQL editor. The API service role cannot create tables.

alter table public.seeker_runs add column if not exists job_kind text not null default 'consumer_search';
alter table public.seeker_runs add column if not exists market_id uuid;
alter table public.seeker_runs add column if not exists claimed_by text;

alter table public.buyer_posts add column if not exists section_id uuid;
alter table public.buyer_posts add column if not exists source_id text;
alter table public.buyer_posts add column if not exists observed_at timestamptz;

alter table public.seller_listings add column if not exists section_id uuid;
alter table public.seller_listings add column if not exists source_id text;
alter table public.seller_listings add column if not exists discovery_url text;
alter table public.seller_listings add column if not exists currency text;
alter table public.seller_listings add column if not exists shipping numeric;
alter table public.seller_listings add column if not exists availability text;
alter table public.seller_listings add column if not exists observed_at timestamptz;

create table if not exists public.seeker_worker_heartbeat (
    worker_id text primary key,
    seen_at timestamptz not null,
    detail text
);

create table if not exists public.seeker_ai_usage (
    usage_date date primary key,
    reserved integer not null default 0,
    succeeded integer not null default 0,
    failed integer not null default 0,
    baseline integer not null default 0
);

create or replace function public.claim_next_seeker_run(worker text)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    claimed public.seeker_runs;
begin
    select * into claimed
    from public.seeker_runs
    where status = 'pending'
    order by requested_at
    for update skip locked
    limit 1;
    if not found then
        return null;
    end if;
    update public.seeker_runs
    set status = 'running',
        started_at = now(),
        claimed_by = worker
    where id = claimed.id
    returning * into claimed;
    return to_jsonb(claimed);
end;
$$;

create or replace function public.reserve_seeker_ai(cap integer, baseline integer)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare
    usage public.seeker_ai_usage;
    today date := (now() at time zone 'utc')::date;
begin
    insert into public.seeker_ai_usage (usage_date, baseline)
    values (today, greatest(coalesce(baseline, 0), 0))
    on conflict (usage_date) do nothing;
    select * into usage
    from public.seeker_ai_usage
    where usage_date = today
    for update;
    if usage.baseline + usage.succeeded + usage.reserved >= cap then
        return false;
    end if;
    update public.seeker_ai_usage
    set reserved = reserved + 1
    where usage_date = today;
    return true;
end;
$$;

create or replace function public.settle_seeker_ai(outcome text)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
    today date := (now() at time zone 'utc')::date;
begin
    if outcome = 'succeeded' then
        update public.seeker_ai_usage
        set reserved = greatest(reserved - 1, 0),
            succeeded = succeeded + 1
        where usage_date = today;
    elsif outcome = 'failed' then
        update public.seeker_ai_usage
        set reserved = greatest(reserved - 1, 0),
            failed = failed + 1
        where usage_date = today;
    end if;
end;
$$;

alter table public.seeker_worker_heartbeat enable row level security;
alter table public.seeker_ai_usage enable row level security;

grant select, insert, update, delete on public.seeker_worker_heartbeat to service_role;
grant select, insert, update, delete on public.seeker_ai_usage to service_role;
grant execute on function public.claim_next_seeker_run(text) to service_role;
grant execute on function public.reserve_seeker_ai(integer, integer) to service_role;
grant execute on function public.settle_seeker_ai(text) to service_role;
