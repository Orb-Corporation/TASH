"""
Worker-side networking: connects to a Master, requests authorization
(gated behind the local operator's explicit consent plus a pairing
code), executes only tasks found in tasks.base_task.TASK_REGISTRY,
sends heartbeats, and reconnects automatically after a dropped
connection.

Security notes:
  - `_user_authorized` is a local, operator-controlled gate. No task
    is ever executed unless the person sitting at this machine clicked
    "Authorize" in the GUI.
  - `_handle_task_assign` calls `tasks.base_task.run_task`, which can
    only ever invoke a function that was registered at import time in
    this codebase. There is no eval/exec/subprocess-of-network-data
    anywhere in this file.
"""
import asyncio
import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

from shared.constants import (
    HEARTBEAT_INTERVAL_SEC, PROTOCOL_VERSION, RECONNECT_BACKOFF_MAX, RECONNECT_BACKOFF_START,
)
from shared.protocol import Message, MessageType, read_message, write_message
from shared.system_info import get_full_snapshot
from tasks.base_task import run_task
import tasks.cpu_benchmark_task  # noqa: F401  (import registers the "cpu_benchmark" task)

logger = logging.getLogger("worker.core")


class WorkerState:
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    AWAITING_CONSENT = "awaiting_consent"
    CONNECTED = "connected"
    COMPUTING = "computing"
    PAUSED = "paused"


