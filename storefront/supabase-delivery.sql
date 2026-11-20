-- Run after the existing public.orders table has been created.
alter table public.orders add column if not exists status text not null default 'pending';
alter table public.orders add column if not exists approved_at bigint;

-- Storefront customers must never read this table directly. The server uses the
-- private service-role key and exposes only the signed-in customer's own orders.
alter table public.orders enable row level security;
revoke all on table public.orders from anon, authenticated;
grant all on table public.orders to service_role;
