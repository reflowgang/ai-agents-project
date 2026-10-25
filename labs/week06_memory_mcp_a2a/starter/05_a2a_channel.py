"""Homework: an A2A-STYLE channel built from scratch (week 6, 2026-10-21).

    python 05_a2a_channel.py

Standard library only. No model, no key, no network beyond localhost.
03_a2a.py was a demonstration. This is the thing built: a remote agent that
writes a booking-confirmation letter, and a client that delegates to it.

What 03_a2a.py only SAID, this file does, and checks:

  * a task is a RECORD with an id and a state, kept on the server, not a
    return value. The client submits, then polls.
  * after a timeout the client keeps the id and gets the result later.
    03 claims "the work may still be running"; here it is shown running.
  * submitting the same id twice does the work once (idempotent), so a
    client that is unsure whether its request arrived can safely resend.
  * a task can be cancelled, and a late result does not overwrite the
    cancelled state.
  * "not delegated" is proven by the server's request counter, not by a
    print statement.

This is A2A-STYLE, not a certified A2A implementation. The names (agent
card, task, state, artifact) follow the vocabulary; the wire format is mine.

The letters are templates, not model output. That is deliberate: the point
here is the channel, and a template cannot invent a word ("rappatriement"
instead of "rabais" was a real memory-on reply in 04_memory_ab.py). It also
means nothing here says anything about how a model behaves behind a channel.

Test hooks: a request may carry "simulate": {"seconds": n} to make the work
take n seconds, or {"artifact": "data"} to return a non-text artifact. A real
agent would not accept those from the caller; they exist so the failure paths
can be exercised on demand.
"""

from __future__ import annotations

import json
import multiprocessing as mp
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8972
BASE = f"http://127.0.0.1:{PORT}"
TERMINAL = {"completed", "failed", "canceled"}

CARD = {
    "name": "confirmation-letter-agent",
    "description": "Writes a short booking-confirmation letter.",
    "url": BASE,
    "version": "1.0",
    "capabilities": {"polling": True, "cancel": True, "idempotent_submit": True},
    "skills": [{
        "id": "confirmation_letter",
        "name": "Booking confirmation letter",
        "description": "Input data: resident, hall, when, discount_percent, "
                       "language (fr or en). Output: one text part.",
        "inputModes": ["application/json"],
        "outputModes": ["text/plain"],
    }],
}

LETTERS = {
    "fr": ("Madame, Monsieur {resident}, votre reservation de la salle {hall} "
           "le {when} est confirmee. Vous beneficiez d'une reduction de "
           "{discount_percent} %."),
    "en": ("Dear {resident}, your booking of {hall} on {when} is confirmed. "
           "You get a discount of {discount_percent} %."),
}

# --------------------------------------------------------------------------
# The remote agent
# --------------------------------------------------------------------------

TASKS: dict[str, dict] = {}
LOCK = threading.Lock()
STATS = {"requests": 0, "executions": 0}      # what the client cannot see


def _set_terminal(task_id: str, state: str, **fields) -> None:
    """Move a task to a terminal state, unless it already is in one.

    This is why a cancelled task stays cancelled when the slow work finishes
    afterwards: the late result finds a terminal state and is dropped.
    """
    with LOCK:
        task = TASKS[task_id]
        if task["state"] in TERMINAL:
            return
        task["state"] = state
        task["history"].append(state)
        task.update(fields)


