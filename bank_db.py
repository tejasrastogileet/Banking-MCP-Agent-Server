"""SQLite-backed demo ledger used by the MCP server.

All business rules live here (not in the LLM layer), so they can be unit tested
without any network access or API key.
"""
from __future__ import annotations

import os
import secrets
import sqlite3
import time
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

DAILY_LIMIT_CENTS = 50_000 * 100   # 50,000.00 per rolling 24h per account
PENDING_TTL_SECONDS = 300          # a proposed transfer expires after 5 minutes


class BankError(Exception):
    """A business-rule violation. The message is safe to show to the agent."""


# --------------------------------------------------------------------------- setup
def connect(path: str | None = None) -> sqlite3.Connection:
    path = path or os.getenv("BANK_DB", "bank.db")
    conn = sqlite3.connect(path, isolation_level=None)  # autocommit; we manage transactions
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_schema(conn)
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS accounts (
            id            TEXT PRIMARY KEY,
            owner         TEXT NOT NULL,
            balance_cents INTEGER NOT NULL CHECK (balance_cents >= 0)
        );
        CREATE TABLE IF NOT EXISTS transactions (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            from_id      TEXT NOT NULL REFERENCES accounts(id),
            to_id        TEXT NOT NULL REFERENCES accounts(id),
            amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
            created_at   INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS pending_transfers (
            token        TEXT PRIMARY KEY,
            from_id      TEXT NOT NULL,
            to_id        TEXT NOT NULL,
            amount_cents INTEGER NOT NULL,
            created_at   INTEGER NOT NULL
        );
        """
    )
    if conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0:
        conn.executemany(
            "INSERT INTO accounts (id, owner, balance_cents) VALUES (?, ?, ?)",
            [
                ("ACC-1001", "Tejas", 25_000 * 100),
                ("ACC-1002", "Rahul", 10_000 * 100),
                ("ACC-1003", "Priya", 5_000 * 100),
            ],
        )


# ------------------------------------------------------------------------- helpers
def to_cents(amount: float) -> int:
    try:
        value = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        raise BankError("Amount is not a valid number.")
    if value <= 0:
        raise BankError("Amount must be greater than zero.")
    return int(value * 100)


def fmt(cents: int) -> str:
    return f"{cents / 100:,.2f}"


def _account(conn: sqlite3.Connection, account_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
    if row is None:
        raise BankError(f"Account '{account_id}' does not exist.")
    return row


def _spent_last_24h(conn: sqlite3.Connection, account_id: str, now: int) -> int:
    row = conn.execute(
        "SELECT COALESCE(SUM(amount_cents), 0) FROM transactions "
        "WHERE from_id = ? AND created_at >= ?",
        (account_id, now - 86_400),
    ).fetchone()
    return row[0]


# ---------------------------------------------------------------------- read tools
def get_balance(conn: sqlite3.Connection, account_id: str) -> dict:
    acc = _account(conn, account_id)
    return {
        "account_id": acc["id"],
        "owner": acc["owner"],
        "balance": acc["balance_cents"] / 100,
    }


def list_transactions(conn: sqlite3.Connection, account_id: str, limit: int = 5) -> dict:
    _account(conn, account_id)
    limit = max(1, min(int(limit), 50))
    rows = conn.execute(
        "SELECT * FROM transactions WHERE from_id = ? OR to_id = ? "
        "ORDER BY id DESC LIMIT ?",
        (account_id, account_id, limit),
    ).fetchall()
    return {
        "account_id": account_id,
        "transactions": [
            {
                "id": r["id"],
                "type": "debit" if r["from_id"] == account_id else "credit",
                "counterparty": r["to_id"] if r["from_id"] == account_id else r["from_id"],
                "amount": r["amount_cents"] / 100,
                "created_at": r["created_at"],
            }
            for r in rows
        ],
    }


# --------------------------------------------------------------- two-step transfer
def propose_transfer(
    conn: sqlite3.Connection,
    from_id: str,
    to_id: str,
    amount: float,
    now: int | None = None,
) -> dict:
    """Step 1: validate and park the transfer. Nothing moves yet."""
    now = int(time.time()) if now is None else now
    cents = to_cents(amount)
    if from_id == to_id:
        raise BankError("Cannot transfer to the same account.")
    src = _account(conn, from_id)
    _account(conn, to_id)
    if src["balance_cents"] < cents:
        raise BankError(
            f"Insufficient funds: balance is {fmt(src['balance_cents'])}, "
            f"requested {fmt(cents)}."
        )
    if _spent_last_24h(conn, from_id, now) + cents > DAILY_LIMIT_CENTS:
        raise BankError(f"Daily transfer limit of {fmt(DAILY_LIMIT_CENTS)} would be exceeded.")

    conn.execute("DELETE FROM pending_transfers WHERE created_at < ?", (now - PENDING_TTL_SECONDS,))
    token = secrets.token_hex(4)
    conn.execute(
        "INSERT INTO pending_transfers (token, from_id, to_id, amount_cents, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (token, from_id, to_id, cents, now),
    )
    return {
        "status": "pending_confirmation",
        "token": token,
        "summary": f"Transfer {fmt(cents)} from {from_id} to {to_id}",
        "expires_in_seconds": PENDING_TTL_SECONDS,
        "note": "Nothing has been moved yet. Ask the USER to explicitly confirm before calling confirm_transfer.",
    }


def confirm_transfer(conn: sqlite3.Connection, token: str, now: int | None = None) -> dict:
    """Step 2: execute a previously proposed transfer atomically."""
    now = int(time.time()) if now is None else now
    conn.execute("BEGIN IMMEDIATE")
    try:
        pend = conn.execute("SELECT * FROM pending_transfers WHERE token = ?", (token,)).fetchone()
        if pend is None:
            raise BankError("Unknown or already used confirmation token.")
        if now - pend["created_at"] > PENDING_TTL_SECONDS:
            conn.execute("DELETE FROM pending_transfers WHERE token = ?", (token,))
            conn.execute("COMMIT")
            raise BankError("Confirmation token expired. Propose the transfer again.")

        src = _account(conn, pend["from_id"])
        if src["balance_cents"] < pend["amount_cents"]:
            raise BankError("Insufficient funds at confirmation time.")

        conn.execute(
            "UPDATE accounts SET balance_cents = balance_cents - ? WHERE id = ?",
            (pend["amount_cents"], pend["from_id"]),
        )
        conn.execute(
            "UPDATE accounts SET balance_cents = balance_cents + ? WHERE id = ?",
            (pend["amount_cents"], pend["to_id"]),
        )
        cur = conn.execute(
            "INSERT INTO transactions (from_id, to_id, amount_cents, created_at) VALUES (?, ?, ?, ?)",
            (pend["from_id"], pend["to_id"], pend["amount_cents"], now),
        )
        conn.execute("DELETE FROM pending_transfers WHERE token = ?", (token,))
        conn.execute("COMMIT")
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise

    return {
        "status": "completed",
        "transaction_id": cur.lastrowid,
        "from": get_balance(conn, pend["from_id"]),
        "to": get_balance(conn, pend["to_id"]),
    }
