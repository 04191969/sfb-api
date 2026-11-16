-- Monitoring around the f972a58 search. Do not change buyer recognition,
-- listing qualification, budgets, or introductions.
-- All markets stay paused: enabled defaults to false.
-- A crashed worker does not prove an open model call failed.

alter table public.seeker_worker_heartbeat add column if not exists code_version text;

alter table public.seeker_runs add column if not exists trigger text not null default 'user';
alter table public.seeker_runs add column if not exists new_matches integer;
alter table public.seeker_runs add column if not exists still_valid integer;
alter table public.seeker_runs add column if not exists no_longer_qualifying integer;
alter table public.seeker_runs add column if not exists skipped_sources integer;
alter table public.seeker_runs add column if not exists ai_calls integer;

alter table public.buyer_posts add column if not exists discovered_at timestamptz;
alter table public.buyer_posts add column if not exists last_checked_at timestamptz;
alter table public.buyer_posts add column if not exists last_matched_at timestamptz;
alter table public.seller_listings add column if not exists discovered_at timestamptz;
alter table public.seller_listings add column if not exists last_checked_at timestamptz;
alter table public.seller_listings add column if not exists last_matched_at timestamptz;

update public.buyer_posts
set discovered_at = scraped_at
where discovered_at is null and scraped_at is not null;

update public.seller_listings
set discovered_at = scraped_at
where discovered_at is null and scraped_at is not null;

create table if not exists public.seeker_market_controls (
    market_id uuid primary key,
    enabled boolean not null default false,
    discovery_interval_minutes integer not null default 30,
    recheck_interval_hours integer not null default 6,
    next_discovery_at timestamptz,
    next_recheck_at timestamptz,
    updated_at timestamptz not null default now()
);

create table if not exists public.seeker_check_history (
    id uuid primary key default gen_random_uuid(),
    market_id uuid,
    run_id uuid,
    source_url text not null,
    source_kind text not null,
    checked_at timestamptz not null default now(),
    evidence text,
    outcome text not null,
    changes text
);

alter table public.seeker_ai_reservations drop constraint if exists seeker_ai_reservations_state_check;
alter table public.seeker_ai_reservations
    add constraint seeker_ai_reservations_state_check
    check (state in ('reserved', 'succeeded', 'failed', 'uncertain'));

drop function if exists public.enqueue_market_discovery(uuid, text);

create or replace function public.enqueue_market_discovery(market uuid, query text, job_trigger text default 'user')
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
    insert into public.seeker_runs (search_query, status, job_kind, market_id, trigger, requested_at)
    values (query, 'pending', 'market_discovery', market, coalesce(nullif(job_trigger, ''), 'user'), now())
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

revoke all on function public.enqueue_market_discovery(uuid, text, text) from public, anon, authenticated;
grant execute on function public.enqueue_market_discovery(uuid, text, text) to service_role;

create or replace function public.enqueue_market_discovery(market uuid, query text)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
begin
    return public.enqueue_market_discovery(market, query, 'user');
end;
$$;

revoke all on function public.enqueue_market_discovery(uuid, text) from public, anon, authenticated;
grant execute on function public.enqueue_market_discovery(uuid, text) to service_role;

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
    order by
        case
            when job_kind = 'consumer_search' then 0
            when coalesce(trigger, 'user') = 'user' then 1
            else 2
        end,
        requested_at
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

create or replace function public.settle_seeker_ai(reservation_id uuid, outcome text)
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

create or replace function public.hold_uncertain_seeker_ai()
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
    held integer;
begin
    update public.seeker_ai_reservations
    set state = 'uncertain',
        settled_at = now()
    where state = 'reserved'
      and created_at < now() - interval '2 minutes';
    get diagnostics held = row_count;
    return held;
end;
$$;

create or replace function public.requeue_stale_seeker_runs(stale_seconds integer)
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
    moved integer;
begin
    update public.seeker_runs as run
    set status = 'pending',
        claimed_by = null,
        started_at = null
    where run.status = 'running'
      and not exists (
          select 1
          from public.seeker_worker_heartbeat as heartbeat
          where heartbeat.worker_id = run.claimed_by
            and heartbeat.seen_at > now() - make_interval(secs => greatest(stale_seconds, 90))
      );
    get diagnostics moved = row_count;
    return moved;
end;
$$;

revoke all on function public.hold_uncertain_seeker_ai() from public, anon, authenticated;
revoke all on function public.requeue_stale_seeker_runs(integer) from public, anon, authenticated;
grant execute on function public.hold_uncertain_seeker_ai() to service_role;
grant execute on function public.requeue_stale_seeker_runs(integer) to service_role;

alter table public.seeker_market_controls enable row level security;
alter table public.seeker_check_history enable row level security;
revoke all on public.seeker_market_controls from public, anon, authenticated;
revoke all on public.seeker_check_history from public, anon, authenticated;
grant select, insert, update, delete on public.seeker_market_controls to service_role;
grant select, insert, update, delete on public.seeker_check_history to service_role;