def work(task_id: str, data: dict, simulate: dict) -> None:
    with LOCK:
        STATS["executions"] += 1
        TASKS[task_id]["state"] = "working"
        TASKS[task_id]["history"].append("working")
    deadline = time.monotonic() + float(simulate.get("seconds", 0))
    while time.monotonic() < deadline:            # sleeping in small steps
        time.sleep(0.05)
    template = LETTERS.get(data.get("language"))
    if template is None:
        _set_terminal(task_id, "failed",
                      error=f"language {data.get('language')!r} is not "
                            f"supported (fr, en)")
        return
    if simulate.get("artifact") == "data":
        _set_terminal(task_id, "completed",
                      artifact={"parts": [{"data": {"letter": "see attachment"}}]})
        return
    try:
        text = template.format(**data)
    except KeyError as missing:
        _set_terminal(task_id, "failed", error=f"missing input field {missing}")
        return
    _set_terminal(task_id, "completed", artifact={"parts": [{"text": text}]})


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return {}

    def do_GET(self):
        with LOCK:
            STATS["requests"] += 1
        if self.path == "/.well-known/agent.json":
            self._send(200, CARD)
        elif self.path == "/stats":               # demonstration only
            with LOCK:
                self._send(200, dict(STATS))
        elif self.path.startswith("/tasks/"):
            task_id = self.path.split("/")[2]
            with LOCK:
                task = TASKS.get(task_id)
                snapshot = json.loads(json.dumps(task)) if task else None
            if snapshot is None:
                self._send(404, {"error": "unknown task", "taskId": task_id})
            else:
                self._send(200, snapshot)
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        with LOCK:
            STATS["requests"] += 1
        if self.path == "/tasks":
            body = self._body()
            task_id, skill = body.get("taskId"), body.get("skill")
            if not task_id:
                self._send(400, {"error": "taskId is required"})
                return
            if skill not in {s["id"] for s in CARD["skills"]}:
                self._send(400, {"error": f"skill {skill!r} is not declared "
                                          f"on the card"})
                return
            with LOCK:
                existing = TASKS.get(task_id)
                if existing is None:              # first time: create it
                    TASKS[task_id] = {"taskId": task_id, "state": "submitted",
                                      "history": ["submitted"]}
                state = TASKS[task_id]["state"]
            if existing is None:
                data = body.get("message", {}).get("parts", [{}])[0].get("data", {})
                threading.Thread(
                    target=work, args=(task_id, data, body.get("simulate", {})),
                    daemon=True).start()
            # A repeated submit returns the task as it stands and does NOT
            # start the work again.
            self._send(200, {"taskId": task_id, "state": state,
                             "duplicate": existing is not None})
        elif self.path.startswith("/tasks/") and self.path.endswith("/cancel"):
            task_id = self.path.split("/")[2]
            with LOCK:
                known = task_id in TASKS
            if not known:
                self._send(404, {"error": "unknown task", "taskId": task_id})
                return
            _set_terminal(task_id, "canceled")
            with LOCK:
                self._send(200, {"taskId": task_id,
                                 "state": TASKS[task_id]["state"]})
        else:
            self._send(404, {"error": "not found"})


def serve() -> None:
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


# --------------------------------------------------------------------------
# The client
# --------------------------------------------------------------------------

class Timeout(Exception):
    """The wait ran out. The task id is carried, not lost."""

    def __init__(self, task_id: str):
        super().__init__(f"timed out waiting for {task_id}")
        self.task_id = task_id


def call(method: str, path: str, body: dict | None = None,
         timeout: float = 3.0) -> tuple[int, dict]:
    request = urllib.request.Request(
        BASE + path, method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read() or b"{}")


def submit(task_id: str, data: dict, simulate: dict | None = None) -> dict:
    body = {"taskId": task_id, "skill": "confirmation_letter",
            "message": {"parts": [{"data": data}]}}
    if simulate:
        body["simulate"] = simulate
    return call("POST", "/tasks", body)[1]


def wait(task_id: str, within: float) -> dict:
    """Poll until the task is terminal. Raise Timeout, with the id, if not."""
    end = time.monotonic() + within
    while time.monotonic() < end:
        _, task = call("GET", f"/tasks/{task_id}")
        if task.get("state") in TERMINAL:
            return task
        time.sleep(0.15)
    raise Timeout(task_id)


def text_of(task: dict) -> str | None:
    for part in task.get("artifact", {}).get("parts", []):
        if "text" in part:
            return part["text"]
    return None


INPUT = {"resident": "A. Muller", "hall": "main hall",
         "when": "Saturday 20:00", "discount_percent": 15, "language": "fr"}


