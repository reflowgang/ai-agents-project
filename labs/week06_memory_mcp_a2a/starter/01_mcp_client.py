"""Block 2, first half: call the memory tools over MCP. TODO 6.

    python mcp_server.py &          # in one terminal
    PORT=8001 python mcp_server.py & # and a second instance
    python 01_mcp_client.py

Three things happen here and the third is the one to watch.
"""

from __future__ import annotations

import asyncio

from mcp.client import Client

SUBJECT = "A. Muller"
PRINCIPAL = "desk-agent-1"     # who the case belongs to (homework: authorization)
INTRUDER = "desk-agent-2"


def text_of(result) -> str:
    content = getattr(result, "content", result)
    if isinstance(content, list) and content:
        return getattr(content[0], "text", str(content[0]))
    return str(content)


async def call(port: int, tool: str, **args):
    async with Client(f"http://127.0.0.1:{port}/mcp") as client:
        return await client.call_tool(tool, args)


async def main() -> int:
    """TODO 6. Three steps, and the third has a warning attached.

    1. Connect to :8000 and print the tool catalog. Seeing it arrive over
       HTTP is what makes the boundary real: these tools are not in this
       process.

    2. Call open_case and then remember on :8000. Watch
       artifacts/memory_store.json change while it runs.

    3. Redeem the SAME handle on :8001, the second instance, and recall from
       it.

    The warning: step 3 on its own does not prove the protocol is stateless.
    It would work under the old session-based protocol too, because each
    call opens its own connection and the state is on disk. It shows that a
    file is a file. 02_stateless.py is the demonstration that actually
    distinguishes the two, and it is the next thing you run.
    """
    from memory import DEFAULT_PATH   # artifacts/memory_store.json

    # Step 1. The catalog arrives over HTTP (MCP revision 2026-07-28).
    async with Client("http://127.0.0.1:8000/mcp") as client:
        tools = await client.list_tools()
    print("catalog discovered on :8000:",
          sorted(t.name for t in tools.tools))

    # Step 2. Open a case and write a fact on :8000, watching the file.
    before = DEFAULT_PATH.read_text(encoding="utf-8") if DEFAULT_PATH.exists() else ""
    handle = text_of(await call(8000, "open_case", subject=SUBJECT,
                                principal=PRINCIPAL))
    fact_handle = text_of(await call(
        8000, "remember", subject=SUBJECT, kind="preference",
        fact="prefers letters in French"))
    after = DEFAULT_PATH.read_text(encoding="utf-8") if DEFAULT_PATH.exists() else ""
    print(f"case handle {handle!r}, fact handle {fact_handle!r}")
    print(f"store file changed: {before != after} "
          f"({len(before)} -> {len(after)} characters)")

    # Step 3. Redeem the SAME handle on :8001. This shows that a file is a
    # file; it does NOT prove the protocol is stateless (see 02_stateless.py).
    who = text_of(await call(8001, "case_subject", handle=handle,
                             principal=PRINCIPAL))
    # Homework: the right handle with the wrong principal must NOT resolve,
    # and must look exactly like an unknown handle.
    denied = text_of(await call(8001, "case_subject", handle=handle,
                                principal=INTRUDER))
    unknown = text_of(await call(8001, "case_subject", handle="case-000000000000",
                                 principal=PRINCIPAL))
    recalled = [getattr(c, "text", str(c)) for c in
                (await call(8001, "recall", subject=SUBJECT,
                            query="French letters")).content]
    other = [getattr(c, "text", str(c)) for c in
             (await call(8001, "recall", subject="Somebody Else",
                         query="French letters")).content]
    print(f"on :8001, case_subject -> {who!r}")
    print(f"on :8001, wrong principal -> {denied!r}; unknown handle -> {unknown!r}")
    print(f"on :8001, recall for {SUBJECT!r} -> {recalled}")
    print(f"on :8001, recall for another subject -> {other}")
    # Run this script twice: the second run recalls MORE facts than the first,
    # because the store survived. That is checklist item 1, "different
    # behaviour on the second run".
    print(f"facts recalled for {SUBJECT!r}: {len(recalled)}")
    ok = (before != after and who == SUBJECT and len(recalled) >= 1
          and not other and denied == unknown and denied.startswith("ERROR"))
    print("OK" if ok else "SOMETHING IS WRONG")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
