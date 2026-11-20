# AVARA storefront

Run from this folder with `uvicorn server:app --host 127.0.0.1 --port 8090`. Open http://127.0.0.1:8090. For local HTTP only, set `AVARA_STORE_SECURE_COOKIE=0` before starting.

For local-only order testing without Gmail, set `AVARA_STORE_TEST_MODE=1` and bind only to 127.0.0.1. Test orders are saved locally and no email is sent. Never enable test mode on a public service.

For Render, create a separate Python web service rooted in `storefront`, build with `pip install -r requirements.txt`, start with `uvicorn server:app --host 0.0.0.0 --port $PORT`, and attach persistent storage. Set `AVARA_STORE_DB` to a database file on that disk.

Render uses the Resend HTTPS API for notifications. Add `RESEND_API_KEY` as a private Render environment variable. For a first test, sign up for Resend with the Gmail inbox that receives AVARA orders. The default sender `onboarding@resend.dev` is restricted to the email associated with the Resend account. For ongoing production sending, verify your own domain in Resend and set `AVARA_STORE_EMAIL_FROM` to a sender on that domain. The Gmail App Password is only used by the local SMTP test, never by the Render notification path. Check Resend's dashboard for delivery and bounce status.

Optional durable order backup: create a Supabase `orders` table with `local_order_id`, `user_name`, `customer_email`, `plan`, and `addon` columns, then set `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` in Render. Keep the service role key private.

Before public launch, finalize the terms and privacy notice, configure HTTPS and persistent backups, add account email verification, password recovery, rate limiting, monitoring, and review security. The current service records cash requests; it does not process payment, activate subscriptions, or deliver AVARA. Existing SQLite sessions expire after 24 hours. On free Render instances without persistent storage, records may disappear on redeploy.