class WorkerClient:
    def __init__(self, worker_name: str):
        self.worker_id = uuid.uuid4().hex[:12]
        self.worker_name = worker_name
        self.state = WorkerState.DISCONNECTED
        self.master_host: Optional[str] = None
        self.master_port: Optional[int] = None
        self.token: Optional[str] = None
        self.cpu_limit_percent = 100  # 1-100, throttles via a sleep between item batches
        self.on_event: Optional[Callable[[str, dict], None]] = None

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._should_run = False
        self._paused = False
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._is_reconnect = False
        self._pending_pairing_code: Optional[str] = None
        self._current_task_cancel: Optional[threading.Event] = None
        self._active_task: Optional[asyncio.Task] = None

        # Local, operator-controlled consent gate. No task is ever
        # executed and no data is sent to a Master unless this is True.
        self._user_authorized = False

    # ------------------------------------------------------------- lifecycle

    def start(self, host: str, port: int, pairing_code: str):
        self.master_host, self.master_port = host, port
        self._pending_pairing_code = pairing_code
        self._should_run = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._should_run = False
        if self._current_task_cancel is not None:
            self._current_task_cancel.set()
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread and self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(timeout=2)

    def disconnect_gracefully(self):
        """Send DISCONNECT before stopping the event loop, when possible."""
        if self._loop and self._writer:
            async def _send():
                try:
                    await write_message(self._writer, Message(MessageType.DISCONNECT, {}))
                except Exception:
                    pass
                finally:
                    self._writer.close()
            try:
                future = asyncio.run_coroutine_threadsafe(_send(), self._loop)
                future.result(timeout=1.0)
            except Exception:
                pass
        self.stop()

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.create_task(self._connection_loop())
        self._loop.run_forever()

    def set_user_authorized(self, value: bool):
        """The local human operator clicking 'Authorize this Master' --
        required before any task the Master sends will ever be executed."""
        self._user_authorized = value

    def set_cpu_limit(self, percent: int):
        self.cpu_limit_percent = max(1, min(100, percent))

    def set_paused(self, paused: bool):
        self._paused = paused
        self._emit("paused" if paused else "resumed", {})
        if self._loop and self._writer and self.state in (
                WorkerState.CONNECTED, WorkerState.COMPUTING, WorkerState.PAUSED):
            message_type = MessageType.PAUSE if paused else MessageType.RESUME
            asyncio.run_coroutine_threadsafe(
                write_message(self._writer, Message(message_type, {})), self._loop)

    def _emit(self, event: str, data: dict):
        if self.on_event:
            try:
                self.on_event(event, data)
            except Exception:
                logger.exception("GUI callback raised")

    # ------------------------------------------------------------- networking

    async def _connection_loop(self):
        backoff = RECONNECT_BACKOFF_START
        while self._should_run:
            try:
                self._emit("connecting", {"host": self.master_host, "port": self.master_port})
                reader, writer = await asyncio.open_connection(self.master_host, self.master_port)
                self._writer = writer
                await self._register_and_run(reader, writer)
                backoff = RECONNECT_BACKOFF_START  # reset after any clean session
            except (ConnectionRefusedError, OSError, asyncio.TimeoutError) as exc:
                self._emit("connection_error", {"error": str(exc)})
            except Exception:
                logger.exception("worker session ended unexpectedly")
            finally:
                self.state = WorkerState.DISCONNECTED
                self._emit("disconnected", {})

            if not self._should_run:
                break
            self._emit("reconnecting", {"in_seconds": backoff})
            await asyncio.sleep(backoff)
            backoff = min(RECONNECT_BACKOFF_MAX, backoff * 2)
        if not self._should_run:
            asyncio.get_running_loop().call_soon(asyncio.get_running_loop().stop)

    async def _register_and_run(self, reader, writer):
        if not self._user_authorized:
            self.state = WorkerState.AWAITING_CONSENT
            self._emit("awaiting_consent", {})
            while not self._user_authorized and self._should_run:
                await asyncio.sleep(0.3)
            if not self._should_run:
                return

        self.state = WorkerState.CONNECTING
        msg_type = MessageType.RECONNECT if (self._is_reconnect and self.token) else MessageType.REGISTER
        await write_message(writer, Message(msg_type, {
            "version": PROTOCOL_VERSION,
            "worker_id": self.worker_id,
            "name": self.worker_name,
            "pairing_code": self._pending_pairing_code,
            "token": self.token,
            "sysinfo": get_full_snapshot(),
        }))

        reply = await read_message(reader)
        if reply is None or reply.type != MessageType.AUTHORIZED:
            reason = reply.payload.get("reason") if reply else "connection closed"
            was_reconnect = msg_type == MessageType.RECONNECT
            if was_reconnect:
                self._emit("session_expired", {"reason": reason})
            else:
                self._emit("rejected", {"reason": reason})
                # A rejected first-time registration needs operator action
                # (for example, a corrected pairing code); do not retry forever.
                self._should_run = False
            self._is_reconnect = False
            self.token = None
            writer.close()
            return

        self.token = reply.payload["token"]
        self._is_reconnect = True
        self.state = WorkerState.CONNECTED
        self._emit("authorized", {"master": f"{self.master_host}:{self.master_port}"})

        hb_task = asyncio.ensure_future(self._heartbeat_loop(writer))
        try:
            while True:
                msg = await read_message(reader)
                if msg is None:
                    break
                await self._dispatch(msg, writer)
        finally:
            hb_task.cancel()
            if self._current_task_cancel is not None:
                self._current_task_cancel.set()
            if self._active_task is not None and not self._active_task.done():
                try:
                    await asyncio.wait_for(asyncio.shield(self._active_task), timeout=2)
                except asyncio.TimeoutError:
                    self._active_task.cancel()
                except Exception:
                    pass

    async def _heartbeat_loop(self, writer):
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL_SEC)
            try:
                await write_message(writer, Message(MessageType.HEARTBEAT, {"client_ts": time.time()}))
                await write_message(writer, Message(MessageType.STATUS_UPDATE,
                                    {"sysinfo": get_full_snapshot()}))
            except Exception:
                return

    async def _dispatch(self, msg: Message, writer):
        if msg.type == MessageType.TASK_ASSIGN:
            if self._active_task is not None and not self._active_task.done():
                # The Master can answer a just-sent TASK_COMPLETE before this
                # coroutine has returned. Allow that tiny completion window,
                # but never overlap two independent task handlers.
                try:
                    await asyncio.wait_for(asyncio.shield(self._active_task), timeout=1.0)
                except asyncio.TimeoutError:
                    await write_message(writer, Message(MessageType.TASK_FAILED, {
                        "chunk_id": msg.payload.get("chunk_id"),
                        "error": "worker is already processing a task",
                    }))
                    return
            self._active_task = asyncio.create_task(self._handle_task_assign(msg, writer))
        elif msg.type == MessageType.TASK_CANCEL:
            if self._current_task_cancel is not None:
                self._current_task_cancel.set()
            self._emit("task_cancelled", {})
        elif msg.type == MessageType.PAUSE:
            self._paused = True
            self._emit("paused", {})
        elif msg.type == MessageType.RESUME:
            self._paused = False
            self._emit("resumed", {})
        elif msg.type == MessageType.HEARTBEAT_ACK:
            pass
        elif msg.type == MessageType.DISCONNECT:
            self._should_run = False
            if self._current_task_cancel is not None:
                self._current_task_cancel.set()
            writer.close()
            self._emit("remote_disconnect", {})

    async def _handle_task_assign(self, msg: Message, writer):
        chunk_id = msg.payload["chunk_id"]
        task_type = msg.payload["task_type"]
        params = dict(msg.payload["params"])

        await write_message(writer, Message(MessageType.TASK_ACK, {"chunk_id": chunk_id}))

        if self._paused:
            await write_message(writer, Message(MessageType.TASK_FAILED,
                                {"chunk_id": chunk_id, "error": "worker paused"}))
            return

        # Translate the 1-100 "CPU limit" slider into a small sleep inserted
        # between batches of work *inside* the task -- this is the only
        # per-task parameter the worker adds. Which code runs is never
        # taken from the network; only `task_type` (checked against the
        # local registry) decides that.
        if self.cpu_limit_percent < 100:
            idle_fraction = (100 - self.cpu_limit_percent) / 100.0
            params["cpu_limit_sleep"] = 0.01 * idle_fraction * 10

        cancel_event = threading.Event()
        self._current_task_cancel = cancel_event
        params["_cancel_event"] = cancel_event
        self.state = WorkerState.COMPUTING
        self._emit("task_started", {"chunk_id": chunk_id})
        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(self._executor, run_task, task_type, params)
            await write_message(writer, Message(MessageType.TASK_COMPLETE,
                                {"chunk_id": chunk_id, "result": result}))
            self._emit("task_completed", {"chunk_id": chunk_id})
        except Exception as exc:
            await write_message(writer, Message(MessageType.TASK_FAILED,
                                {"chunk_id": chunk_id, "error": str(exc)}))
            self._emit("task_failed", {"chunk_id": chunk_id, "error": str(exc)})
        finally:
            if self._current_task_cancel is cancel_event:
                self._current_task_cancel = None
            self.state = WorkerState.CONNECTED

    def send_discovery_probe(self, timeout=1.5):
        """One-shot UDP broadcast to find a Master on the LAN.
        Returns (host, port) or None. Safe to call from the GUI thread;
        it's a short blocking call, not a coroutine."""
        import json
        import socket as _socket
        from shared.constants import DISCOVERY_MAGIC, DISCOVERY_REPLY_MAGIC

        sock = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
        sock.setsockopt(_socket.SOL_SOCKET, _socket.SO_BROADCAST, 1)
        sock.settimeout(timeout)
        try:
            sock.sendto(json.dumps({"magic": DISCOVERY_MAGIC}).encode("utf-8"),
                        ("255.255.255.255", 8766))
            data, _addr = sock.recvfrom(2048)
            reply = json.loads(data.decode("utf-8"))
            if reply.get("magic") == DISCOVERY_REPLY_MAGIC:
                return reply["host"], reply["port"]
        except Exception:
            return None
        finally:
            sock.close()
        return None
