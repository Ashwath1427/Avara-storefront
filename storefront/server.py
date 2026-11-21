"""Standalone AVARA cash-order service; deploy separately from the desktop app."""
import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import smtplib
import sqlite3
import ssl
import time
import urllib.request
from urllib.error import HTTPError
from urllib.parse import quote
from email.message import EmailMessage
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent
DB_PATH = os.environ.get('AVARA_STORE_DB', str(ROOT / 'store.db'))
DATABASE_URL = os.environ.get('AVARA_STORE_DATABASE_URL', '').strip()
SESSION_SECONDS = 14 * 86400
logger = logging.getLogger('uvicorn.error')


def database_failure_hint(exc):
    """Return a safe connection-error category without exposing database secrets."""
    message = str(exc).lower()
    checks = (
        (('password authentication failed', 'authentication failed'), 'credentials rejected'),
        (('tenant or user not found', 'user not found'), 'pooler user or project rejected'),
        (('certificate verify failed', 'certificate validation failed'), 'tls certificate rejected'),
        (('could not translate host name', 'name or service not known', 'nodename nor servname'),
         'database host not found'),
        (('timeout expired', 'timed out', 'connection timeout'), 'database connection timed out'),
        (('connection refused',), 'database connection refused'),
        (('network is unreachable', 'no route to host'), 'database network unreachable'),
        (('server closed the connection unexpectedly',), 'database closed the connection'),
    )
    for fragments, hint in checks:
        if any(fragment in message for fragment in fragments):
            return hint
    return 'unclassified connection failure'


def check_postgres_schema():
    with postgres_connection() as con:
        for table in ('users', 'sessions', 'orders', 'order_email', 'installations',
                      'reward_locks', 'reward_claims', 'reward_attempts'):
            con.execute(f'SELECT 1 FROM {table} LIMIT 0')


@asynccontextmanager
async def lifespan(_app):
    if os.getenv('RENDER') == 'true' and not DATABASE_URL:
        raise RuntimeError('Set AVARA_STORE_DATABASE_URL before deploying to Render.')
    if DATABASE_URL:
        try:
            await asyncio.to_thread(check_postgres_schema)
        except Exception as exc:
            # Connection errors may include private database details.
            hint = database_failure_hint(exc)
            raise RuntimeError(
                f'Storefront database check failed ({type(exc).__name__}: {hint}). '
                'Verify the connection and run supabase-persistence.sql.'
            ) from None
    yield


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


class StoreConnection(sqlite3.Connection):
    """Commit or roll back a with-block, then release the SQLite file handle."""
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def postgres_connection():
    import certifi
    import psycopg

    class StorePostgresConnection(psycopg.Connection):
        def execute(self, query, params=None, **kwargs):
            if query == 'BEGIN IMMEDIATE':
                # Serialize the existing read-then-write reward workflow across workers.
                query = 'SELECT pg_advisory_xact_lock(716683203)'
            return super().execute(query.replace('?', '%s') if params is not None else query, params, **kwargs)

    con = StorePostgresConnection.connect(
        DATABASE_URL, connect_timeout=8, sslmode='verify-full', sslrootcert=certifi.where(),
    )
    try:
        con.execute('SET LOCAL search_path TO avara_store, pg_catalog')
    except BaseException:
        con.close()
        raise
    return con


def db():
    if DATABASE_URL:
        return postgres_connection()
    con = sqlite3.connect(DB_PATH, timeout=10, factory=StoreConnection)
    con.execute('PRAGMA foreign_keys=ON')
    con.executescript('''CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL UNIQUE, salt BLOB NOT NULL, password BLOB NOT NULL);
      CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), expires INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), plan TEXT NOT NULL, addon TEXT NOT NULL, terms_version TEXT NOT NULL, created INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS order_email (order_id INTEGER PRIMARY KEY REFERENCES orders(id), status TEXT NOT NULL, created INTEGER NOT NULL);''')
    columns = {row[1] for row in con.execute('PRAGMA table_info(orders)')}
    if 'status' not in columns:
        con.execute("ALTER TABLE orders ADD COLUMN status TEXT NOT NULL DEFAULT 'pending'")
    if 'approved_at' not in columns:
        con.execute('ALTER TABLE orders ADD COLUMN approved_at INTEGER')
    if 'delivery_email_status' not in columns:
        con.execute("ALTER TABLE orders ADD COLUMN delivery_email_status TEXT NOT NULL DEFAULT 'not_sent'")
    return con

