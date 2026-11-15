-- Confidence on a fitment row, plus the matcher's audit trail.
-- Run this in the Supabase SQL editor before grow_parts stores scored links.

alter table public.fitment add column if not exists confidence numeric(3, 2);
alter table public.fitment add column if not exists qualifier text;
alter table public.fitment add column if not exists match_method text;

create table if not exists public.match_log (
    id uuid primary key default gen_random_uuid(),
    part_id uuid references public.parts (id),
    vehicle_id uuid references public.vehicles (id),
    raw_text text,
    source_url text,
    ai_reasoning text,
    confidence numeric(3, 2),
    created_at timestamptz not null default now()
);

alter table public.match_log enable row level security;

grant select, insert, update, delete on public.fitment to service_role;
grant select, insert, update, delete on public.match_log to service_role;
