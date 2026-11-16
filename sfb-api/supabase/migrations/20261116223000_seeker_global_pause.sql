-- Global pause for scheduled discovery and rechecks.
-- Apply after 20261116210000_seeker_monitoring.sql.
-- Does not enable any market and does not change seeker_ai_usage.
-- A running job is left to finish. Pending scheduled jobs cannot be claimed
-- while paused, and Resume cancels them so they do not start as a catch-up burst.

create table if not exists public.seeker_global_pause (
    id text primary key,
    paused boolean not null default true,
    updated_at timestamptz not null default now(),
    constraint seeker_global_pause_singleton check (id = 'seeker')
);

insert into public.seeker_global_pause (id, paused)
values ('seeker', true)
on conflict (id) do nothing;

create or replace function public.enqueue_market_discovery(market uuid, query text, job_trigger text default 'user')
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    existing public.seeker_runs;
    created public.seeker_runs;
    held boolean := true;
    requested text := coalesce(nullif(job_trigger, ''), 'user');
begin
    select pause.paused into held
    from public.seeker_global_pause as pause
    where pause.id = 'seeker';
    if not found then
        held := true;
    end if;
    if requested = 'schedule' and held then
        return jsonb_build_object('created', false, 'paused', true);
    end if;
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
    values (query, 'pending', 'market_discovery', market, requested, now())
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

create or replace function public.claim_next_seeker_run(worker text)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    claimed public.seeker_runs;
    held boolean := true;
begin
    select pause.paused into held
    from public.seeker_global_pause as pause
    where pause.id = 'seeker';
    if not found then
        held := true;
    end if;
    select * into claimed
    from public.seeker_runs
    where status = 'pending'
      and (
          not held
          or coalesce(trigger, 'user') <> 'schedule'
      )
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

create or replace function public.set_seeker_global_pause(should_pause boolean)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
begin
    insert into public.seeker_global_pause (id, paused)
    values ('seeker', true)
    on conflict (id) do nothing;

    if should_pause then
        update public.seeker_global_pause
        set paused = true,
            updated_at = now()
        where id = 'seeker';
        return jsonb_build_object('paused', true);
    end if;

    -- Cancel queued scheduled jobs before clearing the pause, in this same
    -- transaction, so they cannot be claimed ahead of the new future times.
    update public.seeker_runs
    set status = 'cancelled',
        completed_at = now(),
        result_summary = 'Held during pause. The next run is in the future.'
    where status = 'pending'
      and trigger = 'schedule';

    update public.seeker_market_controls
    set next_discovery_at = now() + make_interval(mins => discovery_interval_minutes),
        next_recheck_at = now() + make_interval(hours => recheck_interval_hours),
        updated_at = now()
    where enabled = true;

    update public.seeker_global_pause
    set paused = false,
        updated_at = now()
    where id = 'seeker';
    return jsonb_build_object('paused', false);
end;
$$;

revoke all on function public.enqueue_market_discovery(uuid, text, text) from public, anon, authenticated;
revoke all on function public.claim_next_seeker_run(text) from public, anon, authenticated;
revoke all on function public.set_seeker_global_pause(boolean) from public, anon, authenticated;
grant execute on function public.enqueue_market_discovery(uuid, text, text) to service_role;
grant execute on function public.claim_next_seeker_run(text) to service_role;
grant execute on function public.set_seeker_global_pause(boolean) to service_role;

alter table public.seeker_global_pause enable row level security;
revoke all on public.seeker_global_pause from public, anon, authenticated;
grant select, insert, update, delete on public.seeker_global_pause to service_role;
