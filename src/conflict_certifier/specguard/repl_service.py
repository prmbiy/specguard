"""Trusted CLI transport to one benchmark-owned, bounded Lean pool.

The Unix socket is mounted outside the agent's bubblewrap filesystem.
Only compilation is exposed; each check starts from the REPL's base environment.
"""
from __future__ import annotations

import json
import os
import queue
import secrets
import socket
import socketserver
import tempfile
import threading
from dataclasses import replace
from pathlib import Path

from conflict_certifier.lean.pool import LeanReplPool
from .artifacts import GuardError

MAX_REQUEST = 8 * 1024 * 1024


class ReplService:
    def __init__(self, size, timeout):
        from .checker import OwnedRepl, lean_environment
        # Preserve the previous benchmark's 8 GiB ceiling per Lean process.
        env = replace(lean_environment(), container_memory=os.environ.get("SPECGUARD_LEAN_MEMORY", "8g"))
        self.pool = LeanReplPool(env, size)
        self.pool._repls = [OwnedRepl(self.pool.env) for _ in range(size)]
        self.timeout = timeout
        self.token = secrets.token_hex(32)
        self.directory = None
        self.server = None
        self.thread = None
        self.stopping = threading.Event()

    def __enter__(self):
        try:
            self.pool.start()
            self.directory = tempfile.TemporaryDirectory(prefix="sg-repl-")
            service = self

            class Handler(socketserver.StreamRequestHandler):
                def handle(self):
                    self.connection.settimeout(30)
                    try:
                        line = self.rfile.readline(MAX_REQUEST + 1)
                        if len(line) > MAX_REQUEST:
                            raise GuardError("Lean request too large")
                        request = json.loads(line)
                        if not secrets.compare_digest(request.get("token", ""), service.token):
                            raise GuardError("Unauthorized Lean client")
                        if request.get("op") == "ping":
                            result = [True, "shared Lean pool"]
                        elif request.get("op") == "compile":
                            timeout = min(service.timeout, max(1, int(request["timeout"])))
                            # Bounded wait; the enclosing CLI also has a total deadline.
                            repl = None
                            while not service.stopping.is_set():
                                try:
                                    repl = service.pool._idle.get(timeout=0.2)
                                    break
                                except queue.Empty:
                                    # Do not compile abandoned requests after a task deadline.
                                    import select
                                    if select.select([self.connection], [], [], 0)[0]:
                                        if not self.connection.recv(1, socket.MSG_PEEK):
                                            return
                            if repl is None:
                                result = [False, "Lean pool shutting down"]
                            else:
                                try:
                                    result = repl.compile(request["source"], timeout=timeout, reject_sorry=True)
                                finally:
                                    service.pool._idle.put(repl)
                        else:
                            raise GuardError("Unknown Lean operation")
                        response = {"result": result}
                    except Exception as exc:
                        response = {"error": str(exc)}
                    try:
                        self.wfile.write(json.dumps(response).encode() + b"\n")
                    except (BrokenPipeError, ConnectionResetError):
                        pass  # The task's overall deadline may have expired.

            class Server(socketserver.ThreadingUnixStreamServer):
                daemon_threads = False
                request_queue_size = 128

            self.server = Server(str(Path(self.directory.name) / "repl.sock"), Handler)
            self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
            self.thread.start()
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_):
        self.stopping.set()
        if self.server:
            if self.thread:
                self.server.shutdown()
            self.server.server_close()
        self.pool.close()
        if self.directory:
            self.directory.cleanup()

    @property
    def mount(self):
        return f"{self.directory.name}:/opt/specguard-pool:ro"

    @property
    def client_env(self):
        return {"SPECGUARD_REPL_SOCKET": "/opt/specguard-pool/repl.sock",
                "SPECGUARD_REPL_TOKEN": self.token}


class RemoteRepl:
    def request(self, op, **kwargs):
        payload = json.dumps({"op": op, "token": os.environ["SPECGUARD_REPL_TOKEN"], **kwargs}).encode() + b"\n"
        if len(payload) > MAX_REQUEST:
            raise GuardError("Lean request too large")
        with socket.socket(socket.AF_UNIX) as connection:
            connection.settimeout(3600)
            connection.connect(os.environ["SPECGUARD_REPL_SOCKET"])
            connection.sendall(payload)
            with connection.makefile("rb") as stream:
                response = json.loads(stream.readline())
        if "error" in response:
            raise GuardError(response["error"])
        return tuple(response["result"])

    def start(self):
        self.request("ping")

    def compile(self, source, *, timeout=None, reject_sorry=True):
        return self.request("compile", source=source, timeout=timeout or 120)

    def close(self):
        pass  # Only the benchmark owns the pool.
