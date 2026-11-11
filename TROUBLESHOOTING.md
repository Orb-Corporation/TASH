# Troubleshooting

## Worker cannot find the Master

- Confirm both devices are on the same IPv4 LAN.
- The Master listens on TCP port `8765` and UDP discovery port `8766` by default.
- Allow Python through the Master computer's firewall for those ports.
  - Debian/Ubuntu with ufw: `sudo ufw allow 8765/tcp` and `sudo ufw allow 8766/udp`.
  - Windows: create inbound rules for TCP 8765 and UDP 8766 for the Python executable.
- Guest Wi-Fi and router client-isolation settings may block device-to-device broadcasts. Enter the Master's LAN IP manually when discovery is blocked.
- Discovery does not cross routers, VPNs, or subnets.

## Invalid pairing code or authorization rejected

The numeric pairing code is generated each time the Master starts. Copy the current code from the Master's Dashboard. A correct code is not sufficient on its own: the Worker operator must confirm the local prompt and the Master operator must approve the Worker.

## Worker repeatedly reconnects after the Master restarts

Session tokens are held in memory by the Master. Restarting it invalidates previous tokens. The Worker falls back to a new registration, which requires the current pairing code and a new approval on the Master.

## Protocol version mismatch

Run the Master and Workers from the same TASH revision. This project intentionally rejects mismatched protocol versions instead of guessing compatibility.

## Tkinter is missing on Linux

Install the Tk bindings for your distribution:

- Debian/Ubuntu: `sudo apt install python3-tk`
- Fedora: `sudo dnf install python3-tkinter`
- Arch: `sudo pacman -S tk`

## A port is already in use

Another Master process may still be running. Check which process owns TCP 8765 or UDP 8766, close it, or change the values in `~/.distcomp_master.json`.

## GPU telemetry displays N/A

GPU reporting is optional and best-effort. Install `GPUtil` for supported NVIDIA devices or ensure `nvidia-smi` is available. TASH's current built-in workload is CPU-based and does not require GPU telemetry.

## Distributed execution is slower than local execution

This is expected for small jobs: network transfer, scheduling, and coordination can cost more than the computation. Use a larger workload, increase chunk size, or use fewer/faster Workers. Check the Benchmark tab before assuming more devices will improve throughput.

## Tests fail with ModuleNotFoundError

Run commands from the repository root, not from one of the package subdirectories. The repository includes package adapters under `master/`, `worker/`, `shared/`, and `tasks/`; keep the root directory as the current working directory.
