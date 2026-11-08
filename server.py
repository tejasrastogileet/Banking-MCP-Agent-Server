"""MCP server exposing a small banking ledger as tools.

Run standalone to inspect it:  npx @modelcontextprotocol/inspector python server.py
The agent (agent.py) starts this file itself over stdio.

NOTE: with the stdio transport, never print() to stdout here - it would corrupt
the MCP protocol stream. Use logging (stderr) if you need debug output.
"""
from mcp.server.fastmcp import FastMCP

import bank_db as db

mcp = FastMCP("bank-mcp")
_conn = db.connect()


def _run(fn, *args, **kwargs) -> dict:
    """Call a ledger function; turn business-rule errors into readable results."""
    try:
        return fn(_conn, *args, **kwargs)
    except db.BankError as exc:
        return {"error": str(exc)}


@mcp.tool()
def get_balance(account_id: str) -> dict:
    """Get the current balance and owner of an account (e.g. 'ACC-1001')."""
    return _run(db.get_balance, account_id)


@mcp.tool()
def list_transactions(account_id: str, limit: int = 5) -> dict:
    """List the most recent transactions (debits and credits) for an account."""
    return _run(db.list_transactions, account_id, limit)


@mcp.tool()
def propose_transfer(from_account: str, to_account: str, amount: float) -> dict:
    """Step 1 of a transfer: validate it and get a confirmation token.

    This does NOT move any money. After calling it, show the summary to the user
    and wait for their explicit confirmation before calling confirm_transfer.
    """
    return _run(db.propose_transfer, from_account, to_account, amount)


@mcp.tool()
def confirm_transfer(token: str) -> dict:
    """Step 2 of a transfer: execute a proposed transfer using its token.

    Only call this after the USER has explicitly said yes to the exact summary
    returned by propose_transfer.
    """
    return _run(db.confirm_transfer, token)


if __name__ == "__main__":
    mcp.run(transport="stdio")
