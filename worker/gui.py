"""Tkinter interface for a TASH Worker.

All callbacks from the networking thread are placed on a queue; Tk widgets
are only updated by the GUI thread through the periodic queue poll.
"""
import json
import queue
import socket
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

from shared.constants import DISCOVERY_MAGIC, DISCOVERY_REPLY_MAGIC
from worker.worker_core import WorkerClient


class WorkerApp:
    def __init__(self, root, worker_name: str):
        self.root = root
        self.events = queue.Queue()
        self.client = WorkerClient(worker_name)
        self.client.on_event = lambda event, data: self.events.put((event, data))
        self.paused = False
        self._build_ui(worker_name)
        self.root.title("TASH — Worker")
        self.root.minsize(460, 520)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._poll_events()

    def _build_ui(self, worker_name: str):
        outer = ttk.Frame(self.root, padding=14)
        outer.pack(fill="both", expand=True)

        ttk.Label(outer, text="TASH Worker", font=("TkDefaultFont", 18, "bold")).pack(anchor="w")
        ttk.Label(outer, text="Connect this device to a TASH Master on your local network.").pack(
            anchor="w", pady=(2, 12))

        form = ttk.LabelFrame(outer, text="Master connection", padding=10)
        form.pack(fill="x")

        self.host_var = tk.StringVar()
        self.port_var = tk.StringVar(value="8765")
        self.code_var = tk.StringVar()
        self.name_var = tk.StringVar(value=worker_name)

        self._add_field(form, "Master IP / hostname", self.host_var, 0)
        self._add_field(form, "TCP port", self.port_var, 1)
        self._add_field(form, "Pairing code", self.code_var, 2, show="•")
        self._add_field(form, "Worker name", self.name_var, 3)

        actions = ttk.Frame(form)
        actions.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.find_button = ttk.Button(actions, text="Find Master on LAN", command=self._discover)
        self.find_button.pack(side="left")
        self.connect_button = ttk.Button(actions, text="Connect", command=self._connect)
        self.connect_button.pack(side="right")

        controls = ttk.LabelFrame(outer, text="Worker controls", padding=10)
        controls.pack(fill="x", pady=10)
        self.status_var = tk.StringVar(value="Disconnected")
        ttk.Label(controls, textvariable=self.status_var, font=("TkDefaultFont", 11, "bold")).pack(
            anchor="w", pady=(0, 8))

        control_row = ttk.Frame(controls)
        control_row.pack(fill="x")
        self.pause_button = ttk.Button(control_row, text="Pause", command=self._toggle_pause,
                                       state="disabled")
        self.pause_button.pack(side="left")
        self.disconnect_button = ttk.Button(control_row, text="Disconnect",
                                            command=self._disconnect, state="disabled")
        self.disconnect_button.pack(side="left", padx=(6, 0))

        cpu_row = ttk.Frame(controls)
        cpu_row.pack(fill="x", pady=(12, 0))
        ttk.Label(cpu_row, text="CPU limit").pack(side="left")
        self.cpu_value = tk.StringVar(value="100%")
        ttk.Label(cpu_row, textvariable=self.cpu_value).pack(side="right")
        self.cpu_scale = ttk.Scale(controls, from_=1, to=100, value=100,
                                   command=self._set_cpu_limit)
        self.cpu_scale.pack(fill="x", pady=(4, 0))

        log_frame = ttk.LabelFrame(outer, text="Activity", padding=6)
        log_frame.pack(fill="both", expand=True)
        self.log = tk.Text(log_frame, height=10, wrap="word", state="disabled")
        self.log.pack(fill="both", expand=True)

    @staticmethod
    def _add_field(parent, label, variable, row, show=None):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=3)
        entry = ttk.Entry(parent, textvariable=variable, show=show)
        entry.grid(row=row, column=1, sticky="ew", pady=3)
        parent.columnconfigure(1, weight=1)
        return entry

    def _connect(self):
        host = self.host_var.get().strip()
        code = self.code_var.get().strip()
        name = self.name_var.get().strip()
        try:
            port = int(self.port_var.get().strip())
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            messagebox.showerror("Invalid port", "Enter a TCP port between 1 and 65535.",
                                 parent=self.root)
            return
        if not host or not code or not name:
            messagebox.showerror("Missing information",
                                 "Master address, pairing code, and Worker name are required.",
                                 parent=self.root)
            return
        if not messagebox.askyesno(
                "Authorize this Master",
                f"Allow this device to connect to {host}:{port} and run TASH's built-in tasks?",
                parent=self.root):
            return

        self.client.worker_name = name
        self.client.set_user_authorized(True)
        self.client.set_cpu_limit(int(float(self.cpu_scale.get())))
        self.client.start(host, port, code)
        self.connect_button.configure(state="disabled")
        self.find_button.configure(state="disabled")
        self.disconnect_button.configure(state="normal")
        self.status_var.set("Connecting…")
        self._append_log(f"Connection requested for {host}:{port}.")

    def _disconnect(self):
        self.client.disconnect_gracefully()
        self.client.set_user_authorized(False)
        self.paused = False
        self.pause_button.configure(text="Pause", state="disabled")
        self.disconnect_button.configure(state="disabled")
        self.connect_button.configure(state="normal")
        self.find_button.configure(state="normal")
        self.status_var.set("Disconnected")

    def _toggle_pause(self):
        self.paused = not self.paused
        self.client.set_paused(self.paused)
        self.pause_button.configure(text="Resume" if self.paused else "Pause")
        self.status_var.set("Paused locally" if self.paused else "Resume requested")

    def _set_cpu_limit(self, value):
        limit = max(1, min(100, int(float(value))))
        self.cpu_value.set(f"{limit}%")
        self.client.set_cpu_limit(limit)

    def _discover(self):
        self.find_button.configure(state="disabled")
        self.status_var.set("Searching local network…")
        threading.Thread(target=self._discover_thread, daemon=True).start()

    def _discover_thread(self):
        found = {}
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                sock.settimeout(0.5)
                probe = json.dumps({"magic": DISCOVERY_MAGIC}).encode("utf-8")
                sock.sendto(probe, ("255.255.255.255", 8766))
                deadline = time.monotonic() + 2.0
                while time.monotonic() < deadline:
                    try:
                        data, address = sock.recvfrom(4096)
                    except socket.timeout:
                        continue
                    try:
                        reply = json.loads(data.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        continue
                    if reply.get("magic") != DISCOVERY_REPLY_MAGIC:
                        continue
                    host = reply.get("host") or address[0]
                    port = int(reply.get("port", 8765))
                    found[f"{reply.get('name', 'Master')} — {host}:{port}"] = (host, port)
        except OSError as exc:
            self.events.put(("discovery_error", {"error": str(exc)}))
            return
        self.events.put(("discovery_done", {"found": found}))

    def _poll_events(self):
        try:
            while True:
                event, data = self.events.get_nowait()
                self._handle_event(event, data)
        except queue.Empty:
            pass
        if self.root.winfo_exists():
            self.root.after(150, self._poll_events)

    def _handle_event(self, event, data):
        if event == "discovery_done":
            self.find_button.configure(state="normal")
            found = data["found"]
            if not found:
                self.status_var.set("No Master found. Enter its address manually.")
                self._append_log("LAN discovery found no Master.")
                return
            labels = list(found)
            selected = tk.StringVar(value=labels[0])
            dialog = tk.Toplevel(self.root)
            dialog.title("Masters found")
            dialog.transient(self.root)
            dialog.grab_set()
            ttk.Label(dialog, text="Select a Master:").pack(anchor="w", padx=12, pady=8)
            combo = ttk.Combobox(dialog, textvariable=selected, values=labels, state="readonly",
                                 width=42)
            combo.pack(padx=12, pady=4)
            def choose():
                host, port = found.get(selected.get(), next(iter(found.values())))
                self.host_var.set(host)
                self.port_var.set(str(port))
                self.status_var.set("Master selected")
                dialog.destroy()
            ttk.Button(dialog, text="Use selected Master", command=choose).pack(pady=10)
        elif event == "discovery_error":
            self.find_button.configure(state="normal")
            self.status_var.set("LAN discovery failed")
            self._append_log(data["error"])
        elif event == "authorized":
            self.status_var.set(f"Connected to {data.get('master', 'Master')}")
            self.connect_button.configure(state="disabled")
            self.find_button.configure(state="disabled")
            self.pause_button.configure(state="normal")
            self.disconnect_button.configure(state="normal")
            self._append_log("Master authorized this Worker.")
        elif event == "remote_disconnect":
            self.status_var.set("Disconnected by Master")
            self.connect_button.configure(state="normal")
            self.find_button.configure(state="normal")
            self.pause_button.configure(state="disabled")
            self.disconnect_button.configure(state="disabled")
            self._append_log("The Master disconnected this Worker.")
        elif event == "disconnected":
            self.status_var.set("Connection interrupted; retrying…" if self.client._should_run
                                else "Disconnected")
            self._append_log("Disconnected; automatic reconnect is active."
                             if self.client._should_run else "Disconnected.")
        elif event == "session_expired":
            self.status_var.set("Session expired; requesting fresh approval…")
            self._append_log(f"Session expired: {data.get('reason', 'unknown reason')}")
        elif event == "rejected":
            self.client.set_user_authorized(False)
            self.status_var.set(f"Rejected: {data.get('reason', 'unknown reason')}")
            self.connect_button.configure(state="normal")
            self.find_button.configure(state="normal")
            self.pause_button.configure(state="disabled")
            self.disconnect_button.configure(state="disabled")
            self._append_log(data.get("reason", event))
        elif event == "connection_error":
            self._append_log(f"Connection error: {data.get('error', 'unknown error')}")
        elif event == "reconnecting":
            self.status_var.set(f"Reconnecting in {data.get('in_seconds', '?')} s…")
        elif event == "connecting":
            self.status_var.set(f"Connecting to {data.get('host')}:{data.get('port')}…")
        elif event == "task_started":
            self.status_var.set(f"Computing chunk {data.get('chunk_id')}")
            self._append_log(f"Started chunk {data.get('chunk_id')}.")
        elif event == "task_completed":
            self._append_log(f"Completed chunk {data.get('chunk_id')}.")
        elif event == "task_failed":
            self._append_log(f"Chunk {data.get('chunk_id')} failed: {data.get('error')}")
        elif event == "task_cancelled":
            self._append_log("Cancellation requested.")
        elif event in ("paused", "resumed"):
            self.status_var.set("Paused" if event == "paused" else "Connected")
            self._append_log(event.capitalize())
        else:
            self._append_log(f"{event}: {data}")

    def _append_log(self, message):
        self.log.configure(state="normal")
        self.log.insert("end", f"{time.strftime('%H:%M:%S')}  {message}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _on_close(self):
        try:
            self.client.disconnect_gracefully()
        finally:
            self.root.destroy()