class Credentials(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12, max_length=128)

class Account(Credentials):
    name: str = Field(min_length=1, max_length=100)

class Order(BaseModel):
    plan: str
    addon: str
    accepted: bool

def session(request: Request):
    token = request.cookies.get('avara_store_session', '')
    with db() as con:
        row = con.execute('SELECT users.id, users.name, users.email FROM sessions JOIN users ON users.id=sessions.user_id WHERE token_hash=? AND expires>?', (hashlib.sha256(token.encode()).hexdigest(), int(time.time()))).fetchone()
    if not row:
        raise HTTPException(401, 'Please sign in first.')
    return row


def owner_session(request: Request):
    expected = os.getenv('AVARA_STORE_ADMIN_KEY', '').strip()
    if len(expected) < 24:
        raise HTTPException(503, 'Owner approval is not configured.')
    authorization = request.headers.get('Authorization', '')
    supplied = authorization[7:] if authorization.startswith('Bearer ') else ''
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(401, 'Owner key is incorrect.')


def plan_slug(plan: str):
    if plan.startswith('Basic'):
        return 'basic'
    if plan.startswith('Pro'):
        return 'pro'
    raise HTTPException(400, 'This order has an unknown plan.')


class ActivationApproval(BaseModel):
    code: str = Field(min_length=1, max_length=80)


class InstallationCheck(BaseModel):
    token: str = Field(pattern=r'^[a-f0-9]{64}$')


def activation_table(con):
    if not isinstance(con, sqlite3.Connection):
        return
    con.execute('''CREATE TABLE IF NOT EXISTS installations (
        code TEXT PRIMARY KEY,
        order_id INTEGER NOT NULL UNIQUE REFERENCES orders(id),
        plan TEXT NOT NULL CHECK(plan IN ('basic', 'pro')),
        expires INTEGER NOT NULL
    )''')


@app.post('/api/admin/order/{order_id}/activate')
def activate_installation(order_id: int, data: ActivationApproval, request: Request):
    owner_session(request)
    edition, _, digits = data.code.strip().upper().partition('-')
    code = digits.replace('-', '')
    if not re.fullmatch(r'[0-9A-F]{32}', code):
        raise HTTPException(400, 'Enter the activation code shown in the customer’s AVARA app.')
    with db() as con:
        activation_table(con)
        order = con.execute('SELECT plan,status FROM orders WHERE id=?', (order_id,)).fetchone()
        if not order or order[1] != 'approved':
            raise HTTPException(409, 'Only a paid and approved order can activate an installation.')
        plan = plan_slug(order[0])
        if edition != plan.upper():
            raise HTTPException(409, 'The code is from a different plan. Ask the customer to open the ZIP for this order.')
        expires = int(time.time()) + 30 * 86400
        inserted = con.execute('INSERT INTO installations VALUES (?,?,?,?) ON CONFLICT DO NOTHING', (code, order_id, plan, expires))
        if not inserted.rowcount:
            raise HTTPException(409, 'This activation code or order has already been used.')
    return {'message': f'AVARA {plan.title()} activated for 30 days.'}


@app.post('/api/activation/check')
def check_installation(data: InstallationCheck, response: Response):
    response.headers['Cache-Control'] = 'no-store'
    code = hashlib.sha256(bytes.fromhex(data.token)).hexdigest()[:32].upper()
    with db() as con:
        activation_table(con)
        row = con.execute(
            '''SELECT installations.plan, installations.expires
               FROM installations JOIN orders ON orders.id=installations.order_id
               WHERE installations.code=? AND orders.status='approved' ''', (code,),
        ).fetchone()
    if not row:
        return {'status': 'pending'}
    if row[1] <= time.time():
        return {'status': 'expired'}
    return {'status': 'active', 'plan': row[0], 'expires': row[1]}


