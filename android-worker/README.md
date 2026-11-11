# TASH Android Worker

A native Kotlin Worker for TASH protocol v1.0. It connects to the Python desktop Master over TCP, performs pairing, sends heartbeats/status updates, accepts only the built-in `cpu_benchmark` task, and reconnects using the session token issued by the Master.

## Build

Open `android-worker/` in Android Studio as an existing Gradle project. After Gradle sync, select **Build → Build APK(s)**. The generated debug APK is under `app/build/outputs/apk/debug/`.

## Connect

1. Start the Python TASH Master on Windows or Linux.
2. Read its current pairing code and local IPv4 address.
3. Open TASH Android Worker.
4. Enter the Master IP, port `8765`, pairing code, and a Worker name.
5. Press **Connect** and approve the local Android confirmation dialog.
6. Approve the Worker on the Master dashboard.

The Android Worker uses the same UDP discovery identifiers as the Python Master. If the network blocks broadcast discovery, enter the Master IP manually.

## Current limitations

- The Android app is a Worker, not a supported Master.
- GPU and per-process CPU utilization are reported as unavailable.
- The app is foreground-only. Android may suspend or stop work when the app is backgrounded or the device enters power-saving modes.
- Only the built-in CPU benchmark task is supported.
- The Android app has not been validated across a broad range of Android devices; treat it as experimental until tested on your hardware.
