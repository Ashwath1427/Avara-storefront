# AVARA storefront

The public storefront runs FastAPI on Render; private orders, sessions, installations and reviews use Supabase Postgres in `avara_store`. The desktop app and private owner builds are separate from this repository.

## Current customer workflow

1. Customer signs up/signs in and requests a cash order for Windows or Mac and a monthly or six-month plan.
2. Owner verifies payment and approves the order at `/owner` using the private owner key.
3. The ordering account receives a protected download. Mac users choose Apple Silicon or Intel; older links open a chip-choice page.
4. The customer installs the EXE/DMG and sends the displayed one-time installation code and order number to the owner.
5. The owner activates the approved order. The order determines Basic/Pro and the 30/180-day term; the platform installer is shared by both plans.
6. Owner controls support pause, continue, cancel, renew and change plan. Delete order works before or after activation; it hides the order and blocks downloads/activation. Show deleted orders exposes Restore order. Restoration retains the previous subscription dates; it does not add free time.

Monthly Basic ₹150; monthly Pro ₹200; six-month Basic ₹1,000; six-month Pro ₹1,400. Six-month entitlements are determined by the order. Provider billing is separate.

## Deployment and database

Render service root: `storefront`. Build: `pip install -r requirements.txt`. Start: `uvicorn server:app --host 0.0.0.0 --port $PORT`.

Run `supabase-persistence.sql` for a new database. Use the complete Supabase Session pooler PostgreSQL URI, with any password reserved characters URL encoded, as private `AVARA_STORE_DATABASE_URL`. It is not the HTTP `SUPABASE_URL`. Startup verifies PostgreSQL TLS with `supabase-ca.crt` and applies backward-compatible platform/deletion/review migrations. The private schema stays outside exposed API schemas with RLS enabled. Back up before migration; do not reset production data.

Required private settings include the existing database URI, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `AVARA_STORE_ADMIN_KEY` (24+ characters), `AVARA_STORE_PUBLIC_URL`, and the chosen mail transport configuration. Never put values in source, guides, screenshots or browser code. The bucket defaults to `avara-releases` and remains private.

Native delivery is enabled by the checked-in `installer-parts.json` manifest; `AVARA_INSTALLER_DOWNLOADS` can override the mode. The manifest is the authoritative filename, size, SHA-256 and ordered part inventory. Every manifest part is required. A local matching installer can also be served by the existing server file lookup. Do not change mode to source ZIP delivery after removing the legacy ZIPs unless they are deliberately restored.

Windows 1.0.1 is withdrawn from customer delivery while the `Trojan:Win32/Bearfoos.A!ml` Defender detection reported on an installed `AVARA.exe` is investigated. The stored numbered Windows parts are retained for evidence and hash comparison, but `enabled: false` prevents order downloads and excludes Windows from the published release catalog. Do not re-enable it merely because a local custom scan is clean. Re-enable only after source review, a newly built and Authenticode-signed release, Microsoft sample-analysis results, clean scans on fresh Windows machines, and an installation smoke test.

## Storage inventory

| Target | Single customer download | Internal parts | Total bytes |
|---|---|---:|---:|
| windows | `AVARA-1.0.1-windows-setup.exe` | 4 | 136,949,846 |
| mac-arm64 | `AVARA-1.0.1-mac-arm64.dmg` | 5 | 193,132,436 |
| mac-x86_64 | `AVARA-1.0.1-mac-x86_64.dmg` | 5 | 204,188,926 |

The current bucket has a 50 MB file limit. Each installer was split into at most 40 MiB objects. These are different numbered pieces, not duplicated installers. The server streams them in sequence, verifies each size/hash and the complete hash, and returns one original EXE/DMG. Deleting even one required part breaks that platform's download. Do not upload the private owner executable, owner source kit, `.env`, or activation data.

Required objects:

- `AVARA-1.0.1-windows-setup.exe.part01`
- `AVARA-1.0.1-windows-setup.exe.part02`
- `AVARA-1.0.1-windows-setup.exe.part03`
- `AVARA-1.0.1-windows-setup.exe.part04`
- `AVARA-1.0.1-mac-arm64.dmg.part01`
- `AVARA-1.0.1-mac-arm64.dmg.part02`
- `AVARA-1.0.1-mac-arm64.dmg.part03`
- `AVARA-1.0.1-mac-arm64.dmg.part04`
- `AVARA-1.0.1-mac-arm64.dmg.part05`
- `AVARA-1.0.1-mac-x86_64.dmg.part01`
- `AVARA-1.0.1-mac-x86_64.dmg.part02`
- `AVARA-1.0.1-mac-x86_64.dmg.part03`
- `AVARA-1.0.1-mac-x86_64.dmg.part04`
- `AVARA-1.0.1-mac-x86_64.dmg.part05`

