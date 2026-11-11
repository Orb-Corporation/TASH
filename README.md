# TASH — LAN Task Sharing

TASH is a small, consent-based distributed-computing prototype for devices on the same local network. A **Master** divides a supported workload into chunks; authorized **Workers** compute those chunks and return results over TCP.

> **Scope of this build:** the shipped workload is a deterministic CPU benchmark. TASH is not yet a general-purpose compute scheduler, a GPU renderer, or a Minecraft shader/render farm. It does not transmit or execute arbitrary code.

## Current status

- Desktop Master: Python + Tkinter, intended for Windows and Linux.
- Desktop Worker: Python + Tkinter, intended for Windows and Linux.
- Android Worker: native Kotlin source in `android-worker/`; build it with Android Studio.
- Built-in task: `cpu_benchmark` only.
- Tests: unit tests plus a real TCP localhost smoke test.
- No prebuilt Windows installer or signed Android APK is published yet.

The source is suitable for hands-on testing, but it still needs validation across multiple physical devices and different routers/firewall configurations before being called a stable release.

## Features

- Master and Worker applications communicate over a local TCP connection.
- UDP broadcast discovery can locate a Master on the same IPv4 LAN.
- First-time pairing requires the Master's numeric pairing code and approval on the Master device.
- Workers require a separate local confirmation before connecting.
- Previously approved Workers can reconnect during the same Master session with a session token.
- Chunk assignment, timeout/reassignment, progress, per-worker statistics, pause, cancellation, and benchmark comparison are implemented.
- Workers execute only task types registered in their own local task registry. A Master cannot send Python, shell commands, binaries, or scripts for a Worker to execute.
- The Android Worker speaks the same protocol and can connect to the Python desktop Master.

## Requirements

- Python 3.9 or newer.
- Tkinter (included with most standard Python distributions; on some Linux distributions install the system Tk package).
- `psutil` for CPU and memory reporting; the app degrades gracefully if it is unavailable.

Install dependencies from the repository root:

```bash
python -m venv .venv
# Windows:
.venv\\Scripts\\activate
# Linux:
source .venv/bin/activate

python -m pip install -r requirements.txt
```

Optional NVIDIA GPU telemetry:

```bash
python -m pip install GPUtil
```

## Run the desktop applications

Start the Master on one computer:

```bash
python -m master.master_app
```

Start a Worker on each additional computer:

```bash
python -m worker.worker_app
```

On the Master dashboard, note the displayed pairing code. In each Worker app, enter the Master's LAN IP address, TCP port (default `8765`), and pairing code. The Worker operator must confirm the local authorization prompt, then the Master operator must approve the Worker.

Workers on the same IPv4 network can use **Find Master on LAN** instead of entering the IP manually. Discovery uses UDP port `8766`.

The shell launchers are also available from the repository root:

```bash
bash run_master.sh
bash run_worker.sh
```

## Run a job

1. Start and authorize at least one Worker.
2. Open the Master's **Job** tab.
3. Set a positive workload size `N` and chunk size.
4. Choose **Run on Master only** for a local baseline or **Distribute to Workers** for a distributed run.
5. Monitor progress and use **Stop all computation** to cancel pending and in-flight chunks.

The **Benchmark** tab compares a local run with runs using one or all authorized Workers. Distribution is not guaranteed to be faster: network overhead and slower devices can make small jobs slower.

## Android Worker

Open the `android-worker/` directory as a Gradle project in Android Studio, allow Gradle sync to finish, then use **Build → Build APK(s)**. Install the generated debug APK on an Android device on the same LAN as the Master.

The Android app currently ships as a **Worker**. Enter the Master address, port, pairing code, and a Worker name; tap **Connect** and approve the local confirmation. LAN discovery uses the same protocol markers as the desktop Master. The Android app is foreground-only; it does not claim to keep computing after Android suspends or closes it.

## Tests

Install development requirements and run the unit suite:

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

Run the socket-level smoke test:

```bash
python e2e_check.py
```

The smoke test starts a real Master and Worker on localhost, authorizes them through a test-only auto-approval switch, distributes a job, and compares the result with a direct computation. It does not replace testing on separate physical devices.

## Project structure

The core implementation files remain at the repository root. Small package modules under `master/`, `worker/`, `shared/`, and `tasks/` provide stable import paths used by the app launchers and tests.

```text
TASH/
├── master/                 # Master package entry points and import adapters
├── worker/                 # Worker package, import adapter, and Tkinter GUI
├── shared/                 # shared protocol, constants, crypto, telemetry adapters
├── tasks/                  # local task registry and built-in task adapters
├── android-worker/         # native Android Worker
├── protocol.py             # newline-delimited JSON protocol
├── master_core.py          # Master networking and authorization
├── worker_core.py          # Worker networking and task execution
├── task_manager.py         # chunk scheduling and result aggregation
├── gui.py                  # Master GUI
├── cpu_benchmark_task.py   # built-in deterministic CPU workload
├── test_*.py               # unit tests
├── e2e_check.py            # localhost integration smoke test
├── PROTOCOL.md
└── TROUBLESHOOTING.md
```

## Network and security notes

- TASH is designed for a trusted local network, not for exposure to the public internet. Do not forward TCP `8765` or UDP `8766` from your router.
- Pairing and approval prevent accidental or casual unauthorized joins; this prototype does not provide TLS transport encryption or protection against a malicious peer already on the LAN.
- Session tokens are held in memory by the Master. Restarting the Master invalidates existing Worker sessions; pair those Workers again.
- A task name and JSON parameters are sent over the network. A Worker only runs the matching function already present in its local `TASK_REGISTRY`.
- Result digests are a low-cost consistency check, not cryptographic proof that a remote machine performed every operation honestly.

See [PROTOCOL.md](PROTOCOL.md) for message details and [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for common setup issues.
