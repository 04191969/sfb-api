-- Asking and sold observations used by the consumer price assessment.
-- One row per source and listing. Run this in the Supabase SQL editor.
-- The service role key cannot create tables through the API.

create table if not exists public.listing_evidence (
    id uuid primary key default gen_random_uuid(),
    source text not null,
    listing_id text not null,
    part_number text,
    price_amount numeric,
    price_currency text,
    shipping_amount numeric,
    shipping_currency text,
    tax_amount numeric,
    tax_currency text,
    core_amount numeric,
    core_refund numeric,
    status text not null default 'asking',
    observed_at timestamptz not null default now(),
    sold_at timestamptz,
    item_country text,
    url text,
    title text,
    constraint listing_evidence_source_listing unique (source, listing_id),
    constraint listing_evidence_status check (status in ('asking', 'sold'))
);

create index if not exists listing_evidence_part_number_idx
    on public.listing_evidence (part_number, status, observed_at desc);

create table if not exists public.supported_interchange (
    id uuid primary key default gen_random_uuid(),
    part_number text not null,
    equivalent_part_number text not null,
    configuration text not null,
    side text not null,
    assembly text not null,
    constraint supported_interchange_qualifiers check (
        length(trim(part_number)) > 0
        and length(trim(equivalent_part_number)) > 0
        and length(trim(configuration)) > 0
        and length(trim(side)) > 0
        and length(trim(assembly)) > 0
    ),
    constraint supported_interchange_pair unique (
        part_number, equivalent_part_number, configuration, side, assembly
    )
);

alter table public.listing_evidence enable row level security;
alter table public.supported_interchange enable row level security;

grant select, insert, update, delete on public.listing_evidence to service_role;
grant select, insert, update, delete on public.supported_interchange to service_role;
