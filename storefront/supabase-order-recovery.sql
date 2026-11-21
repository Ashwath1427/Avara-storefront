-- Run supabase-persistence.sql first. This script does not import orders.
begin;

create table if not exists avara_store.order_recoveries (
    source_reference text primary key,
    order_id bigint not null unique references avara_store.orders(id),
    restored_at timestamptz not null default now()
);
alter table avara_store.order_recoveries enable row level security;
revoke all on avara_store.order_recoveries from public, anon, authenticated, service_role;

-- Use only after privately matching the customer's new account to the original
-- request and confirming the original terms acceptance. Restores a pending order.
create or replace function avara_store.restore_verified_order(
    source_reference text,
    customer_id bigint,
    order_plan text,
    order_addon text,
    original_terms_version text,
    original_created_at timestamptz default now()
) returns bigint
language plpgsql
security invoker
set search_path = pg_catalog, avara_store
as $body$
declare
    new_order_id bigint;
begin
    if source_reference is null or length(btrim(source_reference)) not between 1 and 160
       or original_terms_version is null or btrim(original_terms_version) = ''
       or original_created_at is null then
        raise exception 'A unique recovery reference, original terms version, and date are required.';
    end if;
    if order_plan is null or order_plan not in (
        'Basic — ₹299/month', 'Basic — ₹150/month',
        'Pro — ₹599/month', 'Pro — ₹200/month'
    ) or order_addon is null or order_addon not in (
        'None', 'Setup assistance — discuss availability'
    ) then
        raise exception 'Use the original AVARA plan and add-on.';
    end if;
    if not exists (select from avara_store.users where id = customer_id) then
        raise exception 'The verified customer account must exist first.';
    end if;

    insert into avara_store.orders (user_id, plan, addon, terms_version, created)
    values (customer_id, order_plan, order_addon, original_terms_version,
            floor(extract(epoch from original_created_at))::bigint)
    returning id into new_order_id;

    -- A reused reference rolls back both inserts, including concurrent retries.
    insert into avara_store.order_recoveries (source_reference, order_id)
    values (btrim(source_reference), new_order_id);
    return new_order_id;
end;
$body$;

revoke all on function avara_store.restore_verified_order(text, bigint, text, text, text, timestamptz)
from public, anon, authenticated, service_role;
commit;

-- Review the old mirror if it exists:
-- select local_order_id, user_name, customer_email, plan, addon from public.orders;
-- Compare with the new orders and accounts before restoring any missing row:
-- select id, email from avara_store.users;
-- select id, user_id, plan, addon, status from avara_store.orders;
-- Example only. Use a unique verified receipt reference, not the old order number:
-- select avara_store.restore_verified_order(
--     'receipt-unique-reference', 123, 'Basic — ₹150/month', 'None', 'draft-1',
--     '2026-11-20 20:00:00+05:30'
-- );
