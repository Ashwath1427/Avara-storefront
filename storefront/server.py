"""Standalone AVARA cash-order service; deploy separately from the desktop app."""
import hashlib
import hmac
import os
import secrets
import smtplib
import sqlite3
import ssl
import time
from email.message import EmailMessage
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent
DB_PATH = os.environ.get('AVARA_STORE_DB', str(ROOT / 'store.db'))
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

def db():
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.execute('PRAGMA foreign_keys=ON')
    con.executescript('''CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL UNIQUE, salt BLOB NOT NULL, password BLOB NOT NULL);
      CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), expires INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), plan TEXT NOT NULL, addon TEXT NOT NULL, terms_version TEXT NOT NULL, created INTEGER NOT NULL);''')
    return con

class Account(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12, max_length=128)

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

@app.get('/')
def home():
    return FileResponse(ROOT / 'index.html', headers={'Cache-Control':'no-store'})

@app.get('/{page}.html')
def page(page: str):
    if page not in ('terms', 'privacy'):
        raise HTTPException(404)
    return FileResponse(ROOT / (page + '.html'))

@app.post('/api/account')
def account(data: Account):
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
    return {'message':'Account created. Please sign in.'}

@app.post('/api/login')
def login(data: Account):
    with db() as con:
        row = con.execute('SELECT id,salt,password FROM users WHERE email=?', (data.email.strip().lower(),)).fetchone()
        salt = row[1] if row else bytes(16)
        value = hashlib.scrypt(data.password.encode(), salt=salt, n=16384, r=8, p=1)
        if not row or not hmac.compare_digest(value, row[2]):
            raise HTTPException(401, 'Email or password is incorrect.')
        token = secrets.token_urlsafe(32)
        con.execute('INSERT INTO sessions VALUES(?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), row[0], int(time.time())+86400))
    from fastapi.responses import JSONResponse
    response = JSONResponse({'message':'Signed in.'})
    response.set_cookie('avara_store_session', token, httponly=True, secure=os.environ.get('AVARA_STORE_SECURE_COOKIE','1') == '1', samesite='strict', max_age=86400)
    return response

@app.post('/api/order')
def order(data: Order, request: Request):
    user_id, name, email = session(request)
    if not data.accepted or data.plan not in ('Basic — ₹299/month','Pro — ₹599/month') or data.addon not in ('None','Setup assistance — discuss availability'):
        raise HTTPException(400, 'Select a valid plan and accept the terms.')
    smtp_user = os.environ.get('AVARA_STORE_SMTP_USER')
    # Google displays App Passwords in four-character groups; accept either form.
    smtp_password = ''.join(os.environ.get('AVARA_STORE_SMTP_PASSWORD', '').split())
    test_mode = os.environ.get('AVARA_STORE_TEST_MODE') == '1' and request.client and request.client.host in ('127.0.0.1', '::1')
    if (not smtp_user or not smtp_password) and not test_mode:
        raise HTTPException(503, 'Orders are unavailable until email notifications are configured.')
    with db() as con:
        cur = con.execute('INSERT INTO orders(user_id,plan,addon,terms_version,created) VALUES(?,?,?,?,?)', (user_id,data.plan,data.addon,'draft-1',int(time.time())))
        order_id = cur.lastrowid
    if test_mode and (not smtp_user or not smtp_password):
        return {'message': f'Test order #{order_id} saved locally. No email was sent and no payment was collected.'}
    msg = EmailMessage()
    msg['From'] = smtp_user
    msg['To'] = 'kasula.ashwath3180076@gmail.com'
    msg['Reply-To'] = email
    msg['Subject'] = f'AVARA cash order #{order_id}'
    msg.set_content(f'Order #{order_id}\nCustomer: {name}\nEmail: {email}\nPlan: {data.plan}\nAdd-on: {data.addon}\nPayment: cash\nTerms: draft-1 accepted')
    delivered = False
    for mode in ('starttls', 'ssl'):
        try:
            if mode == 'ssl':
                with smtplib.SMTP_SSL('smtp.gmail.com', 465, timeout=20, context=ssl.create_default_context()) as smtp:
                    smtp.login(smtp_user, smtp_password)
                    smtp.send_message(msg)
            else:
                with smtplib.SMTP('smtp.gmail.com', 587, timeout=20) as smtp:
                    smtp.ehlo()
                    smtp.starttls(context=ssl.create_default_context())
                    smtp.ehlo()
                    smtp.login(smtp_user, smtp_password)
                    smtp.send_message(msg)
            delivered = True
            break
        except (OSError, smtplib.SMTPException):
            continue
    if not delivered:
        return {'message':f'Order #{order_id} was saved, but email delivery failed. Contact the AVARA owner with this order number.'}
    return {'message':f'Cash order #{order_id} was saved and the notification was sent. The owner will contact you to arrange payment.'}
