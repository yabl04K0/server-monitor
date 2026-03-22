#!/usr/bin/env python3
"""
Monitoring agent — run on EVERY server you want to monitor.
Sends metrics to the central server every 30 seconds.
"""

import json
import time
import subprocess
import socket
import requests
import psutil

# ─── Load config ─────────────────────────────────────────────────────────────
with open("config.json") as f:
    CONFIG = json.load(f)

CENTRAL_URL  = CONFIG["central_url"]          # e.g. "http://1.2.3.4:5000"
API_SECRET   = CONFIG["api_secret"]
SERVER_NAME  = CONFIG.get("server_name") or socket.gethostname()
INTERVAL     = CONFIG.get("interval_seconds", 30)
SERVICES     = CONFIG.get("services", [])     # list of service names to check


# ─── Metric collectors ───────────────────────────────────────────────────────
def get_cpu() -> float:
    return psutil.cpu_percent(interval=1)


def get_ram() -> float:
    return psutil.virtual_memory().percent


def get_disk() -> float:
    return psutil.disk_usage("/").percent


_prev_net = None
def get_network_mbps() -> tuple[float, float]:
    """Returns (in_mbps, out_mbps) averaged over INTERVAL."""
    global _prev_net
    counters = psutil.net_io_counters()
    now = time.time()
    if _prev_net is None:
        _prev_net = (counters, now)
        return 0.0, 0.0
    prev_counters, prev_time = _prev_net
    dt = now - prev_time or 1
    in_mbps  = (counters.bytes_recv - prev_counters.bytes_recv) / dt / 1024 / 1024
    out_mbps = (counters.bytes_sent - prev_counters.bytes_sent) / dt / 1024 / 1024
    _prev_net = (counters, now)
    return round(max(in_mbps, 0), 3), round(max(out_mbps, 0), 3)


def check_service(name: str) -> bool:
    """
    Check if a service is running.
    Supports: systemd services, docker containers, nginx/apache process names.
    """
    # Try systemd first
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", name],
            timeout=5
        )
        if result.returncode == 0:
            return True
        # returncode != 0 could mean "inactive" or "not found" — try docker next
    except FileNotFoundError:
        pass  # systemctl not available

    # Try docker container
    try:
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Running}}", name],
            capture_output=True, text=True, timeout=5
        )
        if result.stdout.strip() == "true":
            return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Fallback: check process name
    for proc in psutil.process_iter(["name"]):
        if proc.info["name"] and name.lower() in proc.info["name"].lower():
            return True

    return False


def collect_metrics() -> dict:
    net_in, net_out = get_network_mbps()
    services = {svc: check_service(svc) for svc in SERVICES}
    return {
        "cpu_percent":  get_cpu(),
        "ram_percent":  get_ram(),
        "disk_percent": get_disk(),
        "net_in_mbps":  net_in,
        "net_out_mbps": net_out,
        "services":     services,
    }


# ─── Main loop ────────────────────────────────────────────────────────────────
def send_metrics(metrics: dict):
    payload = {"server": SERVER_NAME, "metrics": metrics}
    headers = {"X-Secret": API_SECRET, "Content-Type": "application/json"}
    resp = requests.post(f"{CENTRAL_URL}/metrics", json=payload, headers=headers, timeout=10)
    resp.raise_for_status()


if __name__ == "__main__":
    print(f"[*] Agent started on '{SERVER_NAME}', reporting to {CENTRAL_URL} every {INTERVAL}s")
    while True:
        try:
            metrics = collect_metrics()
            send_metrics(metrics)
            print(f"[OK] {time.strftime('%H:%M:%S')} CPU={metrics['cpu_percent']}% "
                  f"RAM={metrics['ram_percent']}% Disk={metrics['disk_percent']}%")
        except Exception as e:
            print(f"[ERR] {time.strftime('%H:%M:%S')} {e}")
        time.sleep(INTERVAL)
