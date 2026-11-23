-- 23 Nov 2026 (owner request):
-- 1. An "Owner" plan (full Pro features, not for sale) for the owner's own AVARA.
-- 2. Delete the old website orders the owner no longer needs (all already marked deleted) and the
--    cancelled plans that came from them. Website order #10 (active Mac plan) and app order #1 stay.
-- 3. public.orders shows each order's CURRENT plan (an order's plan can be changed later by the owner).
begin;

insert into avara_store.plans (id, name, tier, price_inr, term_days, six_month, active, sort)
values ('owner', 'Owner', 'pro', 0, 3650, 0, 0, 99)
on conflict (id) do update set name = excluded.name, tier = excluded.tier, active = 0;

-- The owner's own computer (ASHWATH, app order #1): Owner plan for 10 years.
update avara_store.entitlements
   set plan_id = 'owner', tier = 'pro', six_month = 0, state = 'active', paused_at = null,
       expires_at = extract(epoch from timestamptz '2036-11-23 00:00:00+05:30')::bigint,
       updated_at = extract(epoch from now())::bigint
 where id = 6 and installation_id = 'ins_48cb5f49e5a17e55917a0522';

-- Cancelled plans from the deleted website orders, then everything that points at those orders.
delete from avara_store.entitlements where id in (1, 2, 4, 5) and state = 'cancelled';
delete from avara_store.installations where order_id in (select id from avara_store.orders where deleted_at is not null);
delete from avara_store.order_email where order_id in (select id from avara_store.orders where deleted_at is not null);
delete from avara_store.order_recoveries where order_id in (select id from avara_store.orders where deleted_at is not null);
delete from avara_store.orders where deleted_at is not null;

drop view if exists public.orders;
create view public.orders with (security_invoker = true) as
select 'App'::text as source,
       lo.id as order_number,
       u.name as customer_name,
       u.email as customer_email,
       coalesce(cp.name, p.name) as plan,
       p.name as plan_when_ordered,
       lo.price_inr,
       lo.status,
       to_timestamp(lo.created) as placed_at,
       to_timestamp(lo.decided_at) as decided_at,
       ai.platform as computer,
       lo.payment_reference
from avara_store.license_orders lo
left join avara_store.app_installations ai on ai.id = lo.installation_id
left join avara_store.users u on u.id = ai.user_id
left join avara_store.plans p on p.id = lo.plan_id
left join avara_store.entitlements e on e.source_order_id = lo.id
left join avara_store.plans cp on cp.id = e.plan_id
union all
select 'Website'::text,
       o.id,
       u.name,
       u.email,
       coalesce(cp.name, split_part(o.plan, ' — ', 1)),
       split_part(o.plan, ' — ', 1),
       null::integer,
       o.status,
       to_timestamp(o.created),
       to_timestamp(o.approved_at),
       o.platform,
       null::text
from avara_store.orders o
left join avara_store.users u on u.id = o.user_id
left join avara_store.entitlements e on e.legacy_order_id = o.id
left join avara_store.plans cp on cp.id = e.plan_id
where o.deleted_at is null
order by placed_at desc;

revoke all on public.orders from public, anon, authenticated;
comment on view public.orders is 'Every AVARA order (app and website together), newest first, with its current plan.';
commit;
