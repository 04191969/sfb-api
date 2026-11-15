-- Email watches and the eBay listings found for them.
-- The watches table already exists. These columns extend it.

alter table public.watches alter column user_id drop not null;
alter table public.watches add column if not exists user_email text;
alter table public.watches add column if not exists active boolean not null default true;
alter table public.watches add column if not exists max_price numeric(10, 2);
alter table public.watches add column if not exists last_scanned_at timestamptz;

create unique index if not exists watches_email_part_idx
    on public.watches (lower(user_email), part_id)
    where user_email is not null;

create table if not exists public.scan_results (
    id uuid primary key default gen_random_uuid(),
    watch_id uuid not null references public.watches (id) on delete cascade,
    ebay_item_id text not null,
    title text,
    price numeric(10, 2),
    currency text default 'USD',
    listing_url text,
    image_url text,
    condition text,
    seller_name text,
    found_at timestamptz not null default now(),
    notified boolean not null default false,
    constraint scan_results_watch_item unique (watch_id, ebay_item_id)
);

create index if not exists scan_results_watch_id_idx
    on public.scan_results (watch_id, found_at desc);

alter table public.scan_results enable row level security;

grant select, insert, update, delete on public.watches to service_role;
grant select, insert, update, delete on public.scan_results to service_role;
