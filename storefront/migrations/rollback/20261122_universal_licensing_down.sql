-- EMERGENCY ROLLBACK ONLY. Do not run as part of a normal deploy.
--
-- Removes the universal-licensing tables created by 20261122000001/2. The
-- legacy orders/installations tables are untouched, so already-installed old
-- copies keep their previous answers. This DELETES every new-flow code, order,
-- entitlement and the audit log. Export them first if you need them:
--   copy (select * from avara_store.audit_log) to stdout with csv header;
-- Deploy the previous storefront version before running this.
begin;
drop table if exists avara_store.rate_limits;
drop table if exists avara_store.owner_sessions;
drop trigger if exists audit_log_append_only on avara_store.audit_log;
drop table if exists avara_store.audit_log;
drop function if exists avara_store.audit_log_append_only();
drop table if exists avara_store.entitlements;
drop table if exists avara_store.license_orders;
drop table if exists avara_store.activation_codes;
drop table if exists avara_store.app_installations;
drop table if exists avara_store.plans;
commit;