def plan_filename(plan: str):
    return f'avara-{plan_slug(plan)}.zip'


def plan_bundle(plan: str):
    slug = plan_slug(plan)
    configured = os.getenv(f'AVARA_{slug.upper()}_ZIP', '').strip()
    return Path(configured) if configured else ROOT / 'deliverables' / plan_filename(plan)


def storage_request(plan: str, method='GET'):
    url = os.getenv('SUPABASE_URL', '').strip().rstrip('/')
    key = os.getenv('SUPABASE_SERVICE_ROLE_KEY', '').strip()
    bucket = os.getenv('AVARA_RELEASE_BUCKET', 'avara-releases').strip()
    if not url or not key or not bucket:
        return None
    slug = plan_slug(plan)
    object_name = os.getenv(f'AVARA_{slug.upper()}_STORAGE_OBJECT', plan_filename(plan)).strip()
    if not object_name:
        return None
    headers = {'apikey': key, 'Cache-Control': 'no-store'}
    if not key.startswith('sb_secret_'):
        headers['Authorization'] = 'Bearer ' + key
    path = '/'.join(quote(part, safe='') for part in object_name.split('/'))
    return urllib.request.Request(
        f'{url}/storage/v1/object/authenticated/{quote(bucket, safe="")}/{path}',
        headers=headers,
        method=method,
    )


def plan_bundle_ready(plan: str):
    if plan_bundle(plan).is_file():
        return True
    request = storage_request(plan, 'HEAD')
    if not request:
        return False
    try:
        with urllib.request.urlopen(request, timeout=8):
            return True
    except (HTTPError, OSError, ValueError):
        return False


def email_transport():
    if os.getenv('RESEND_API_KEY', '').strip():
        return 'resend'
    if os.getenv('RENDER') != 'true' and os.getenv('AVARA_STORE_SMTP_USER') and os.getenv('AVARA_STORE_SMTP_PASSWORD'):
        return 'smtp'
    return None

@app.get('/api/session')
def current_session(request: Request):
    _, name, _ = session(request)
    return {'name': name}


@app.get('/api/orders')
def customer_orders(request: Request):
    user_id, _, _ = session(request)
    with db() as con:
        rows = con.execute(
            'SELECT id,plan,addon,created,status,approved_at,delivery_email_status FROM orders WHERE user_id=? ORDER BY id DESC',
            (user_id,),
        ).fetchall()
    return {'orders': [
        {'id': row[0], 'plan': row[1], 'addon': row[2], 'created': row[3], 'status': row[4],
         'approved_at': row[5], 'delivery_email_status': row[6],
         'download_ready': row[4] == 'approved' and plan_bundle_ready(row[1])}
        for row in rows
    ]}


@app.get('/api/order/{order_id}/download')
def download_order(order_id: int, request: Request):
    user_id, _, _ = session(request)
    with db() as con:
        row = con.execute('SELECT plan,status FROM orders WHERE id=? AND user_id=?', (order_id, user_id)).fetchone()
    if not row:
        raise HTTPException(404, 'Order not found.')
    if row[1] != 'approved':
        raise HTTPException(403, 'This order is not ready for download yet.')
    plan = row[0]
    bundle = plan_bundle(plan)
    if bundle.is_file():
        return FileResponse(bundle, filename=bundle.name, media_type='application/zip', headers={'Cache-Control': 'no-store'})
    storage = storage_request(plan)
    if not storage:
        raise HTTPException(503, 'The release file is temporarily unavailable. Contact AVARA support.')
    try:
        upstream = urllib.request.urlopen(storage, timeout=20)
    except HTTPError as exc:
        logger.warning('Approved order %s release download HTTP %s', order_id, exc.code)
        raise HTTPException(503, 'The release file is temporarily unavailable. Contact AVARA support.')
    except (OSError, ValueError):
        raise HTTPException(503, 'The release file is temporarily unavailable. Contact AVARA support.')

    def stream_release():
        try:
            while chunk := upstream.read(1024 * 1024):
                yield chunk
        finally:
            upstream.close()

    return StreamingResponse(
        stream_release(), media_type='application/zip',
        headers={'Cache-Control': 'no-store', 'Content-Disposition': f'attachment; filename="{plan_filename(plan)}"'},
    )

