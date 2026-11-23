-- 23 Nov 2026: owner asked for ASHWATH's customer AVARA plan to stay Pro (the Owner app needs no plan).
update avara_store.entitlements set plan_id='pro', tier='pro', six_month=0,
       expires_at=extract(epoch from timestamptz '2026-12-23 15:46:56+00')::bigint, updated_at=extract(epoch from now())::bigint
 where id=6 and plan_id='owner';
delete from avara_store.plans where id='owner' and not exists (select 1 from avara_store.entitlements where plan_id='owner');