The four old files `avara-basic.zip`, `avara-pro.zip`, `avara-basic-mac.zip`, `avara-pro-mac.zip` are legacy source releases, unused in native delivery mode. Their local backups were removed during cleanup. They may be removed from cloud storage while native mode is enabled. One customer-facing file does not require one cloud object. To use one cloud object per installer, move to storage that permits the full 137–204 MB files, then change the delivery configuration and verify downloads before removing parts.

## Email

Render uses the Resend HTTPS API through private `RESEND_API_KEY`. The default onboarding sender is restricted by Resend; configure a verified sender domain and `AVARA_STORE_EMAIL_FROM` for production recipients. Owner pages reporting Customer email: failed do not mean the approved download or activation is unavailable. Check delivery status and retest a real recipient after sender setup. SMTP is for supported local testing, not Render's default transport.

## Local run and tests

From `storefront`: `uvicorn server:app --host 127.0.0.1 --port 8090`. Use `AVARA_STORE_SECURE_COOKIE=0` only for local HTTP. SQLite is supported locally. Set `AVARA_STORE_TEST_MODE=1` only for local cash-order tests; never on a public service.

From the project root: `python -m pytest tests/test_order_deletion.py tests/test_installer_delivery.py tests/test_storefront_delivery.py tests/test_customer_activation.py -q`.

Customer instructions are maintained in `AVARA_SETUP_GUIDE.md` and `setup.html`; keep them synchronized. Packaged users need no separate Python or Node.js. Source users need a supported Python and source launcher.

## Recovery and operations

Use `migrate_store.py` only with a verified SQLite backup and a privately configured destination before accepting writes. If the backup is unavailable, `supabase-order-recovery.sql` documents verified customer reconciliation; old public mirror numbers can repeat. Do not infer ownership or password recovery from mirror records alone.

The footer lock reward is server-backed and limited to one claim per account; verify matching claims before manually fulfilling free access. Keep production backups and do not reset reward tables. Cash approval remains manual. Email verification, account recovery, rate limiting, monitoring, published legal notices and a security review remain production hardening work.

## Separate plan downloads

Each of Basic, Pro, 6-Month Basic and 6-Month Pro has a distinct Windows EXE, Apple Silicon DMG and Intel DMG download filename: 12 names total. The authenticated route derives the filename from the approved order; the server remains the authority for features and 30/180-day duration. All plans share the three verified app engines, so storage parts are not duplicated. `release-catalog.json` in the private bucket lists all 12 plan filenames, hashes, prices, terms and underlying pieces. The owner-only Sync release catalog control verifies availability and publishes this catalog using existing server-side storage credentials.

Local customer exports are generated with `python packaging/package_plan_installers.py` into `customer releases/1.0.1`, grouped into four plan folders. The script reconstructs and hashes the three original engines, exports 12 plan-labelled files and verifies every exported hash. These are labelled exports of verified 1.0.1 engines, not newly compiled app versions. Changing filenames or editing a catalog does not grant access. Keep owner builds separate.

## Universal installer licensing (v2)

One Windows setup and one Apple Silicon DMG for every plan. The plan is never in the installer.

- `GET /api/plans`: plan catalog (prices come from `avara_store.plans`).
- `POST /api/v2/installations/register`: the app chooses a plan. Creates a pending order and a one-time code `AVR-XXXX-XXXX` (48 h, one installation, one use). Only HMACs of codes and SHA-256 of installation secrets are stored.
- `POST /api/v2/activation/status`: Ed25519-signed ticket (installation, plan, expiry, six-month flag, issued-at). The app verifies it with the public key it ships and works offline until the ticket expires.
- Owner (`/owner`, session cookie + CSRF token, every action audit-logged): `/api/owner/login`, `/api/owner/license-orders` (+ `/approve`, `/{id}/reject`), `/api/owner/entitlements/{id}` (pause, resume, cancel, change_plan, extend), `/api/owner/audit`.
- `GET /api/downloads`, `GET /download/windows|mac`: public universal downloads, closed until `universal-installers.json` is enabled or `AVARA_PUBLIC_DOWNLOADS=true`.
- Old copies keep using `POST /api/activation/check`; old website orders keep their downloads.

Schema changes ship only as SQL migrations in `migrations/` (run in order in the Supabase SQL editor before deploying). Required new Render variables: `AVARA_TICKET_SIGNING_KEY`, `AVARA_CODE_PEPPER`. See `.env.example`. Approvals go through one function, `grant_license_order()`, so a payment provider can approve codes later without changing the code or ticket system.

