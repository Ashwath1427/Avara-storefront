-- 23 Nov 2026: Supabase advisor clean-up for the AVARA store (no data is changed or removed).

-- 1. Security: the RLS auto-enable event-trigger helper must not be callable through the public REST API.
--    Event triggers still run it automatically; only direct calls by anon/signed-in API users are blocked.
revoke execute on function public.rls_auto_enable() from public, anon, authenticated;

-- 2. Performance: index every foreign key the advisor reported.
create index if not exists app_installations_user_id_idx on avara_store.app_installations (user_id);
create index if not exists email_log_user_id_idx on avara_store.email_log (user_id);
create index if not exists entitlements_legacy_order_id_idx on avara_store.entitlements (legacy_order_id);
create index if not exists entitlements_plan_id_idx on avara_store.entitlements (plan_id);
create index if not exists entitlements_source_order_id_idx on avara_store.entitlements (source_order_id);
create index if not exists license_orders_plan_id_idx on avara_store.license_orders (plan_id);
create index if not exists reviews_user_id_idx on avara_store.reviews (user_id);
create index if not exists reward_attempts_user_id_idx on avara_store.reward_attempts (user_id);
create index if not exists sessions_user_id_idx on avara_store.sessions (user_id);

-- 3. Labels so the Supabase table editor is not confusing.
comment on table public.orders is
  'NOT USED by AVARA (left over from an early version; always empty). Live data: avara_store.license_orders = orders placed inside the AVARA app; avara_store.orders = old website orders; avara_store.entitlements = active plans; avara_store.users = customer accounts.';
comment on table public.storefront_accounts is
  'Copy of customer names/emails for viewing in Supabase. Sign-in uses avara_store.users.';
comment on table avara_store.license_orders is
  'Orders placed inside the AVARA app (choose plan -> AVR code -> owner approves). Empty until a customer chooses a plan.';
comment on table avara_store.users is 'Customer accounts used by the website and the AVARA app sign-in (email + scrypt password).';
