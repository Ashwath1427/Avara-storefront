"""Import a verified storefront SQLite snapshot into an empty Supabase schema."""
import argparse
from contextlib import closing
from pathlib import Path
import sqlite3

import server


TABLES = {
    'users': ('id', 'name', 'email', 'salt', 'password'),
    'sessions': ('token_hash', 'user_id', 'expires'),
    'orders': ('id', 'user_id', 'plan', 'addon', 'terms_version', 'created', 'status', 'approved_at', 'delivery_email_status', 'platform'),
    'order_email': ('order_id', 'status', 'created'),
    'installations': ('code', 'order_id', 'plan', 'expires'),
    'reward_locks': ('id', 'pin_hash', 'clues', 'consumed'),
    'reward_claims': ('code', 'user_id', 'lock_id', 'created'),
    'reward_attempts': ('user_id', 'created'),
}
OPTIONAL = set(TABLES) - {'users', 'orders'}
ORDER_DEFAULTS = {'status': "'pending'", 'approved_at': 'NULL', 'delivery_email_status': "'not_sent'", 'platform': "'windows'"}


def migrate(snapshot):
    if not server.DATABASE_URL:
        raise ValueError('Set AVARA_STORE_DATABASE_URL privately before importing.')
    with closing(sqlite3.connect(snapshot.resolve().as_uri() + '?mode=ro', uri=True)) as source:
        source.execute('BEGIN')
        source_tables = {row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'users', 'orders'} <= source_tables:
            raise ValueError('The selected file is not an AVARA storefront database.')
        with server.postgres_connection() as destination:
            destination.execute('LOCK TABLE ' + ', '.join(TABLES) + ' IN ACCESS EXCLUSIVE MODE')
            for table in TABLES:
                if destination.execute(f'SELECT 1 FROM {table} LIMIT 1').fetchone():
                    raise ValueError('The destination already contains records. Import stopped.')
            for table, fields in TABLES.items():
                if table in OPTIONAL and table not in source_tables:
                    continue
                existing = {row[1] for row in source.execute(f'PRAGMA table_info({table})')}
                projection = [ORDER_DEFAULTS[field] if field not in existing and table == 'orders'
                              and field in ORDER_DEFAULTS else field for field in fields]
                rows = source.execute(f'SELECT {", ".join(projection)} FROM {table}')
                placeholders = ', '.join('?' for _ in fields)
                for row in rows:
                    destination.execute(f'INSERT INTO {table} ({", ".join(fields)}) VALUES ({placeholders})', row)
            for table in ('users', 'orders'):
                destination.execute(
                    f"SELECT setval(pg_get_serial_sequence('avara_store.{table}', 'id'), "
                    f"COALESCE(MAX(id), 1), MAX(id) IS NOT NULL) FROM {table}"
                )


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot', type=Path)
    migrate(parser.parse_args().snapshot)
    print('Storefront snapshot imported.')
