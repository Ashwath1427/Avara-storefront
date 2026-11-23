-- 23 Nov 2026: the Supabase table editor's "orders" in schema public now shows every real order.
-- The old public.orders table was an empty, unused mirror (the website stores orders in avara_store).
alter table public.orders rename to old_unused_orders;
comment on table public.old_unused_orders is 'Old empty mirror table, NOT USED. Real orders: public.orders (view).';
alter view public.avara_all_orders rename to orders;
comment on view public.orders is 'Every AVARA order (app + old website), newest first. Read-only overview of avara_store.';
alter view public.avara_active_plans rename to plans;
comment on view public.plans is 'Every AVARA plan with customer, state and expiry (Windows and Mac). Read-only overview of avara_store.';
