#!/usr/bin/env python3
"""
Central monitoring server: receives metrics from agents, sends Telegram alerts.
Run on your main server.
"""

import json
import time
import threading
import requests
from flask import Flask, request, jsonify
from datetime import datetime, timedelta

# ─── Load config ────────────────────────────────────────────────────────────
with open("config.json") as f:
    CONFIG = json.load(f)

BOT_TOKEN   = CONFIG["telegram"]["bot_token"]
CHAT_ID     = CONFIG["telegram"]["chat_id"]
API_SECRET  = CONFIG["api_secret"]
THRESHOLDS  = CONFIG["thresholds"]
OFFLINE_TTL = CONFIG.get("offline_after_seconds", 90)   # mark offline if no data for N sec

app = Flask(__name__)

# ─── In-memory state ─────────────────────────────────────────────────────────
# { server_name: { "last_seen": timestamp, "metrics": {...}, "alerts": set() } }
state = {}
state_lock = threading.Lock()


# ─── Telegram helpers ────────────────────────────────────────────────────────
def tg_send(text: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=10)
    except Exception as e:
        print(f"[TG ERROR] {e}")


def alert(server: str, key: str, message: str, recover=False):
    """Send alert only once per issue (avoid spam). recover=True clears the alert."""
    with state_lock:
        alerts = state[server].setdefault("alerts", set())
        if recover:
            if key in alerts:
                alerts.discard(key)
                tg_send(f"✅ <b>{server}</b> — {message}")
        else:
            if key not in alerts:
                alerts.add(key)
                tg_send(f"🚨 <b>{server}</b> — {message}")


# ─── Metric checks ───────────────────────────────────────────────────────────
def check_metrics(server: str, m: dict):
    cpu  = m.get("cpu_percent", 0)
    ram  = m.get("ram_percent", 0)
    disk = m.get("disk_percent", 0)
    net_in  = m.get("net_in_mbps", 0)
    net_out = m.get("net_out_mbps", 0)

    # CPU
    if cpu > THRESHOLDS["cpu"]:
        alert(server, "cpu", f"CPU {cpu:.1f}% (порог {THRESHOLDS['cpu']}%)")
    else:
        alert(server, "cpu", f"CPU вернулся в норму ({cpu:.1f}%)", recover=True)

    # RAM
    if ram > THRESHOLDS["ram"]:
        alert(server, "ram", f"RAM {ram:.1f}% (порог {THRESHOLDS['ram']}%)")
    else:
        alert(server, "ram", f"RAM вернулась в норму ({ram:.1f}%)", recover=True)

    # Disk
    if disk > THRESHOLDS["disk"]:
        alert(server, "disk", f"Диск {disk:.1f}% (порог {THRESHOLDS['disk']}%)")
    else:
        alert(server, "disk", f"Диск в норме ({disk:.1f}%)", recover=True)

    # Services
    for svc, status in m.get("services", {}).items():
        if not status:
            alert(server, f"svc_{svc}", f"Сервис <code>{svc}</code> упал!")
        else:
            alert(server, f"svc_{svc}", f"Сервис <code>{svc}</code> снова работает", recover=True)


# ─── Offline watchdog ────────────────────────────────────────────────────────
def offline_watchdog():
    """Background thread: marks servers offline if they stop reporting."""
    while True:
        time.sleep(15)
        now = time.time()
        with state_lock:
            for server, data in state.items():
                last = data.get("last_seen", 0)
                was_offline = "offline" in data.get("alerts", set())
                if now - last > OFFLINE_TTL:
                    if not was_offline:
                        data.setdefault("alerts", set()).add("offline")
                        tg_send(f"🔴 <b>{server}</b> — сервер недоступен (нет данных более {OFFLINE_TTL}с)")
                else:
                    if was_offline:
                        data.get("alerts", set()).discard("offline")
                        tg_send(f"🟢 <b>{server}</b> — сервер снова онлайн")


# ─── API endpoints ───────────────────────────────────────────────────────────
@app.route("/metrics", methods=["POST"])
def receive_metrics():
    if request.headers.get("X-Secret") != API_SECRET:
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(force=True)
    server = data.get("server")
    metrics = data.get("metrics", {})

    if not server:
        return jsonify({"error": "missing server"}), 400

    with state_lock:
        if server not in state:
            state[server] = {}
        state[server]["last_seen"] = time.time()
        state[server]["metrics"] = metrics

    check_metrics(server, metrics)
    return jsonify({"ok": True})


@app.route("/status", methods=["GET"])
def status_page():
    """Quick JSON overview of all servers."""
    if request.headers.get("X-Secret") != API_SECRET:
        return jsonify({"error": "unauthorized"}), 401

    now = time.time()
    out = {}
    with state_lock:
        for server, data in state.items():
            age = now - data.get("last_seen", 0)
            out[server] = {
                "online": age < OFFLINE_TTL,
                "last_seen_sec_ago": round(age),
                "metrics": data.get("metrics", {}),
                "active_alerts": list(data.get("alerts", [])),
            }
    return jsonify(out)


# ─── Telegram bot polling ────────────────────────────────────────────────────
def format_status() -> str:
    now = time.time()
    lines = []
    with state_lock:
        if not state:
            return "Нет данных. Агенты ещё не отправили метрики."
        for server, data in sorted(state.items()):
            age = now - data.get("last_seen", 0)
            online = age < OFFLINE_TTL
            icon = "🟢" if online else "🔴"
            m = data.get("metrics", {})
            cpu  = m.get("cpu_percent",  "?")
            ram  = m.get("ram_percent",  "?")
            disk = m.get("disk_percent", "?")
            net_in  = m.get("net_in_mbps",  0)
            net_out = m.get("net_out_mbps", 0)
            svcs = m.get("services", {})
            svc_str = ""
            if svcs:
                svc_str = "  " + " ".join(
                    f"{'✅' if ok else '❌'}{n}" for n, ok in svcs.items()
                )
            lines.append(
                f"{icon} <b>{server}</b>\n"
                f"  CPU: {cpu}%  RAM: {ram}%  Диск: {disk}%\n"
                f"  ↓{net_in:.1f} ↑{net_out:.1f} Mbps"
                + (f"\n{svc_str}" if svc_str else "")
            )
    return "\n\n".join(lines)


def bot_polling():
    offset = 0
    url = f"https://api.telegram.org/bot{BOT_TOKEN}"
    while True:
        try:
            r = requests.get(f"{url}/getUpdates", params={"offset": offset, "timeout": 20}, timeout=25)
            updates = r.json().get("result", [])
            for upd in updates:
                offset = upd["update_id"] + 1
                msg = upd.get("message", {})
                text = msg.get("text", "").strip()
                cid  = msg.get("chat", {}).get("id")
                if not cid:
                    continue
                if str(cid) != str(CHAT_ID):
                    continue
                if text == "/status":
                    tg_send(f"📊 <b>Статус серверов</b>\n\n{format_status()}")
                elif text == "/help":
                    tg_send("Команды:\n/status — статус всех серверов\n/help — помощь")
        except Exception as e:
            print(f"[BOT ERROR] {e}")
            time.sleep(5)


# ─── Entry point ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("[*] Starting offline watchdog...")
    threading.Thread(target=offline_watchdog, daemon=True).start()

    print("[*] Starting Telegram bot polling...")
    threading.Thread(target=bot_polling, daemon=True).start()

    print("[*] Starting Flask API on :5000 ...")
    tg_send("🚀 Monitoring server started!")
    app.run(host="0.0.0.0", port=5000)
