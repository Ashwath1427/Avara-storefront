-- Copy every existing activation into avara_store.entitlements so current
-- customers keep their plan, expiry, pause/cancel state and Basic/Pro tier.
--
-- Read-only on the legacy tables: avara_store.orders and avara_store.installations
-- are not updated or deleted. Idempotent: re-running skips copied rows.
-- Mirrors storefront/server.py:sync_legacy_entitlement().
begin;

with legacy as (
    select i.code,
           i.plan as tier,
           i.expires,
           i.state,
           i.paused_at,
           o.plan as label,
           o.deleted_at,
           o.id as order_id,
           coalesce(o.approved_at, o.created) as started,
           case
               when o.plan like '6-Month Basic%' then 'six_month_basic'
               when o.plan like '6-Month Pro%' or o.plan like 'AVARA 6-Month Plus%' then 'six_month_pro'
               when o.plan like 'Basic%' then 'basic'
               when o.plan like 'Pro%' then 'pro'
               else i.plan
           end as mapped_plan
    from avara_store.installations i
    join avara_store.orders o on o.id = i.order_id
    where o.status = 'approved'
)
insert into avara_store.entitlements
    (legacy_code, plan_id, tier, six_month, state, starts_at, expires_at, paused_at, legacy_order_id, updated_at)
select l.code,
       case when p.tier = l.tier then l.mapped_plan else l.tier end,
       l.tier,
       case when l.label like '6-Month Basic%' or l.label like '6-Month Pro%' or l.label like 'AVARA 6-Month Plus%'
            then 1 else 0 end,
       -- A deleted order already answers "cancelled" to the app today; keep that answer.
       case when l.deleted_at is not null then 'cancelled' else l.state end,
       coalesce(l.started, extract(epoch from now())::bigint),
       l.expires,
       case when l.deleted_at is not null then coalesce(l.paused_at, l.deleted_at) else l.paused_at end,
       l.order_id,
       extract(epoch from now())::bigint
from legacy l
left join avara_store.plans p on p.id = l.mapped_plan
on conflict (legacy_code) do nothing;

commit;

-- Review the result (no customer contact data):
-- select e.id, e.plan_id, e.tier, e.state, to_timestamp(e.expires_at) as expires, e.legacy_order_id
-- from avara_store.entitlements e order by e.legacy_order_id;
