# Bank MCP Agent

An AI agent built with **LangGraph + Groq** that talks to a banking ledger through the
**Model Context Protocol (MCP)**.

```
You -> LangGraph ReAct agent (Groq LLM) -> MCP client -> MCP server (FastMCP, stdio) -> SQLite ledger
```

## Features
- MCP server exposing 4 tools: `get_balance`, `list_transactions`, `propose_transfer`, `confirm_transfer`
- **Two-step transfers:** money only moves after a single-use, expiring confirmation token
- Business rules in plain Python (`bank_db.py`): balance checks, 24h daily limit, atomic transactions
- Unit tests for the ledger (no API key or network needed)

## Setup
```bash
python -m venv .venv
.venv\Scripts\activate          # Windows  (Linux/Mac: source .venv/bin/activate)
pip install -r requirements.txt
copy .env.example .env          # then put your real GROQ_API_KEY inside .env
```

## Run
```bash
python agent.py                 # chat with the agent
pytest                          # run ledger tests
npx @modelcontextprotocol/inspector python server.py   # inspect the MCP tools
```

Try: *"What's the balance of ACC-1001?"*, then *"Send 500 from ACC-1001 to ACC-1002"*, then *"yes"*.

## Known limitations / roadmap
- The "ask the user before confirming" rule is currently enforced by the system prompt plus the
  token step. Next: a real human-in-the-loop approval using LangGraph `interrupt`.