@app.get('/api/order/{order_id}/notification')
def order_notification(order_id: int, request: Request):
    user_id, _, _ = session(request)
    with db() as con:
        row = con.execute(
            'SELECT status, order_email.created FROM order_email JOIN orders ON orders.id=order_email.order_id WHERE orders.id=? AND orders.user_id=?',
            (order_id, user_id),
        ).fetchone()
    if not row:
        raise HTTPException(404, 'Order notification unavailable.')
    status, created = row
    if status == 'pending' and time.time() - created > 90:
        status = 'unconfirmed'
    return {'status': status}

@app.get('/')
def home():
    return FileResponse(ROOT / 'index.html', headers={'Cache-Control':'no-store'})


@app.get('/owner')
def owner_page():
    return FileResponse(ROOT / 'owner.html', headers={'Cache-Control': 'no-store'})


@app.get('/AVARA_SETUP_GUIDE.md')
def setup_guide_file():
    return FileResponse(ROOT / 'AVARA_SETUP_GUIDE.md', filename='AVARA_SETUP_GUIDE.md', media_type='text/markdown')


@app.get('/reward.js')
def reward_script():
    return FileResponse(ROOT / 'reward.js', media_type='text/javascript', headers={'Cache-Control': 'no-store'})

@app.get('/{page}.html')
def page(page: str):
    if page not in ('terms', 'privacy', 'setup'):
        raise HTTPException(404)
    return FileResponse(ROOT / (page + '.html'))

@app.post('/api/account')
def account(data: Account, background_tasks: BackgroundTasks):
    email = data.email.strip().lower()
    name = data.name.strip()
    if not name or email.count('@') != 1 or any(c in email for c in '\r\n '):
        raise HTTPException(400, 'Enter a valid name and email address.')
    salt = secrets.token_bytes(16)
    hashed = hashlib.scrypt(data.password.encode(), salt=salt, n=16384, r=8, p=1)
    with db() as con:
        inserted = con.execute(
            'INSERT INTO users(name,email,salt,password) VALUES(?,?,?,?) ON CONFLICT(email) DO NOTHING',
            (name, email, salt, hashed),
        )
        if not inserted.rowcount:
            raise HTTPException(409, 'This account already exists. Please sign in.')
    background_tasks.add_task(_sync_supabase_account, email, name)
    return {'message':'Account created. Please sign in.'}

