# AVARA storefront

The footer's small sealed lock reveals a six-digit puzzle after sign-in. The first correct submission creates a unique claim for **one free month of AVARA Basic** and permanently consumes that PIN. Subsequent rounds generate a new PIN; old PINs are never reused. Claims are limited to one per account and saved receipts can be recovered by reopening the lock. Five failed guesses per account per minute are allowed. Separate accounts are not proof of separate people; review repeat claimants manually.

Winners must email a screenshot and claim code from their account email to `kasula.ashwath3180076@gmail.com` (override with `AVARA_REWARD_EMAIL`). The email button opens a draft; users must attach the screenshot and send it. No subscription is activated automatically and no message is sent by the server. Verify the code and matching account against `reward_claims` joined to `users` before manually approving/activating a free month; record your fulfillment so the same claim is not honored twice. Persistent `AVARA_STORE_DB` storage is required to preserve used PINs and claims across deploys. Do not reset this database during the promotion.

Run isolated reward regression checks with `python -m unittest discover -s tests -p test_storefront_rewards.py -v` from the project root.

Run from this folder with `uvicorn server:app --host 127.0.0.1 --port 8090`. Open http://127.0.0.1:8090. For local HTTP only, set `AVARA_STORE_SECURE_COOKIE=0` before starting.

For local-only order testing without Gmail, set `AVARA_STORE_TEST_MODE=1` and bind only to 127.0.0.1. Test orders are saved locally and no email is sent. Never enable test mode on a public service.

For Render, create a separate Python web service rooted in `storefront`, build with `pip install -r requirements.txt`, start with `uvicorn server:app --host 0.0.0.0 --port $PORT`, and attach persistent storage. Set `AVARA_STORE_DB` to a database file on that disk. Configure `AVARA_STORE_SMTP_USER` and `AVARA_STORE_SMTP_PASSWORD` as secret environment variables. The Gmail SMTP sender requires an appropriate app password. Notifications go to kasula.ashwath3180076@gmail.com; customer addresses are reply-to addresses.

The private owner page is `/owner`. Set `AVARA_STORE_ADMIN_KEY` to a random secret of at least 24 characters and `AVARA_STORE_PUBLIC_URL` to the public origin, such as `https://avara-storefront.onrender.com`. Never commit or share the owner key. Order approval requires the matching private release ZIP. Store `avara-basic.zip` and `avara-pro.zip` on a private persistent disk, then set `AVARA_BASIC_ZIP` and `AVARA_PRO_ZIP` to their absolute paths. Do not commit proprietary release files to the public storefront repository. Customers must sign in to the ordering account before the protected download route will serve an approved release.

Render uses the Resend HTTPS API for notifications. Add `RESEND_API_KEY` as a private Render environment variable. For a first test, sign up for Resend with the Gmail inbox that receives AVARA orders. The default sender `onboarding@resend.dev` is restricted to the email associated with the Resend account. For ongoing production sending, verify your own domain in Resend and set `AVARA_STORE_EMAIL_FROM` to a sender on that domain. The Gmail App Password is only used by the local SMTP test, never by the Render notification path. Check Resend's dashboard for delivery and bounce status.

Optional durable order backup: create a Supabase `orders` table with `local_order_id`, `user_name`, `customer_email`, `plan`, and `addon` columns, run `supabase-delivery.sql`, then set `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` in Render. Keep the service role key private.

Before public launch, finalize the terms and privacy notice, configure HTTPS and persistent backups, add account email verification, password recovery, rate limiting, monitoring, and review security. Cash payment is still verified manually. Existing SQLite sessions expire after 24 hours. On free Render instances without persistent storage, accounts, approvals, and order records may disappear on redeploy.