def main() -> int:
    server = mp.Process(target=serve, daemon=True)
    server.start()
    time.sleep(1.2)
    checks: list[tuple[str, bool]] = []

    def check(name: str, ok: bool) -> None:
        checks.append((name, ok))
        print(f"   [{'ok' if ok else 'FAIL'}] {name}")

    try:
        # 1. Discovery, then delegation because the card declares the skill.
        _, card = call("GET", "/.well-known/agent.json")
        skills = [s["id"] for s in card["skills"]]
        print(f"card: {card['name']}, skills {skills}, "
              f"capabilities {card['capabilities']}")

        print("\n1 delegated and completed")
        submit("t1", INPUT)
        task = wait("t1", within=3)
        print(f"   history {task['history']}\n   letter  {text_of(task)}")
        check("state history is submitted, working, completed",
              task["history"] == ["submitted", "working", "completed"])
        check("artifact has a text part", text_of(task) is not None)

        # 2. Not delegated. Proven by the server's own counter.
        print("\n2 not delegated")
        before = call("GET", "/stats")[1]["requests"]
        wanted = "summarize"
        if wanted not in skills:
            print(f"   card declares no {wanted!r}: handled locally, "
                  f"no task request sent")
        after = call("GET", "/stats")[1]["requests"]
        check("server saw no task request (the only new request is the second /stats read)",
              after - before == 1)
        code, body = call("POST", "/tasks", {"taskId": "t2", "skill": wanted})
        print(f"   (forcing it anyway) -> {code} {body}")
        check("server itself refuses an undeclared skill", code == 400)

        # 3. Failure is a state, not an exception.
        print("\n3 failed")
        submit("t3", {**INPUT, "language": "de"})
        task = wait("t3", within=3)
        print(f"   state {task['state']}, error {task.get('error')!r}")
        check("failed state with a reason", task["state"] == "failed"
              and "not supported" in task.get("error", ""))

        # 4. Timeout, keep the id, collect later, and a safe resend.
        print("\n4 timed out, id kept, result collected later")
        submit("t4", INPUT, simulate={"seconds": 2.0})
        kept = None
        try:
            wait("t4", within=0.6)
        except Timeout as exc:
            kept = exc.task_id
            print(f"   gave up waiting after 0.6s, kept id {kept!r}")
        check("timeout raised and carried the task id", kept == "t4")
        executions_before = call("GET", "/stats")[1]["executions"]
        resend = submit("t4", INPUT, simulate={"seconds": 2.0})
        print(f"   resent the same id -> {resend}")
        task = wait(kept, within=4)
        print(f"   later, same id -> {task['state']}: {text_of(task)}")
        check("the kept id still returns the finished result",
              task["state"] == "completed" and text_of(task) is not None)
        check("resending the id did not start the work twice",
              resend.get("duplicate") is True
              and call("GET", "/stats")[1]["executions"] == executions_before)

        # 5. Cancel, and a late result does not undo it.
        print("\n5 cancelled")
        submit("t5", INPUT, simulate={"seconds": 1.0})
        time.sleep(0.2)
        cancelled = call("POST", "/tasks/t5/cancel")[1]
        time.sleep(1.4)                       # the slow work has now finished
        task = call("GET", "/tasks/t5")[1]
        print(f"   cancel -> {cancelled['state']}, after the work ended -> "
              f"{task['state']}, artifact {task.get('artifact')}")
        check("cancelled stays cancelled and gets no artifact",
              cancelled["state"] == "canceled" and task["state"] == "canceled"
              and "artifact" not in task)

        # 6. An artifact with no text part is reported, not stringified.
        print("\n6 an artifact whose parts are not text")
        submit("t6", INPUT, simulate={"artifact": "data"})
        task = wait("t6", within=3)
        letter = text_of(task)
        if letter is None:
            print(f"   artifact has no text part: {task['artifact']['parts']}")
            print("   reported as a mismatch, not shown to a resident")
        check("no text part is detected, not coerced to a string",
              letter is None and task["state"] == "completed")

        # 7. A task id nobody created.
        print("\n7 unknown task id")
        code, body = call("GET", "/tasks/never-created")
        print(f"   -> {code} {body}")
        check("404 for an unknown id", code == 404)
    finally:
        server.terminate()

    failed = [name for name, ok in checks if not ok]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    for name in failed:
        print(f"FAILED: {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    mp.set_start_method("spawn")
    raise SystemExit(main())
