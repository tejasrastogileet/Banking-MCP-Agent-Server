import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bank_db as db  # noqa: E402


@pytest.fixture
def conn():
    return db.connect(":memory:")


def balance(conn, acc):
    return db.get_balance(conn, acc)["balance"]


def test_seed_accounts(conn):
    assert balance(conn, "ACC-1001") == 25_000


def test_unknown_account(conn):
    with pytest.raises(db.BankError):
        db.get_balance(conn, "NOPE")


def test_propose_does_not_move_money(conn):
    db.propose_transfer(conn, "ACC-1001", "ACC-1002", 500)
    assert balance(conn, "ACC-1001") == 25_000
    assert balance(conn, "ACC-1002") == 10_000


def test_confirm_moves_money_once(conn):
    token = db.propose_transfer(conn, "ACC-1001", "ACC-1002", 500)["token"]
    db.confirm_transfer(conn, token)
    assert balance(conn, "ACC-1001") == 24_500
    assert balance(conn, "ACC-1002") == 10_500
    with pytest.raises(db.BankError):  # token is single use
        db.confirm_transfer(conn, token)


def test_insufficient_funds(conn):
    with pytest.raises(db.BankError, match="Insufficient"):
        db.propose_transfer(conn, "ACC-1003", "ACC-1001", 9_999)


def test_same_account_and_bad_amounts(conn):
    with pytest.raises(db.BankError):
        db.propose_transfer(conn, "ACC-1001", "ACC-1001", 10)
    for bad in (0, -5):
        with pytest.raises(db.BankError):
            db.propose_transfer(conn, "ACC-1001", "ACC-1002", bad)


def test_token_expires(conn):
    token = db.propose_transfer(conn, "ACC-1001", "ACC-1002", 100, now=1_000)["token"]
    with pytest.raises(db.BankError, match="expired"):
        db.confirm_transfer(conn, token, now=1_000 + db.PENDING_TTL_SECONDS + 1)
    assert balance(conn, "ACC-1001") == 25_000


def test_daily_limit(conn):
    conn.execute("UPDATE accounts SET balance_cents = ? WHERE id = 'ACC-1001'", (200_000 * 100,))
    t1 = db.propose_transfer(conn, "ACC-1001", "ACC-1002", 40_000, now=10)["token"]
    db.confirm_transfer(conn, t1, now=10)
    with pytest.raises(db.BankError, match="limit"):
        db.propose_transfer(conn, "ACC-1001", "ACC-1002", 20_000, now=20)


def test_transaction_history(conn):
    token = db.propose_transfer(conn, "ACC-1001", "ACC-1002", 250)["token"]
    db.confirm_transfer(conn, token)
    tx = db.list_transactions(conn, "ACC-1002")["transactions"]
    assert tx[0]["type"] == "credit" and tx[0]["amount"] == 250
