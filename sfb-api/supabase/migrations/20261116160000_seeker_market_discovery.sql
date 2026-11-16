-- Revised Stage B migration for worker project fyxglozykvhrfejrcrqz.
-- Do not apply the copy from commit 99c298f. Apply this file instead.
-- Apply it only when the matching worker (heartbeat revision reservation-v2) is ready to start.
-- The running worker must not keep calling the database after this file replaces the draft functions.
-- This seeds 2026-11-16 with baseline 7 and does not reset succeeded, failed, or an existing higher baseline.

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
    detail text,
    revision text
);

alter table public.seeker_worker_heartbeat add column if not exists revision text;

create table if not exists public.seeker_ai_usage (
    usage_date date primary key,
    baseline integer not null default 0,
    succeeded integer not null default 0,
    failed integer not null default 0
);

alter table public.seeker_ai_usage add column if not exists baseline integer not null default 0;
alter table public.seeker_ai_usage add column if not exists succeeded integer not null default 0;
alter table public.seeker_ai_usage add column if not exists failed integer not null default 0;

create table if not exists public.seeker_ai_reservations (
    id uuid primary key default gen_random_uuid(),
    usage_date date not null references public.seeker_ai_usage (usage_date),
    state text not null default 'reserved' check (state in ('reserved', 'succeeded', 'failed')),
    created_at timestamptz not null default now(),
    settled_at timestamptz
);

create unique index if not exists seeker_runs_one_active_market_job
    on public.seeker_runs (market_id, job_kind)
    where status in ('pending', 'running')
      and market_id is not null;

create index if not exists seeker_ai_reservations_open_day
    on public.seeker_ai_reservations (usage_date)
    where state = 'reserved';

insert into public.seeker_ai_usage as usage (usage_date, baseline)
values (date '2026-11-16', 7)
on conflict (usage_date) do update
set baseline = excluded.baseline
where usage.baseline < excluded.baseline
  and usage.succeeded = 0
  and usage.failed = 0
  and not exists (
      select 1
      from public.seeker_ai_reservations as existing_reservation
      where existing_reservation.usage_date = usage.usage_date
  );

drop function if exists public.settle_seeker_ai(text);
drop function if exists public.reserve_seeker_ai(integer, integer);
drop function if exists public.claim_next_seeker_run(text);
drop function if exists public.enqueue_market_discovery(uuid, text);

create function public.claim_next_seeker_run(worker text)
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

create function public.enqueue_market_discovery(market uuid, query text)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    existing public.seeker_runs;
    created public.seeker_runs;
begin
    select * into existing
    from public.seeker_runs
    where market_id = market
      and job_kind = 'market_discovery'
      and status in ('pending', 'running')
    order by requested_at
    limit 1
    for update;
    if found then
        return jsonb_build_object('created', false, 'run', to_jsonb(existing));
    end if;
    insert into public.seeker_runs (search_query, status, job_kind, market_id, requested_at)
    values (query, 'pending', 'market_discovery', market, now())
    returning * into created;
    return jsonb_build_object('created', true, 'run', to_jsonb(created));
exception
    when unique_violation then
        select * into existing
        from public.seeker_runs
        where market_id = market
          and job_kind = 'market_discovery'
          and status in ('pending', 'running')
        order by requested_at
        limit 1;
        if not found then
            return null;
        end if;
        return jsonb_build_object('created', false, 'run', to_jsonb(existing));
end;
$$;

create function public.reserve_seeker_ai(cap integer, baseline integer)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
    usage public.seeker_ai_usage;
    today date := (now() at time zone 'utc')::date;
    open_count integer;
    created_id uuid;
begin
    insert into public.seeker_ai_usage (usage_date, baseline)
    values (today, greatest(coalesce(baseline, 0), 0))
    on conflict (usage_date) do nothing;
    select * into usage
    from public.seeker_ai_usage
    where usage_date = today
    for update;
    select count(*) into open_count
    from public.seeker_ai_reservations
    where usage_date = today
      and state = 'reserved';
    if usage.baseline + usage.succeeded + open_count >= cap then
        return null;
    end if;
    insert into public.seeker_ai_reservations (usage_date, state)
    values (today, 'reserved')
    returning id into created_id;
    return created_id;
end;
$$;

create function public.settle_seeker_ai(reservation_id uuid, outcome text)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare
    reserved_day date;
    settled_day date;
begin
    if outcome not in ('succeeded', 'failed') then
        return false;
    end if;
    select usage_date into reserved_day
    from public.seeker_ai_reservations
    where id = reservation_id;
    if not found then
        return false;
    end if;
    perform 1
    from public.seeker_ai_usage
    where usage_date = reserved_day
    for update;
    update public.seeker_ai_reservations
    set state = outcome,
        settled_at = now()
    where id = reservation_id
      and state = 'reserved'
    returning usage_date into settled_day;
    if not found then
        return false;
    end if;
    if outcome = 'succeeded' then
        update public.seeker_ai_usage
        set succeeded = succeeded + 1
        where usage_date = settled_day;
    else
        update public.seeker_ai_usage
        set failed = failed + 1
        where usage_date = settled_day;
    end if;
    return true;
end;
$$;

revoke all on function public.claim_next_seeker_run(text) from public, anon, authenticated;
revoke all on function public.enqueue_market_discovery(uuid, text) from public, anon, authenticated;
revoke all on function public.reserve_seeker_ai(integer, integer) from public, anon, authenticated;
revoke all on function public.settle_seeker_ai(uuid, text) from public, anon, authenticated;

grant execute on function public.claim_next_seeker_run(text) to service_role;
grant execute on function public.enqueue_market_discovery(uuid, text) to service_role;
grant execute on function public.reserve_seeker_ai(integer, integer) to service_role;
grant execute on function public.settle_seeker_ai(uuid, text) to service_role;

alter table public.seeker_worker_heartbeat enable row level security;
alter table public.seeker_ai_usage enable row level security;
alter table public.seeker_ai_reservations enable row level security;

revoke all on public.seeker_worker_heartbeat from public, anon, authenticated;
revoke all on public.seeker_ai_usage from public, anon, authenticated;
revoke all on public.seeker_ai_reservations from public, anon, authenticated;

grant select, insert, update, delete on public.seeker_worker_heartbeat to service_role;
grant select, insert, update, delete on public.seeker_ai_usage to service_role;
grant select, insert, update, delete on public.seeker_ai_reservations to service_role;
