"""Durable, per-activation OTP routing state. No plaintext OTP or phone stored."""
import hashlib
import hmac
import os
import secrets
import sqlite3
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get('OTP_STATE_DB', str(ROOT / 'data' / 'otp_fallback.sqlite3')))
_LOCK = threading.RLock()


def _connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=15)
    conn.execute('PRAGMA busy_timeout=15000')
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('''CREATE TABLE IF NOT EXISTS meta (name TEXT PRIMARY KEY, value TEXT NOT NULL)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS seen (
        activation TEXT NOT NULL, code_hash TEXT NOT NULL,
        first_seen REAL NOT NULL, last_seen REAL NOT NULL,
        PRIMARY KEY(activation, code_hash))''')
    conn.execute('''CREATE TABLE IF NOT EXISTS attempts (
        activation TEXT NOT NULL, code_hash TEXT NOT NULL,
        game TEXT NOT NULL, attempted_at REAL NOT NULL,
        result TEXT NOT NULL DEFAULT 'attempted',
        PRIMARY KEY(activation, code_hash, game))''')
    conn.execute('''CREATE TABLE IF NOT EXISTS consumed (
        activation TEXT NOT NULL, code_hash TEXT NOT NULL,
        consumed_at REAL NOT NULL,
        PRIMARY KEY(activation, code_hash))''')
    return conn


def _digest(conn, value):
    row = conn.execute("SELECT value FROM meta WHERE name='salt'").fetchone()
    if row is None:
        conn.execute("INSERT OR IGNORE INTO meta VALUES ('salt', ?)", (secrets.token_hex(32),))
        row = conn.execute("SELECT value FROM meta WHERE name='salt'").fetchone()
    return hmac.new(bytes.fromhex(row[0]), str(value).encode(), hashlib.sha256).hexdigest()


def _keys(conn, provider, txn_id, code):
    activation = _digest(conn, f'{provider}:{txn_id}')
    code_hash = _digest(conn, f'{provider}:{txn_id}:{code}')
    return activation, code_hash


def observe(provider, txn_id, code):
    """Return fixed first-observed timestamp; never replace with a newer poll time."""
    now = time.time()
    with _LOCK, _connect() as conn:
        a, c = _keys(conn, provider, txn_id, code)
        conn.execute('INSERT OR IGNORE INTO seen VALUES (?, ?, ?, ?)', (a, c, now, now))
        conn.execute('UPDATE seen SET last_seen=? WHERE activation=? AND code_hash=?', (now, a, c))
        first_seen = conn.execute('SELECT first_seen FROM seen WHERE activation=? AND code_hash=?', (a, c)).fetchone()[0]
        consumed = conn.execute('SELECT 1 FROM consumed WHERE activation=? AND code_hash=?', (a, c)).fetchone() is not None
        return first_seen, consumed


def claim_attempt(provider, txn_id, code, game):
    """Atomic claim prevents repeated attempts, including after process restart."""
    with _LOCK, _connect() as conn:
        a, c = _keys(conn, provider, txn_id, code)
        if conn.execute('SELECT 1 FROM consumed WHERE activation=? AND code_hash=?', (a, c)).fetchone():
            return False
        cur = conn.execute('INSERT OR IGNORE INTO attempts (activation, code_hash, game, attempted_at) VALUES (?, ?, ?, ?)', (a, c, game, time.time()))
        return cur.rowcount == 1


def mark_result(provider, txn_id, code, game, result):
    with _LOCK, _connect() as conn:
        a, c = _keys(conn, provider, txn_id, code)
        conn.execute('UPDATE attempts SET result=? WHERE activation=? AND code_hash=? AND game=?', (result, a, c, game))


def mark_consumed(provider, txn_id, code):
    with _LOCK, _connect() as conn:
        a, c = _keys(conn, provider, txn_id, code)
        conn.execute('INSERT OR IGNORE INTO consumed VALUES (?, ?, ?)', (a, c, time.time()))


def prune(days=30):
    """Remove old activation state; do not call during an active activation."""
    cutoff = time.time() - days * 86400
    with _LOCK, _connect() as conn:
        conn.execute('DELETE FROM attempts WHERE activation IN (SELECT activation FROM seen GROUP BY activation HAVING MAX(last_seen) < ?)', (cutoff,))
        conn.execute('DELETE FROM consumed WHERE consumed_at < ?', (cutoff,))
        conn.execute('DELETE FROM seen WHERE last_seen < ?', (cutoff,))
