"""LangGraph ReAct agent (Groq LLM) that uses the bank MCP server's tools."""
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from langchain_groq import ChatGroq
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import create_react_agent

load_dotenv()

SYSTEM_PROMPT = """You are a careful banking assistant. You can only act through the tools provided.

Rules:
- Never guess account IDs, balances or amounts. Use tools, or ask the user.
- To move money: first call propose_transfer, then show the user the summary and ask
  "Do you want me to confirm this? (yes/no)". Only call confirm_transfer AFTER the user
  replies yes in a later message. Never propose and confirm in the same turn.
- If a tool returns an "error", explain it plainly and suggest a fix.
- Keep answers short."""


async def main() -> None:
    if not os.getenv("GROQ_API_KEY"):
        sys.exit("GROQ_API_KEY is missing. Copy .env.example to .env and add your key.")

    client = MultiServerMCPClient(
        {
            "bank": {
                "command": sys.executable,
                "args": [str(Path(__file__).with_name("server.py"))],
                "transport": "stdio",
            }
        }
    )
    tools = await client.get_tools()
    print("Loaded tools:", ", ".join(t.name for t in tools))

    llm = ChatGroq(model=os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"), temperature=0)
    agent = create_react_agent(llm, tools, prompt=SYSTEM_PROMPT, checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "cli-session"}}

    print("Bank agent ready. Type 'q' to quit.\n")
    while True:
        question = input("You: ").strip()
        if question.lower() in {"q", "quit", "exit"}:
            break
        if not question:
            continue
        result = await agent.ainvoke({"messages": [{"role": "user", "content": question}]}, config)
        print("Agent:", result["messages"][-1].content, "\n")


if __name__ == "__main__":
    asyncio.run(main())