@app.post('/api/login')
def login(data: Credentials, background_tasks: BackgroundTasks):
    with db() as con:
        row = con.execute('SELECT id,salt,password,name FROM users WHERE email=?', (data.email.strip().lower(),)).fetchone()
        salt = row[1] if row else bytes(16)
        value = hashlib.scrypt(data.password.encode(), salt=salt, n=16384, r=8, p=1)
        if not row or not hmac.compare_digest(value, row[2]):
            raise HTTPException(401, 'Email or password is incorrect.')
        token = secrets.token_urlsafe(32)
        con.execute('INSERT INTO sessions VALUES(?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), row[0], int(time.time())+SESSION_SECONDS))
    from fastapi.responses import JSONResponse
    response = JSONResponse({'message':'Signed in.', 'name': row[3]})
    response.set_cookie('avara_store_session', token, httponly=True, secure=os.environ.get('AVARA_STORE_SECURE_COOKIE','1') == '1', samesite='strict', max_age=SESSION_SECONDS)
    background_tasks.add_task(_sync_supabase_account, data.email.strip().lower(), row[3])
    return response

def _sync_supabase_account(email: str, display_name: str):
    url = os.getenv('SUPABASE_URL', '').strip().rstrip('/')
    key = os.getenv('SUPABASE_SERVICE_ROLE_KEY', '').strip()
    if not url or not key:
        return

    headers = {
        'apikey': key,
        'Content-Type': 'application/json',
        'Prefer': 'resolution=merge-duplicates,return=minimal',
    }
    if not key.startswith('sb_secret_'):
        headers['Authorization'] = 'Bearer ' + key
    try:
        request = urllib.request.Request(
            url + '/rest/v1/storefront_accounts',
            data=json.dumps({'email': email, 'display_name': display_name}).encode(),
            headers=headers,
            method='POST',
        )
        with urllib.request.urlopen(request, timeout=8):
            pass
    except HTTPError as exc:
        logger.warning('Supabase storefront account sync returned HTTP %s', exc.code)
    except (OSError, ValueError) as exc:
        logger.warning('Supabase storefront account sync failed (%s)', type(exc).__name__)

def _send_order_email(order_id, name, email, plan, addon, transport):
    msg = EmailMessage()
    msg['To'] = 'kasula.ashwath3180076@gmail.com'
    msg['Subject'] = f'AVARA cash order #{order_id}'
    msg.set_content(f'Order #{order_id}\nCustomer: {name}\nEmail: {email}\nPlan: {plan}\nAdd-on: {addon}\nPayment: cash\nTerms: draft-1 accepted')
    if transport == 'resend':
        payload = {'from': os.getenv('AVARA_STORE_EMAIL_FROM', 'AVARA Orders <onboarding@resend.dev>'),
                   'to': [str(msg['To'])], 'subject': str(msg['Subject']), 'text': msg.get_content()}
        if email.isascii() and email.count('@') == 1 and all(email.split('@')) and not any(c in email for c in '<>,; \r\n'):
            payload['reply_to'] = email
        req = urllib.request.Request('https://api.resend.com/emails', data=json.dumps(payload).encode(), headers={
            'Authorization': 'Bearer ' + os.environ['RESEND_API_KEY'].strip(),
            'Content-Type': 'application/json', 'User-Agent': 'AVARA-Storefront/1.0',
        }, method='POST')
        with urllib.request.urlopen(req, timeout=8) as response:
            if not json.load(response).get('id'):
                raise ValueError('Missing email provider message ID')
        return
    msg['From'] = os.environ['AVARA_STORE_SMTP_USER'].strip()
    msg['Reply-To'] = email
    with smtplib.SMTP('smtp.gmail.com', 587, timeout=8) as smtp:
        smtp.starttls(context=ssl.create_default_context())
        smtp.login(str(msg['From']), ''.join(os.environ['AVARA_STORE_SMTP_PASSWORD'].split()))
        smtp.send_message(msg)

def _deliver_order_email(order_id, name, email, plan, addon, transport):
    status = 'unconfirmed'
    try:
        _send_order_email(order_id, name, email, plan, addon, transport)
        status = 'accepted'
    except HTTPError as exc:
        status = 'failed' if 400 <= exc.code < 500 else 'unconfirmed'
        logger.warning('Order %s email provider HTTP %s', order_id, exc.code)
    except (OSError, ValueError) as exc:
        logger.warning('Order %s email unconfirmed (%s)', order_id, type(exc).__name__)
    with db() as con:
        con.execute('UPDATE order_email SET status=? WHERE order_id=?', (status, order_id))


def _send_ready_email(order_id, name, email, plan, public_url, transport):
    msg = EmailMessage()
    msg['To'] = email
    msg['Subject'] = f'Your AVARA order #{order_id} is ready'
    msg.set_content(
        f'Hello {name},\n\nYour {plan} order has been approved and is ready.\n\n'
        f'1. Open {public_url}/#order and sign in with the account used for this order.\n'
        f'2. Open Your orders and choose Download AVARA.\n'
        f'3. Read the setup guide before starting: {public_url}/setup.html\n'
        '4. Launch AVARA and send the owner the activation code shown in the app and your order number.\n\n'
        'You will need either Ollama installed locally or compatible AI-provider API keys for AI features. '
        'Some integrations require their own accounts and credentials. Never share your account password or API keys.\n\n'
        'AVARA Support'
    )
    if transport == 'resend':
        payload = {
            'from': os.getenv('AVARA_STORE_EMAIL_FROM', 'AVARA Orders <onboarding@resend.dev>'),
            'to': [email], 'subject': str(msg['Subject']), 'text': msg.get_content(),
        }
        req = urllib.request.Request('https://api.resend.com/emails', data=json.dumps(payload).encode(), headers={
            'Authorization': 'Bearer ' + os.environ['RESEND_API_KEY'].strip(),
            'Content-Type': 'application/json', 'User-Agent': 'AVARA-Storefront/1.0',
        }, method='POST')
        with urllib.request.urlopen(req, timeout=8) as response:
            if not json.load(response).get('id'):
                raise ValueError('Missing email provider message ID')
        return
    msg['From'] = os.environ['AVARA_STORE_SMTP_USER'].strip()
    with smtplib.SMTP('smtp.gmail.com', 587, timeout=8) as smtp:
        smtp.starttls(context=ssl.create_default_context())
        smtp.login(str(msg['From']), ''.join(os.environ['AVARA_STORE_SMTP_PASSWORD'].split()))
        smtp.send_message(msg)


def _deliver_ready_email(order_id, name, email, plan, public_url, transport):
    status = 'unconfirmed'
    try:
        _send_ready_email(order_id, name, email, plan, public_url, transport)
        status = 'accepted'
    except HTTPError as exc:
        status = 'failed' if 400 <= exc.code < 500 else 'unconfirmed'
        logger.warning('Order %s delivery email provider HTTP %s', order_id, exc.code)
    except (OSError, ValueError) as exc:
        logger.warning('Order %s delivery email unconfirmed (%s)', order_id, type(exc).__name__)
    with db() as con:
        con.execute('UPDATE orders SET delivery_email_status=? WHERE id=?', (status, order_id))

def _save_supabase_order(order_id: int, user_id: int, name: str, email: str, plan: str, addon: str):
    if DATABASE_URL:
        return  # The durable order already lives in Supabase; legacy mirror IDs may collide.
    url = os.environ.get('SUPABASE_URL', '').rstrip('/')
    key = os.environ.get('SUPABASE_SERVICE_ROLE_KEY', '')
    if not url or not key:
        return
    payload = json.dumps({'local_order_id': order_id, 'user_name': name, 'customer_email': email, 'plan': plan, 'addon': addon}).encode()
    req = urllib.request.Request(url + '/rest/v1/orders', data=payload, method='POST', headers={'apikey': key, 'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'})
    try:
        with urllib.request.urlopen(req, timeout=8):
            pass
    except HTTPError as exc:
        logger.warning('Order %s Supabase backup HTTP %s', order_id, exc.code)
    except (OSError, ValueError) as exc:
        logger.warning('Order %s Supabase backup failed (%s)', order_id, type(exc).__name__)


def _update_supabase_order_status(order_id: int, status: str, approved_at: int):
    if DATABASE_URL:
        return
    url = os.environ.get('SUPABASE_URL', '').rstrip('/')
    key = os.environ.get('SUPABASE_SERVICE_ROLE_KEY', '')
    if not url or not key:
        return
    payload = json.dumps({'status': status, 'approved_at': approved_at}).encode()
    req = urllib.request.Request(
        url + f'/rest/v1/orders?local_order_id=eq.{order_id}', data=payload, method='PATCH',
        headers={'apikey': key, 'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'},
    )
    try:
        with urllib.request.urlopen(req, timeout=8):
            pass
    except HTTPError as exc:
        logger.warning('Order %s Supabase approval sync HTTP %s', order_id, exc.code)
    except (OSError, ValueError) as exc:
        logger.warning('Order %s Supabase approval sync failed (%s)', order_id, type(exc).__name__)


@app.get('/api/admin/orders')
def admin_orders(request: Request):
    owner_session(request)
    with db() as con:
        activation_table(con)
        rows = con.execute(
            '''SELECT orders.id,users.name,users.email,orders.plan,orders.addon,orders.created,
                      orders.status,orders.approved_at,orders.delivery_email_status,installations.expires
               FROM orders JOIN users ON users.id=orders.user_id
               LEFT JOIN installations ON installations.order_id=orders.id ORDER BY orders.id DESC'''
        ).fetchall()
    return {'orders': [
        {'id': row[0], 'name': row[1], 'email': row[2], 'plan': row[3], 'addon': row[4],
         'created': row[5], 'status': row[6], 'approved_at': row[7], 'delivery_email_status': row[8],
         'bundle_ready': plan_bundle_ready(row[3]), 'activation_expires': row[9]}
        for row in rows
    ]}


@app.post('/api/admin/order/{order_id}/approve')
def approve_order(order_id: int, request: Request, background_tasks: BackgroundTasks):
    owner_session(request)
    transport = email_transport()
    if not transport:
        raise HTTPException(503, 'Customer email delivery is not configured.')
    with db() as con:
        row = con.execute(
            'SELECT orders.plan,orders.status,users.name,users.email FROM orders JOIN users ON users.id=orders.user_id WHERE orders.id=?',
            (order_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404, 'Order not found.')
        plan, status, name, email = row
        if status == 'approved':
            return {'message': f'Order #{order_id} is already approved.', 'status': status}
        if not plan_bundle_ready(plan):
            raise HTTPException(409, f'Upload {plan_filename(plan)} before approving this order.')
        approved_at = int(time.time())
        updated = con.execute(
            "UPDATE orders SET status='approved',approved_at=?,delivery_email_status='pending' WHERE id=? AND status='pending'",
            (approved_at, order_id),
        )
        if not updated.rowcount:
            return {'message': f'Order #{order_id} is already approved.', 'status': 'approved'}
    public_url = os.getenv('AVARA_STORE_PUBLIC_URL', '').strip().rstrip('/') or str(request.base_url).rstrip('/')
    background_tasks.add_task(_deliver_ready_email, order_id, name, email, plan, public_url, transport)
    background_tasks.add_task(_update_supabase_order_status, order_id, 'approved', approved_at)
    return {'message': f'Order #{order_id} approved. The customer email is being sent.', 'status': 'approved'}

@app.post('/api/order')
def order(data: Order, request: Request, background_tasks: BackgroundTasks):
    user_id, name, email = session(request)
    if not data.accepted or data.plan not in ('Basic — ₹150/month','Pro — ₹200/month') or data.addon not in ('None','Setup assistance — discuss availability'):
        raise HTTPException(400, 'Select a valid plan and accept the terms.')
    on_render = os.getenv('RENDER') == 'true'
    test_mode = not on_render and os.getenv('AVARA_STORE_TEST_MODE') == '1' and request.client and request.client.host in ('127.0.0.1', '::1')
    transport = None
    if not test_mode:
        transport = email_transport()
    status = 'pending' if transport else 'disabled'
    with db() as con:
        order_id = con.execute(
            'INSERT INTO orders(user_id,plan,addon,terms_version,created) VALUES(?,?,?,?,?) RETURNING id',
            (user_id, data.plan, data.addon, 'draft-1', int(time.time())),
        ).fetchone()[0]
        con.execute('INSERT INTO order_email VALUES(?,?,?)', (order_id, status, int(time.time())))
    if test_mode:
        return {'message': f'Test order #{order_id} saved locally. No email was sent and no payment was collected.'}
    background_tasks.add_task(_save_supabase_order, order_id, user_id, name, email, data.plan, data.addon)
    if transport:
        background_tasks.add_task(_deliver_order_email, order_id, name, email, data.plan, data.addon, transport)
    else:
        logger.warning('Order %s saved without a configured email sender', order_id)
    return {'message': f'Cash order #{order_id} saved.', 'order_id': order_id, 'notification': status}


# Rewards live on the storefront server so refreshing or changing browsers cannot
# reuse a winning PIN. Keep this database on persistent storage in production.
@contextmanager
def reward_db():
    con = db()
    if not isinstance(con, sqlite3.Connection):
        with con:
            yield con
        return
    con.executescript('''
      CREATE TABLE IF NOT EXISTS reward_locks (
        id TEXT PRIMARY KEY, pin_hash TEXT NOT NULL UNIQUE, clues TEXT NOT NULL,
        consumed INTEGER NOT NULL DEFAULT 0);
      CREATE TABLE IF NOT EXISTS reward_claims (
        code TEXT PRIMARY KEY, user_id INTEGER NOT NULL UNIQUE REFERENCES users(id),
        lock_id TEXT NOT NULL UNIQUE REFERENCES reward_locks(id), created INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS reward_attempts (
        user_id INTEGER NOT NULL REFERENCES users(id), created INTEGER NOT NULL);
    ''')
    try:
        with con:
            yield con
    finally:
        con.close()


def current_reward_lock(con):
    import json
    row = con.execute('SELECT id,clues FROM reward_locks WHERE consumed=0 LIMIT 1').fetchone()
    if row:
        return {'lock_id': row[0], 'clues': json.loads(row[1])}
    # PIN hashes are unique across all rounds, including retired rounds.
    for _ in range(100):
        pin = ''.join(str(secrets.randbelow(10)) for _ in range(6))
        digest = hashlib.sha256(pin.encode()).hexdigest()
        if con.execute('SELECT 1 FROM reward_locks WHERE pin_hash=?', (digest,)).fetchone():
            continue
        lock_id = secrets.token_urlsafe(18)
        clues = []
        for digit in pin:
            offset = secrets.randbelow(15) + 5
            clues.append(f'{int(digit) + offset} minus {offset}')
        con.execute('INSERT INTO reward_locks(id,pin_hash,clues) VALUES(?,?,?)', (lock_id, digest, json.dumps(clues)))
        return {'lock_id': lock_id, 'clues': clues}
    raise HTTPException(503, 'A new lock could not be prepared. Try again later.')


def reward_receipt(row, email):
    return {'claim': {'code': row[0], 'created': row[1], 'account_email': email,
                      'plan': 'AVARA Basic', 'duration': '1 month',
                      'status': 'Awaiting screenshot email and owner approval',
                      'contact_email': os.environ.get('AVARA_REWARD_EMAIL', 'kasula.ashwath3180076@gmail.com')}}


@app.get('/api/reward')
def reward_status(request: Request):
    user_id, _, email = session(request)
    with reward_db() as con:
        con.execute('BEGIN IMMEDIATE')
        claim = con.execute('SELECT code,created FROM reward_claims WHERE user_id=?', (user_id,)).fetchone()
        if claim:
            return reward_receipt(claim, email)
        return current_reward_lock(con)


class RewardUnlock(BaseModel):
    lock_id: str = Field(min_length=10, max_length=64)
    pin: str = Field(pattern=r'^[0-9]{6}$', min_length=6, max_length=6)


@app.post('/api/reward/unlock')
def reward_unlock(data: RewardUnlock, request: Request):
    user_id, _, email = session(request)
    now = int(time.time())
    # Commit failed attempts too, and serialize competing correct answers.
    error = None
    receipt = None
    with reward_db() as con:
        con.execute('BEGIN IMMEDIATE')
        if con.execute('SELECT 1 FROM reward_claims WHERE user_id=?', (user_id,)).fetchone():
            error = (409, 'You already have a reward. Reopen the lock to view your saved claim.')
        else:
            con.execute('DELETE FROM reward_attempts WHERE created<?', (now - 60,))
            attempts = con.execute('SELECT COUNT(*) FROM reward_attempts WHERE user_id=?', (user_id,)).fetchone()[0]
            if attempts >= 5:
                error = (429, 'Too many guesses. Wait one minute before trying again.')
            else:
                con.execute('INSERT INTO reward_attempts VALUES(?,?)', (user_id, now))
                lock = con.execute('SELECT pin_hash,consumed FROM reward_locks WHERE id=?', (data.lock_id,)).fetchone()
                if not lock or lock[1]:
                    error = (409, 'This lock has already been opened or is unavailable. Reopen for a fresh puzzle.')
                elif not hmac.compare_digest(lock[0], hashlib.sha256(data.pin.encode()).hexdigest()):
                    error = (400, 'Incorrect combination. Solve the six clues in order.')
                else:
                    code = 'AVARA-BASIC-' + secrets.token_hex(10).upper()
                    con.execute('UPDATE reward_locks SET consumed=1 WHERE id=? AND consumed=0', (data.lock_id,))
                    con.execute('INSERT INTO reward_claims VALUES(?,?,?,?)', (code, user_id, data.lock_id, now))
                    receipt = reward_receipt((code, now), email)
    if error:
        raise HTTPException(*error)
    return receipt
