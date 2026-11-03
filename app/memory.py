"""The memory store, and the handle. TODO 1 to 3.

Two things live here and they are easy to confuse.

**Memory** is what the agent remembers about a resident across runs: facts,
written once and searched later. It is a store.

**A handle** is a name for a piece of state that the agent carries in its own
messages. It is not memory, it is an address. `open_case` mints one, and
every later call passes it back as an ordinary argument.

The distinction is the whole week. Block 2 shows why: when the protocol stops
carrying state for you, the state does not vanish, it becomes an argument
with a name the model can see.
"""

from __future__ import annotations

import hmac
import json
import re
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PATH = Path("artifacts/memory_store.json")


def _norm(text: str) -> str:
    return text.casefold().strip()


def _words(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.casefold()))


@dataclass
class Fact:
    subject: str
    kind: str
    text: str
    handle: str
    written_at: str


@dataclass
class MemoryStore:
    """A file-backed store. Deliberately not a database and not a vector store.

    Week 7 replaces `search` with real retrieval over embeddings. Keep the
    keyword version behind this one method and next week costs you ten lines
    instead of a refactor.
    """

    path: Path = DEFAULT_PATH
    facts: list[Fact] = field(default_factory=list)
    # handle -> {"subject": ..., "owner": ...}. The owner is who opened the
    # case (homework, 2026-10-21); see open_case and subject_of.
    cases: dict[str, dict] = field(default_factory=dict)

    # ----------------------------------------------------------------
    # TODO 1. Writing, and the handle it returns.
    # ----------------------------------------------------------------

    def write(self, subject: str, kind: str, text: str) -> str:
        """TODO 1. Append one fact and return a handle for it.

        Three decisions here, and all three go in DECISIONS.md with a reason.

        What does a handle look like? A sequential `fact-0007` tells anyone
        who sees it how many facts exist and lets them guess `fact-0006`,
        which is a read of somebody else's data. Week 12 attacks exactly
        this.

        What happens when a new fact contradicts an older one? Keeping both
        means `recall` can return two contradictory facts and something has
        to cope. Overwriting means losing what changed and when. Pick one.

        How long does a record live? Nothing here expires, which is a choice
        and the wrong one for a real service holding residents' data. If you
        keep it, say so rather than leaving it unsaid.
        """
        # Handle: random, so it reveals nothing about how many facts exist and
        # cannot be guessed by counting. 6 random bytes = 48 bits.
        handle = f"fact-{secrets.token_hex(6)}"
        # Conflict policy: keep both. A contradicting fact is appended, not
        # swapped in; `search` puts the newer one first and shows the date.
        self.facts.append(Fact(
            subject=subject, kind=kind, text=text, handle=handle,
            written_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))
        self.save()
        return handle

    # ----------------------------------------------------------------
    # TODO 2. Searching, and what k costs.
    # ----------------------------------------------------------------

    def search(self, subject: str, query: str, k: int = 3) -> list[str]:
        """Facts about this resident first, then passages from the corpus.

        One method, one signature since week 6. Week 7 put the corpus behind
        it: the keyword match over the resident's facts stays in
        `_search_facts`, the dense retrieval over the commune documents is in
        `_search_corpus`, and the facts that match come first.
        """
        k = max(0, k)
        facts = self._search_facts(subject, query, k)
        room = k - len(facts)
        return facts + (self._search_corpus(query, room) if room > 0 else [])

    def _search_facts(self, subject: str, query: str, k: int) -> list[str]:
        """Week 6: keyword overlap over ONE subject's facts, nothing else.

        The week 6 v2 fallback (the newest facts when no word matches) is not
        here. With a corpus behind this method it filled the slots with facts
        unrelated to the question and left no room for retrieved passages.
        """
        wanted = _words(query)
        scored = []
        for position, fact in enumerate(self.facts):
            if _norm(fact.subject) != _norm(subject):
                continue          # the boundary: other subjects are never scored
            overlap = len(wanted & _words(f"{fact.kind} {fact.text}"))
            if overlap:
                scored.append((overlap, fact.written_at, position, fact))
        scored.sort(key=lambda row: row[:3], reverse=True)
        return [f"{f.text} (recorded {f.written_at[:10]})"
                for _, _, _, f in scored[:k]]

    _index = None        # built once per process, on the first corpus search

    def _search_corpus(self, query: str, k: int) -> list[str]:
        """Week 7: dense retrieval over the commune corpus (600/80, Chroma).

        The first call in a process builds the index: about 2 to 5 seconds
        with the embedding model loaded, longer from cold.
        """
        from .chunking import load_chunks
        from .retrieval import DenseIndex
        if MemoryStore._index is None:
            MemoryStore._index = DenseIndex(load_chunks(600, 80), name="app")
        return [f"[{c.doc_id} / {c.section}] {c.text}"
                for c in MemoryStore._index.search(query, k=k)]

    # ----------------------------------------------------------------
    # TODO 3. Forgetting, which is not optional.
    # ----------------------------------------------------------------

    def forget(self, handle: str) -> bool:
        """TODO 3. Hard delete by handle. Return whether it existed.

        Not optional. A store that can only be written to is not a store you
        can operate, and "we keep everything forever" is an answer with a
        legal department attached to it.

        One line in DECISIONS.md about what this does NOT reach: anything
        already copied into a running conversation's messages. Deleting the
        fact does not un-say it.
        """
        before = len(self.facts)
        self.facts = [f for f in self.facts if f.handle != handle]
        existed = len(self.facts) < before
        if existed:
            self.save()
        return existed

    # ----------------------------------------------------------------
    # The handle, which is block 2's subject
    # ----------------------------------------------------------------

    def open_case(self, subject: str, principal: str) -> str:
        """Mint an explicit handle for a piece of ongoing state.

        Authorization (homework). The case is owned by `principal`, the
        identity of whoever opened it, and only that principal can read it
        back. Holding the handle is no longer enough.

        What this is NOT: authentication. `principal` is a string the caller
        supplies, and over plain MCP nothing proves the caller is who it
        says. This closes "anyone who sees the handle can read the case"; it
        does not close "anyone who also guesses the principal name can".
        Verifying the principal (a credential checked by the server, not a
        value in the request) is the part left for week 12.
        """
        handle = f"case-{secrets.token_hex(6)}"
        self.cases[handle] = {"subject": subject, "owner": principal}
        self.save()
        return handle

    def subject_of(self, handle: str, principal: str) -> str | None:
        """The case's subject, or None. None means ONE thing to the caller.

        An unknown handle and a handle owned by somebody else both return
        None, so the caller cannot tell them apart. If they differed, the
        difference would let anyone test which handles exist.
        """
        case = self.cases.get(handle)
        if case is None or case.get("owner") is None:
            return None                  # unknown, or a pre-authorization case
        if not hmac.compare_digest(case["owner"].encode(), principal.encode()):
            return None
        return case["subject"]

    # ----------------------------------------------------------------
    # Persistence
    # ----------------------------------------------------------------

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({
            "facts": [vars(f) for f in self.facts],
            "cases": self.cases,
        }, indent=2, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load(cls, path: Path = DEFAULT_PATH) -> "MemoryStore":
        store = cls(path=path)
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            store.facts = [Fact(**f) for f in data.get("facts", [])]
            # Cases written before the authorization change are plain
            # handle -> subject strings. They have no owner, so nobody can
            # read them (deny by default) rather than everybody.
            store.cases = {h: (c if isinstance(c, dict)
                               else {"subject": c, "owner": None})
                           for h, c in data.get("cases", {}).items()}
        return store
