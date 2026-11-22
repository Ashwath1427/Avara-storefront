"""Universal-installer licensing primitives shared by the storefront server.

Nothing here touches the database. The server owns persistence; this module
owns the plan catalog defaults, one-time code derivation, hashing, and the
Ed25519 activation tickets that the desktop app verifies with its public key.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets

# Default catalog. The database `plans` table overrides prices/availability once
# the migration has been applied; these values keep local SQLite and the very
# first deploy working with the published launch prices.
DEFAULT_PLANS = (
    {'id': 'basic', 'name': 'Basic', 'tier': 'basic', 'price_inr': 150, 'term_days': 30,
     'six_month': False, 'sort': 10, 'legacy_label': 'Basic — ₹150/month'},
    {'id': 'pro', 'name': 'Pro', 'tier': 'pro', 'price_inr': 200, 'term_days': 30,
     'six_month': False, 'sort': 20, 'legacy_label': 'Pro — ₹200/month'},
    {'id': 'six_month_basic', 'name': '6-Month Basic', 'tier': 'basic', 'price_inr': 1000, 'term_days': 180,
     'six_month': True, 'sort': 30, 'legacy_label': '6-Month Basic — ₹1,000/6 months'},
    {'id': 'six_month_pro', 'name': '6-Month Pro', 'tier': 'pro', 'price_inr': 1400, 'term_days': 180,
     'six_month': True, 'sort': 40, 'legacy_label': '6-Month Pro — ₹1,400/6 months'},
)
PLAN_IDS = tuple(plan['id'] for plan in DEFAULT_PLANS)

# Unambiguous characters only: no 0/O, 1/I/L, or U.
CODE_ALPHABET = '23456789ABCDEFGHJKMNPQRSTVWXYZ'
CODE_PREFIX = 'AVR'
CODE_LENGTH = 8
CODE_TTL_SECONDS = 48 * 3600
TICKET_VERSION = 1


def legacy_plan_id(label: str):
    """Map an old storefront order label to a plan id."""
    if label.startswith('6-Month Basic'):
        return 'six_month_basic'
    if label.startswith(('6-Month Pro', 'AVARA 6-Month Plus')):
        return 'six_month_pro'
    if label.startswith('Basic'):
        return 'basic'
    if label.startswith('Pro'):
        return 'pro'
    return None


# ---------------------------------------------------------------- tokens/codes

def token_bytes(token: str) -> bytes:
    if not isinstance(token, str) or len(token) != 64:
        raise ValueError('Invalid installation token')
    return bytes.fromhex(token)


def token_hash(token: str) -> str:
    """Full SHA-256 of the token. The legacy installation code is its first 32 hex digits."""
    return hashlib.sha256(token_bytes(token)).hexdigest()


def legacy_code(token: str) -> str:
    return token_hash(token)[:32].upper()


def installation_id(token: str) -> str:
    """Stable public id for one installation; the app derives the same value locally."""
    digest = hashlib.sha256(b'avara-installation-id\x00' + token_bytes(token)).hexdigest()
    return 'ins_' + digest[:24]


def derive_code(token: str, nonce: bytes) -> str:
    """Derive a short one-time code from the installation secret and a server nonce."""
    mac = hmac.new(token_bytes(token), b'avara-activation-code\x00' + nonce, hashlib.sha256).digest()
    value = int.from_bytes(mac[:16], 'big')
    chars = []
    for _ in range(CODE_LENGTH):
        value, index = divmod(value, len(CODE_ALPHABET))
        chars.append(CODE_ALPHABET[index])
    body = ''.join(chars)
    return f'{CODE_PREFIX}-{body[:4]}-{body[4:]}'


def normalize_code(text: str):
    """Return the canonical AVR-XXXX-XXXX form, or None if the input is not a code."""
    if not isinstance(text, str) or len(text) > 40:
        return None
    compact = ''.join(ch for ch in text.upper() if ch.isalnum())
    if compact.startswith(CODE_PREFIX) and len(compact) == len(CODE_PREFIX) + CODE_LENGTH:
        compact = compact[len(CODE_PREFIX):]
    if len(compact) != CODE_LENGTH or any(ch not in CODE_ALPHABET for ch in compact):
        return None
    return f'{CODE_PREFIX}-{compact[:4]}-{compact[4:]}'


def _pepper() -> bytes:
    return (os.getenv('AVARA_CODE_PEPPER', '').strip() or 'avara-code-hash-v1').encode()


def code_hash(code: str) -> str:
    canonical = normalize_code(code)
    if not canonical:
        raise ValueError('Invalid code')
    return hmac.new(_pepper(), canonical.encode(), hashlib.sha256).hexdigest()


def code_hint(code: str) -> str:
    """What the owner list may show without storing the code: the last four characters."""
    return f'{CODE_PREFIX}-••••-{normalize_code(code)[-4:]}'


def new_nonce() -> bytes:
    return secrets.token_bytes(16)


# ---------------------------------------------------------------------- tickets

def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


def _b64d(text: str) -> bytes:
    if not isinstance(text, str) or len(text) > 8192:
        raise ValueError('Invalid encoding')
    return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))


def canonical_json(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')


PASSPHRASE_MIN_CHARS = 24
PASSPHRASE_MIN_WORDS = 4
_PASSPHRASE_SALT = b'avara-ticket-signing-passphrase-v1'


def normalise_passphrase(text: str) -> str:
    """Spaces are allowed; leading/trailing spaces are ignored and runs of spaces count as one."""
    return ' '.join((text or '').split())


def seed_from_passphrase(text: str) -> bytes:
    """Turn the owner's own secret passphrase into the 32-byte Ed25519 seed.

    scrypt with a high cost makes every guess slow, because the matching public key ships
    inside the app and could be used to test guesses offline. Requires a long phrase.
    """
    phrase = normalise_passphrase(text)
    if len(phrase) < PASSPHRASE_MIN_CHARS or len(phrase.split(' ')) < PASSPHRASE_MIN_WORDS:
        raise ValueError(f'The signing passphrase must be at least {PASSPHRASE_MIN_CHARS} characters '
                         f'and {PASSPHRASE_MIN_WORDS} words.')
    return hashlib.scrypt(phrase.encode('utf-8'), salt=_PASSPHRASE_SALT, n=2 ** 17, r=8, p=1,
                          maxmem=256 * 1024 * 1024, dklen=32)


def _looks_like_seed(text: str) -> bool:
    import re
    if not re.fullmatch(r'[A-Za-z0-9_+/-]{43}=?', text):
        return False
    try:
        return len(_b64d(text.rstrip('=').replace('+', '-').replace('/', '_'))) == 32
    except ValueError:
        return False


def load_private_key(text: str | None = None):
    """Load the Ed25519 signing key from AVARA_TICKET_SIGNING_KEY.

    Accepts a generated key (43-character base64 seed), a PEM key, or the owner's own
    secret passphrase (spaces allowed, at least 24 characters and 4 words).
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    text = (os.getenv('AVARA_TICKET_SIGNING_KEY', '') if text is None else text).strip()
    if not text:
        return None
    if text.startswith('-----BEGIN'):
        key = serialization.load_pem_private_key(text.replace('\\n', '\n').encode(), password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError('AVARA_TICKET_SIGNING_KEY is not an Ed25519 key')
        return key
    if _looks_like_seed(text):
        return Ed25519PrivateKey.from_private_bytes(_b64d(text.rstrip('=').replace('+', '-').replace('/', '_')))
    return Ed25519PrivateKey.from_private_bytes(seed_from_passphrase(text))


def public_key_text(private_key) -> str:
    from cryptography.hazmat.primitives import serialization
    raw = private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return _b64e(raw)


def key_id(public_text: str) -> str:
    return hashlib.sha256(_b64d(public_text)).hexdigest()[:12]


def sign_ticket(payload: dict, private_key) -> dict:
    body = canonical_json(dict(payload, v=TICKET_VERSION))
    return {'payload': _b64e(body), 'sig': _b64e(private_key.sign(body)),
            'kid': key_id(public_key_text(private_key))}


def verify_ticket(ticket: dict, public_keys) -> dict:
    """Return the verified payload or raise ValueError. `public_keys` are base64url raw keys."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    if not isinstance(ticket, dict):
        raise ValueError('Ticket missing')
    body, signature = _b64d(ticket.get('payload', '')), _b64d(ticket.get('sig', ''))
    for text in public_keys:
        try:
            Ed25519PublicKey.from_public_bytes(_b64d(text)).verify(signature, body)
        except (InvalidSignature, ValueError):
            continue
        payload = json.loads(body)
        if not isinstance(payload, dict) or payload.get('v') != TICKET_VERSION:
            raise ValueError('Unsupported ticket version')
        return payload
    raise ValueError('Ticket signature is not valid')
