"""The memory store, exposed over MCP. TODO 4 and 5.

    python mcp_server.py            # serves on 8000
    PORT=8001 python mcp_server.py  # a second instance, same store

Targets MCP specification revision 2026-07-28. Every place this file relies
on that revision carries the date in a comment, because the protocol runs a
twelve month deprecation policy and a file without a date is a file nobody
can safely re-check.
"""

from __future__ import annotations

import asyncio
import os

from memory import MemoryStore

try:
    from mcp.server import MCPServer   # revision 2026-07-28. SDK v1 had FastMCP.
except ImportError as exc:             # noqa: BLE001
    raise SystemExit(
        "Could not import MCPServer from mcp.server.\n"
        "The stable v1 line has no MCPServer class, so an unpinned install\n"
        "resolves to something that cannot run this file. Install the pin:\n"
        "    pip install --pre 'mcp==2.0.0rc1'\n"
    ) from exc


app = MCPServer("memory-desk")


# --------------------------------------------------------------------------
# TODO 4. The memory tools, over the wire.
# --------------------------------------------------------------------------
#
# These are the same three methods you wrote in block 1. What is new is that
# the description is now an interface contract for a model that is not in
# this process, which is the same lesson as week 4's tool schemas one layer
# further out.
#
# The store is loaded per call rather than held in a module global. That
# looks wasteful and it is the point: two server instances share one file, so
# each call reads the current state rather than a copy it cached at startup.
# A server that caches the store in memory is a server with session state
# hiding inside it, whatever the protocol says.

# TODO 4. Wrap the three memory methods as MCP tools.
#
#   remember(subject, kind, fact) -> handle
#   recall(subject, query, k=3)   -> list of facts
#   forget(handle)                -> bool
#
# Two things that are not obvious.
#
# The docstring becomes the tool description that a model in another process
# reads, so it is prompt engineering rather than documentation. Say what the
# tool returns, when NOT to use it, and what an empty result means, exactly
# as in week 4.
#
# Load the store per call rather than holding it in a module global. That
# looks wasteful and it is the point: two instances share one file, so each
# call must read the current state. A server that caches the store at startup
# has session state hiding inside it, whatever the protocol says.


# --------------------------------------------------------------------------
# TODO 5. The handle, which is the week's central move.
# --------------------------------------------------------------------------
#
# open_case mints a name for a piece of ongoing state, and every later call
# takes that name as an ordinary argument.
#
# That is the lecture's claim made executable. The protocol carries no
# session, so the state cannot hide in the transport. It has to be somewhere
# the request can point at, and the request is written by the model, so the
# state becomes something the model can see and reason about.
#
# Compare the two shapes:
#
#   session-based   the server remembers who you are between calls
#   stateless       you say who you are, every time, in the call
#
# The second is more typing and it is the one that scales, because any
# request can land on any instance. 02_stateless.py measures that at the
# wire.

# TODO 5. open_case(subject) -> handle, and case_subject(handle) -> subject.
#
# The handle is the state. Later calls pass it as an ordinary argument
# instead of the server remembering who you are.
#
# Write the two tools, then answer this in DECISIONS.md: what would the
# server have had to hold between calls if there were no handle, and what
# would that cost you when a second instance appears behind a load balancer?


# --- TODO 4: the memory tools. MCP revision 2026-07-28 (SDK 2.0.0rc1). ---
# The store is loaded inside each call and never held in a module global, so
# two instances sharing artifacts/memory_store.json always see current state.

MAX_K = 10      # a cap enforced in code: the model cannot ask for 400 facts


@app.tool()
def remember(subject: str, kind: str, fact: str) -> str:
    """Store ONE lasting fact about a resident and return its handle.

    subject: the resident's name exactly as used elsewhere, for example
             "A. Muller". kind: a short label such as "preference" or
             "address". fact: one plain sentence.
    Returns the fact's handle. Keep it: forget needs it.
    Use for things worth knowing next time. Do NOT use for chit-chat or for
    anything the resident has not actually said.
    """
    store = MemoryStore.load()
    return store.write(subject, kind, fact)


@app.tool()
def recall(subject: str, query: str, k: int = 3) -> list[str]:
    """Look up remembered facts about ONE resident, newest first on a tie.

    subject: the resident's name. query: a few keywords about what you need.
    k: how many facts at most (default 3, capped at 10).
    Returns a list of facts, each with its recording date. An EMPTY list
    means nothing relevant is remembered for this subject; it does not mean
    the resident has no history. Do NOT use it for facts about someone else.
    """
    store = MemoryStore.load()
    return store.search(subject, query, k=max(1, min(k, MAX_K)))


@app.tool()
def forget(handle: str) -> bool:
    """Permanently delete one remembered fact by its handle.

    Returns True if it existed, False if the handle matched nothing. This
    cannot be undone. It does not remove the fact from anything already said
    in a conversation.
    """
    store = MemoryStore.load()
    return store.forget(handle)


# --- TODO 5: the handle. The protocol carries no session (2026-07-28), so
# the state has a name the model can see and pass back as an argument. ---

@app.tool()
def open_case(subject: str, principal: str) -> str:
    """Open a case for a resident and return its handle (case-...).

    subject: the resident the case is about. principal: who you are acting
    for, for example "desk-agent-1". Only this same principal can read the
    case back, so use the same value on every later call.
    Call this once at the start of a conversation about a resident. Do NOT
    open a second case for the same conversation.
    """
    store = MemoryStore.load()
    return store.open_case(subject, principal)


@app.tool()
def case_subject(handle: str, principal: str) -> str:
    """Return the resident a case handle belongs to.

    principal: the same value you gave to open_case. Returns
    "ERROR: no such case" both when the handle is unknown and when it
    belongs to a different principal; the two are deliberately
    indistinguishable. Do not guess a subject after an error.
    """
    store = MemoryStore.load()
    subject = store.subject_of(handle, principal)
    return subject if subject is not None else "ERROR: no such case"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    print(f"MCP revision 2026-07-28, streamable HTTP, stateless")
    print(f"http://127.0.0.1:{port}/mcp")
    # stateless_http=True is the 2026-07-28 core: no initialize handshake,
    # no Mcp-Session-Id, every request self-describing. Leaving it off puts
    # the server back on the old session-based model, and 02_stateless.py
    # shows you the difference at the wire.
    asyncio.run(app.run_streamable_http_async(
        host="127.0.0.1", port=port, stateless_http=True))
