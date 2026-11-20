# AVARA storefront

Run from this folder with `uvicorn server:app --host 127.0.0.1 --port 8090`. Open http://127.0.0.1:8090. For local HTTP only, set `AVARA_STORE_SECURE_COOKIE=0` before starting.

For local-only order testing without Gmail, set `AVARA_STORE_TEST_MODE=1` and bind only to 127.0.0.1. Test orders are saved locally and no email is sent. Never enable test mode on a public service.

For Render, create a separate Python web service rooted in `storefront`, build with `pip install -r requirements.txt`, start with `uvicorn server:app --host 0.0.0.0 --port $PORT`, and attach persistent storage. Set `AVARA_STORE_DB` to a database file on that disk. Configure `AVARA_STORE_SMTP_USER` and `AVARA_STORE_SMTP_PASSWORD` as secret environment variables. The Gmail SMTP sender requires an appropriate app password. Notifications go to kasula.ashwath3180076@gmail.com; customer addresses are reply-to addresses.

Before public launch, finalize the terms and privacy notice, configure HTTPS and persistent backups, add account email verification, password recovery, rate limiting, monitoring, and review security. The current service records cash requests; it does not process payment, activate subscriptions, or deliver AVARA. Existing SQLite sessions expire after 24 hours. On free Render instances without persistent storage, records may disappear on redeploy.
