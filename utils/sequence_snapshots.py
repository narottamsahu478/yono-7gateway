"""Per-activation immutable game order snapshots for registration and OTP fallback."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[1] / 'data' / 'sequence_snapshots.sqlite3'


def save_snapshot(provider, txn_id, phone, primary_game, games):
    """Persist a fresh copy, keyed by activation (not phone alone)."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    sequence = [primary_game] + [g for g in games if g != primary_game]
    with sqlite3.connect(DB_PATH, timeout=15) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS registration_sequences (
            provider TEXT NOT NULL, txn_id TEXT NOT NULL, phone TEXT NOT NULL,
            sequence_json TEXT NOT NULL, created_at TEXT NOT NULL,
            PRIMARY KEY (provider, txn_id))''')
        conn.execute('''INSERT OR IGNORE INTO registration_sequences
            (provider, txn_id, phone, sequence_json, created_at)
            VALUES (?, ?, ?, ?, ?)''', (str(provider), str(txn_id), str(phone),
                json.dumps(sequence), datetime.now(timezone.utc).isoformat()))
    return sequence


def load_snapshot(provider, txn_id):
    if not DB_PATH.exists():
        return None
    with sqlite3.connect(DB_PATH, timeout=15) as conn:
        row = conn.execute('SELECT sequence_json FROM registration_sequences WHERE provider=? AND txn_id=?',
                           (str(provider), str(txn_id))).fetchone()
    return json.loads(row[0]) if row else None
