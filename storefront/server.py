"""Standalone AVARA cash-order service; deploy separately from the desktop app."""
import hashlib
import hmac
import json
import logging
import os
import secrets
import smtplib
import sqlite3
import ssl
import time
import urllib.request
from urllib.error import HTTPError
from email.message import EmailMessage
from contextlib import contextmanager
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent
DB_PATH = os.environ.get('AVARA_STORE_DB', str(ROOT / 'store.db'))
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
logger = logging.getLogger('uvicorn.error')

def db():
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.execute('PRAGMA foreign_keys=ON')
    con.executescript('''CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL UNIQUE, salt BLOB NOT NULL, password BLOB NOT NULL);
      CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), expires INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), plan TEXT NOT NULL, addon TEXT NOT NULL, terms_version TEXT NOT NULL, created INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS order_email (order_id INTEGER PRIMARY KEY REFERENCES orders(id), status TEXT NOT NULL, created INTEGER NOT NULL);''')
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

@app.get('/api/session')
def current_session(request: Request):
    _, name, _ = session(request)
    return {'name': name}

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

@app.get('/{page}.html')
def page(page: str):
    if page not in ('terms', 'privacy'):
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
    try:
        with db() as con:
            con.execute('INSERT INTO users(name,email,salt,password) VALUES(?,?,?,?)', (name,email,salt,hashed))
    except sqlite3.IntegrityError:
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
        con.execute('INSERT INTO sessions VALUES(?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), row[0], int(time.time())+86400))
    from fastapi.responses import JSONResponse
    response = JSONResponse({'message':'Signed in.', 'name': row[3]})
    response.set_cookie('avara_store_session', token, httponly=True, secure=os.environ.get('AVARA_STORE_SECURE_COOKIE','1') == '1', samesite='strict', max_age=86400)
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

def _save_supabase_order(order_id: int, user_id: int, name: str, email: str, plan: str, addon: str):
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

@app.post('/api/order')
def order(data: Order, request: Request, background_tasks: BackgroundTasks):
    user_id, name, email = session(request)
    if not data.accepted or data.plan not in ('Basic — ₹299/month','Pro — ₹599/month') or data.addon not in ('None','Setup assistance — discuss availability'):
        raise HTTPException(400, 'Select a valid plan and accept the terms.')
    on_render = os.getenv('RENDER') == 'true'
    test_mode = not on_render and os.getenv('AVARA_STORE_TEST_MODE') == '1' and request.client and request.client.host in ('127.0.0.1', '::1')
    transport = None
    if not test_mode:
        if os.getenv('RESEND_API_KEY', '').strip():
            transport = 'resend'
        elif not on_render and os.getenv('AVARA_STORE_SMTP_USER') and os.getenv('AVARA_STORE_SMTP_PASSWORD'):
            transport = 'smtp'
    status = 'pending' if transport else 'disabled'
    with db() as con:
        cur = con.execute('INSERT INTO orders(user_id,plan,addon,terms_version,created) VALUES(?,?,?,?,?)', (user_id,data.plan,data.addon,'draft-1',int(time.time())))
        order_id = cur.lastrowid
        con.execute('INSERT INTO order_email VALUES(?,?,?)', (order_id, status, int(time.time())))
    if test_mode:
        return {'message': f'Test order #{order_id} saved locally. No email was sent and no payment was collected.'}
    background_tasks.add_task(_save_supabase_order, order_id, user_id, name, email, data.plan, data.addon)
    if transport:
        background_tasks.add_task(_deliver_order_email, order_id, name, email, data.plan, data.addon, transport)
    else:
        logger.warning('Order %s saved without a configured email sender', order_id)
    return {'message': f'Cash order #{order_id} saved.', 'order_id': order_id, 'notification': status}
