begin;

create table if not exists public.storefront_accounts (
  email text primary key,
  display_name text not null,
  first_synced_at timestamptz not null default now()
);

alter table public.storefront_accounts enable row level security;
revoke all on table public.storefront_accounts from public, anon, authenticated;
grant select, insert, update on table public.storefront_accounts to service_role;

notify pgrst, 'reload schema';

commit;
