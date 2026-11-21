# AVARA storefront

## Customer activation

Use Supabase Postgres as the primary database on Render. Run [supabase-persistence.sql](supabase-persistence.sql) in Supabase SQL Editor. In Supabase, open **Connect → Session pooler**, copy the Postgres connection string, fill in the database password using URL encoding as needed, and save it as the private Render variable `AVARA_STORE_DATABASE_URL`. Keep the existing Resend and Supabase Storage settings. The application requires a trusted PostgreSQL TLS certificate and verifies the host name.

The SQL enables row-level security for the private `avara_store` tables. Use the table-owner connection for this configuration and leave the schema outside Supabase's exposed API schemas. The storefront handles customer access through its server routes. No browser-access policies are needed. If you ran an earlier copy of the schema SQL, you can run the updated file again.

Back up the original SQLite database before changing the environment or redeploying. If a verified backup is available, use `python migrate_store.py PATH_TO_SQLITE_BACKUP` with the destination URL set privately in the environment. Run the importer before accepting any writes to the new database. The old public Supabase account directory and order mirror remain available for owner review. The directory lacks password hashes, and order numbers in the old mirror may have been reused across Render restarts. Review and reconcile those records before restoring access if the original SQLite file is unavailable.

On Render, the new release checks the Supabase connection and schema at startup. Set the variable and run the SQL before deploying. Local runs can still use SQLite.

1. Build the customer packages from the desktop project with `python package_customer_releases.py` and upload the two ZIPs to the private release bucket.
2. Confirm cash payment and approve the order. The customer downloads and extracts the matching edition and runs `START_AVARA_WINDOWS.bat`.
3. Ask for the code shown by AVARA and the order number. On the approved order in `/owner`, enter the code and select **Activate installation**. Each approved order and code can be used once. Check the order number and code before confirming.

The initial activation lasts 30 days. The first unlock and periodic checks require internet. The activation is tied to the installation's local token, so keep the installation folder. Renewal, cancellation controls, and account or hardware binding are separate features. These source-based ZIPs expose application code to recipients, and local enforcement can be changed by anyone who edits their installed files.

The Basic edition currently includes 3 website generations, 3 presentations, and 30 screen-understanding requests per license period. Supported Google write actions require Pro. AI routing and cloud backup are outside these activation checks.

The footer's small sealed lock reveals a six-digit puzzle after sign-in. The first correct submission creates a unique claim for **one free month of AVARA Basic** and permanently consumes that PIN. Subsequent rounds generate a new PIN; old PINs are never reused. Claims are limited to one per account and saved receipts can be recovered by reopening the lock. Five failed guesses per account per minute are allowed. Separate accounts are not proof of separate people; review repeat claimants manually.

Winners must email a screenshot and claim code from their account email to `kasula.ashwath3180076@gmail.com` (override with `AVARA_REWARD_EMAIL`). The email button opens a draft; users must attach the screenshot and send it. No subscription is activated automatically and no message is sent by the server. Verify the code and matching account against `reward_claims` joined to `users` before manually approving/activating a free month; record your fulfillment so the same claim is not honored twice. Persistent `AVARA_STORE_DB` storage is required to preserve used PINs and claims across deploys. Do not reset this database during the promotion.

Run isolated reward regression checks with `python -m unittest discover -s tests -p test_storefront_rewards.py -v` from the project root.

Run from this folder with `uvicorn server:app --host 127.0.0.1 --port 8090`. Open http://127.0.0.1:8090. For local HTTP only, set `AVARA_STORE_SECURE_COOKIE=0` before starting.

For local-only order testing without Gmail, set `AVARA_STORE_TEST_MODE=1` and bind only to 127.0.0.1. Test orders are saved locally and no email is sent. Never enable test mode on a public service.

For Render, use a Python web service rooted in `storefront`, build with `pip install -r requirements.txt`, and start with `uvicorn server:app --host 0.0.0.0 --port $PORT`. Configure the primary database as described above. Owner notifications go to kasula.ashwath3180076@gmail.com, with the customer address as Reply-To.

The private owner page is `/owner`. Set `AVARA_STORE_ADMIN_KEY` to a random secret of at least 24 characters and `AVARA_STORE_PUBLIC_URL` to the public origin, such as `https://avara-storefront.onrender.com`. Never commit or share the owner key. Order approval requires the matching private release ZIP. Customers must sign in to the ordering account before the protected download route will serve an approved release.

For a Free Render service, create a **private** Supabase Storage bucket named `avara-releases` and upload `avara-basic.zip` and `avara-pro.zip`. The server proxies approved downloads using its existing private `SUPABASE_SERVICE_ROLE_KEY`; that key never reaches the browser. Override the bucket with `AVARA_RELEASE_BUCKET`, or object paths with `AVARA_BASIC_STORAGE_OBJECT` and `AVARA_PRO_STORAGE_OBJECT`. Do not add public read policies to the bucket.

As an alternative on paid Render, store the two ZIPs on a private persistent disk and set `AVARA_BASIC_ZIP` and `AVARA_PRO_ZIP` to their absolute paths. Local disk files take priority over Supabase Storage. Do not commit proprietary release files to the public storefront repository.

Render uses the Resend HTTPS API for notifications. Add `RESEND_API_KEY` as a private Render environment variable. For a first test, sign up for Resend with the Gmail inbox that receives AVARA orders. The default sender `onboarding@resend.dev` is restricted to the email associated with the Resend account. For ongoing production sending, verify your own domain in Resend and set `AVARA_STORE_EMAIL_FROM` to a sender on that domain. The Gmail App Password is only used by the local SMTP test, never by the Render notification path. Check Resend's dashboard for delivery and bounce status.

The old `public.orders` mirror is available in SQLite mode only. In PostgreSQL mode, view new orders under `avara_store.orders` or in the owner dashboard.

Before public launch, finalize the terms and privacy notice, configure HTTPS and persistent backups, add account email verification, password recovery, rate limiting, monitoring, and review security. Cash payment is still verified manually. New sign-ins last up to 14 days. Keep SQLite for local development or mount it on persistent storage when using that mode.
