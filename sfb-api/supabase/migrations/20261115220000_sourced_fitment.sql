-- Structured fitment. Variant, brake configuration, axle, side, and assembly
-- are columns. Run this in the Supabase SQL editor.
-- The service role key cannot create tables through the API.

create table if not exists public.sourced_fitment (
    id uuid primary key default gen_random_uuid(),
    manufacturer text not null,
    part_number text not null,
    part_name text not null,
    year_from integer not null,
    year_to integer not null,
    make text not null,
    model text not null,
    variant text not null,
    brake_configuration text not null,
    axle text not null,
    side text not null,
    assembly text not null,
    option_code text,
    restrictions text,
    constraint sourced_fitment_identity check (
        length(trim(part_number)) > 0
        and length(trim(variant)) > 0
        and length(trim(brake_configuration)) > 0
        and length(trim(axle)) > 0
        and length(trim(side)) > 0
        and length(trim(assembly)) > 0
        and year_from <= year_to
    ),
    constraint sourced_fitment_part unique (
        part_number, year_from, year_to, make, model, variant,
        brake_configuration, axle, side, assembly
    )
);

create table if not exists public.sourced_fitment_source (
    id uuid primary key default gen_random_uuid(),
    part_number text not null,
    source_url text not null,
    checked_on date not null,
    note text,
    constraint sourced_fitment_source_url unique (part_number, source_url)
);

create table if not exists public.part_number_relation (
    id uuid primary key default gen_random_uuid(),
    part_number text not null,
    related_number text not null,
    relation text not null,
    replacement_confirmed boolean not null default false,
    source_url text not null,
    checked_on date not null,
    note text,
    constraint part_number_relation_pair unique (part_number, related_number, relation)
);

alter table public.sourced_fitment enable row level security;
alter table public.sourced_fitment_source enable row level security;
alter table public.part_number_relation enable row level security;

grant select, insert, update, delete on public.sourced_fitment to service_role;
grant select, insert, update, delete on public.sourced_fitment_source to service_role;
grant select, insert, update, delete on public.part_number_relation to service_role;

insert into public.sourced_fitment (
    manufacturer, part_number, part_name, year_from, year_to, make, model,
    variant, brake_configuration, axle, side, assembly, option_code, restrictions
) values (
    'Porsche', '9P1615123L', 'brake caliper', 2014, 2016, 'Porsche', '911',
    'Turbo', 'iron', 'front', 'left', 'caliper without brake pads', 'PR:-450',
    'Sources describe this row as without PCCB, option PR:-450. The PCCB left front caliper on the same diagram is 9P1615123K, option PR:450. Diagram 602-000 position 4 also lists 9P1615123L for the 991.1 Turbo S 3.8L 2014-2016. The same number is also on diagram 602-002 for the 991.2 Turbo 2016-2019. No finer 2015 production-date split is stated. Also use fastener 999 067 040 01.'
) on conflict on constraint sourced_fitment_part do nothing;

insert into public.sourced_fitment_source (part_number, source_url, checked_on, note) values
(
    '9P1615123L',
    'https://www.design911.com/parts/9P1615123L/',
    '2026-11-15',
    'Genuine Porsche caliper without brake pads. Diagram 602-000, front axle, 991 Turbo through 2016, 991.1 Turbo 3.8L 2014-16, position 4, also use 999 067 040 01.'
),
(
    '9P1615123L',
    'https://www.design911.com/p/brake-caliper-front-porsche-991-turbo/',
    '2026-11-15',
    'Front calipers 9P1615123L and 9P1615124L for the 991 Turbo will not fit PCCB.'
),
(
    '9P1615123L',
    'https://www.design911.com/diagrams/d/42975/9P1615123L',
    '2026-11-15',
    'Diagram 602-000. Indexed parts table: 9P1615123L, left, PR:-450. The fastener on the part page matches that row. Page text uses -I450 for cars without PCCB.'
),
(
    '9P1615123L',
    'https://teile.com/en/porsche-parts-shop/model-911-991/27/Wheels-brakes/602A-Disc-brake-front-axle/30/602-00-Disc-brake-front/Brake-calliper-Turbo-L/67514',
    '2026-11-15',
    'Genuine Porsche left Turbo calliper, front-axle diagram 602-00. Previous article numbers are supplier-reported only.'
),
(
    '9P1615123L',
    'https://teile.com/pdf/teilecom_en_PCCB-fixed-calliper-C2-C2S-C4-C4S-Turbo-L_9P1615123K.pdf',
    '2026-11-15',
    '9P1615123K is the PCCB left front caliper for the 991.1 Turbo 2014-2016.'
),
(
    '9P1615123L',
    'https://www.fcpeuro.com/products/porsche-disc-brake-caliper-front-left-911-por-99135142713',
    '2026-11-15',
    'Lists 99135142714, 99135142713, and 99135142712 as OE numbers. Does not state which number replaced which.'
)
on conflict on constraint sourced_fitment_source_url do nothing;

insert into public.part_number_relation (
    part_number, related_number, relation, replacement_confirmed, source_url, checked_on, note
) values
(
    '9P1615123L', '99135142714', 'supplier_reported_oe_number', false,
    'https://www.fcpeuro.com/products/porsche-disc-brake-caliper-front-left-911-por-99135142713',
    '2026-11-15',
    'FCP Euro lists this as an OE number on the 9P1615123L page. The replacement direction is not stated.'
),
(
    '9P1615123L', '99135142713', 'supplier_reported_previous', false,
    'https://teile.com/en/porsche-parts-shop/model-911-991/27/Wheels-brakes/602A-Disc-brake-front-axle/30/602-00-Disc-brake-front/Brake-calliper-Turbo-L/67514',
    '2026-11-15',
    'Teile lists this as a previous article number. Replacement applicability for this iron front-left caliper is not confirmed.'
),
(
    '9P1615123L', '99135142712', 'supplier_reported_previous', false,
    'https://teile.com/en/porsche-parts-shop/model-911-991/27/Wheels-brakes/602A-Disc-brake-front-axle/30/602-00-Disc-brake-front/Brake-calliper-Turbo-L/67514',
    '2026-11-15',
    'Teile lists this as a previous article number. Replacement applicability for this iron front-left caliper is not confirmed.'
)
on conflict on constraint part_number_relation_pair do nothing;
