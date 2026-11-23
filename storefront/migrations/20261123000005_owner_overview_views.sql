-- 23 Nov 2026: easy-to-read overviews in the "public" schema for the Supabase table editor.
-- Read-only views over avara_store. security_invoker = the viewer's own permissions apply, and the public
-- API roles (anon / authenticated) get no access, so nothing becomes visible to website visitors.

create or replace view public.avara_all_orders with (security_invoker = true) as
select 'App (choose plan in AVARA)'::text as placed_in,
       lo.id as order_number,
       u.name as customer_name,
       u.email as customer_email,
       p.name as plan,
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
union all
select 'Website (old)'::text,
       o.id,
       u.name,
       u.email,
       o.plan,
       null::integer,
       case when o.deleted_at is not null then 'deleted' else o.status end,
       to_timestamp(o.created),
       to_timestamp(o.approved_at),
       o.platform,
       null::text
from avara_store.orders o
left join avara_store.users u on u.id = o.user_id
order by placed_at desc;

create or replace view public.avara_active_plans with (security_invoker = true) as
select e.id as plan_number,
       coalesce(u.name, lu.name) as customer_name,
       coalesce(u.email, lu.email) as customer_email,
       coalesce(p.name, e.plan_id) as plan,
       e.state,
       to_timestamp(e.starts_at) as started_at,
       to_timestamp(e.expires_at) as expires_at,
       coalesce(ai.platform, lo.platform) as computer,
       case when e.legacy_order_id is not null then 'Website order #' || e.legacy_order_id
            else 'App order #' || e.source_order_id end as from_order,
       to_timestamp(ai.last_seen) as app_last_seen
from avara_store.entitlements e
left join avara_store.app_installations ai on ai.id = e.installation_id
left join avara_store.users u on u.id = ai.user_id
left join avara_store.orders lo on lo.id = e.legacy_order_id
left join avara_store.users lu on lu.id = lo.user_id
left join avara_store.plans p on p.id = e.plan_id
order by e.expires_at desc;

revoke all on public.avara_all_orders, public.avara_active_plans from public, anon, authenticated;
comment on view public.avara_all_orders is 'Every AVARA order (app + old website), newest first. Read-only overview of avara_store.';
comment on view public.avara_active_plans is 'Every AVARA plan with customer, state and expiry (Windows and Mac). Read-only overview of avara_store.';
