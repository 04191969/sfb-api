-- Fitment catalog for project fyxglozykvhrfejrcrqz.
-- Paste this into the Supabase SQL editor, then run:
--   python scripts/import_catalog.py
--
-- Django connects with the service role key, which bypasses row level
-- security. The anon key used by the frontend cannot read these tables.

create extension if not exists pgcrypto;

create table if not exists public.vehicles (
    id uuid primary key,
    year integer not null,
    make text not null,
    model text not null,
    generation text,
    engine text,
    body_style text,
    created_at timestamptz not null default now()
);

create table if not exists public.parts (
    id uuid primary key,
    part_name text not null,
    category text,
    oem_part_number text,
    brand text,
    created_at timestamptz not null default now()
);

create table if not exists public.fitment (
    id uuid primary key,
    part_id uuid not null references public.parts (id) on delete cascade,
    vehicle_id uuid not null references public.vehicles (id) on delete cascade,
    source text,
    confirmed_at timestamptz,
    created_at timestamptz not null default now(),
    constraint fitment_part_vehicle unique (part_id, vehicle_id)
);

create table if not exists public.watches (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null,
    part_id uuid not null references public.parts (id) on delete cascade,
    vehicle_id uuid references public.vehicles (id) on delete set null,
    created_at timestamptz not null default now(),
    constraint watches_user_part unique (user_id, part_id)
);

create table if not exists public.subscriptions (
    user_id uuid primary key,
    tier text not null default 'free',
    status text not null default 'inactive',
    stripe_customer_id text,
    stripe_subscription_id text,
    updated_at timestamptz not null default now(),
    constraint subscriptions_tier_check check (tier in ('free', 'paid'))
);

create index if not exists vehicles_make_model_year_idx
    on public.vehicles (make, model, year);

create index if not exists parts_part_name_category_idx
    on public.parts (part_name, category);

create index if not exists parts_oem_part_number_idx
    on public.parts (oem_part_number);

create index if not exists fitment_part_id_idx
    on public.fitment (part_id);

create index if not exists fitment_vehicle_id_idx
    on public.fitment (vehicle_id);

create index if not exists watches_user_id_idx
    on public.watches (user_id);

create index if not exists subscriptions_stripe_subscription_idx
    on public.subscriptions (stripe_subscription_id);

alter table public.vehicles enable row level security;
alter table public.parts enable row level security;
alter table public.fitment enable row level security;
alter table public.watches enable row level security;
alter table public.subscriptions enable row level security;
