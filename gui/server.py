#!/usr/bin/env python3
"""
Camera-over-private-5G control GUI  (runs ON the CN+CU host, 10.230.4.33).

Self-contained: Python stdlib only. Controls the camera 5G stack and streams the
live camera feed reconstructed from the F1-U tap (DU<->CU) to the browser, where
mpegts.js decodes it. No ffmpeg/node needed on this host.

Stack:  camera(192.168.1.250) -> UE(10.230.4.39) --5G--> RU(192.168.9.20)
        -> DU(10.230.4.26) --F1--> CU(10.230.4.33) -> CN5G(local docker)

DU needs the PTP chain (ts2phc/ptp4l/phc2sys) up and the clocks LOCKED before it
can start; the GUI exposes PTP as its own control and times the sync.
"""
import json, os, queue, shlex, socket, subprocess, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
KEY  = os.path.expanduser("~/.ssh/pqc_dashboard_ed25519")
SSH  = ["ssh", "-i", KEY, "-o", "BatchMode=yes", "-o", "ConnectTimeout=6",
        "-o", "StrictHostKeyChecking=accept-new"]

DU_HOST, UE_HOST = "snsrl2@10.230.4.26", "snsrl@10.230.4.39"
COMPOSE  = "~/disaggregated_core/oai-cn5g-fed/docker-compose"
CU_TREE  = "/home/snsrl/openairinterface5g"
CU_CONF  = CU_TREE + "/ci-scripts/conf_files/gnb-cu.sa.band78.106prb.conf"
DU_SCR   = "/home/snsrl2/pegatron_oai_setup/scripts/du"
DU_TREE  = "/home/snsrl2/openairinterface5g"
DU_CONF  = DU_TREE + "/ci-scripts/conf_files/gnb-du.sa.band78.273prb.fhi72.pegatron.conf"
DU_POOL  = "7,23,7,23,7,23,7,23"
F1_FILTER = "src 10.230.4.26 and dst 10.230.4.33 and udp port 2153"
PTP_LOCK_NS = 1000          # |offset| below this (ns) = locked / clocks synced

# Per-component timers. Start stamps "start"; the first status poll that sees the component fully
# READY stamps "ready" (time-to-ready = ready - start). Stop clears it.
TIMERS = {}
TRACKED = ("core", "cu", "ptp", "du", "ue_reg", "ue_data", "stream")
def mark_start(c): TIMERS[c] = {"start": time.time(), "ready": None}
def mark_stop(c):  TIMERS.pop(c, None)
def apply_timers(st):
    now = time.time()
    for c in TRACKED:
        s_ = st[c]; ready = bool(s_.get("ready")); t = TIMERS.get(c)
        if t:
            if ready and t["ready"] is None:
                t["ready"] = now
            elif not ready and t["ready"] is not None:      # was ready, then dropped out
                TIMERS.pop(c, None); t = None
        s_["started_at"] = t["start"] if t else None
        s_["ready_at"]   = t["ready"] if t else None
    st["_now"] = now                                         # server clock, so the page ticks in sync

def run(cmd, timeout=25):
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr)
    except subprocess.TimeoutExpired:
        return 124, "timeout"

def ssh(host, remote_cmd, timeout=25):
    return run(" ".join(SSH) + f" {host} {shlex.quote(remote_cmd)}", timeout)

# ------------------------------------------------------------------ status (batched: 2 ssh calls)
DU_PROBE = (
    "r=0; for p in ts2phc ptp4l phc2sys; do pgrep -x $p >/dev/null && r=$((r+1)); done; echo RUN=$r; "
    "echo TS=$(grep -a 'master offset' ~/ptp_logs/ts2phc.log 2>/dev/null | tail -1 | grep -oE 'master offset +-?[0-9]+' | grep -oE '\\-?[0-9]+'); "
    "echo PH=$(grep -a 'offset' ~/ptp_logs/phc2sys.log 2>/dev/null | tail -1 | grep -oE 'offset +-?[0-9]+' | grep -oE '\\-?[0-9]+' | head -1); "
    "pgrep -x nr-softmodem >/dev/null && echo DU=up || echo DU=down; "
    "L=$(ls -t ~/du_*.log 2>/dev/null | head -1); "
    "echo F1=$(grep -ac 'received F1 Setup Response' \"$L\" 2>/dev/null); "
    "echo RXPPS=$(grep -a 'o-du 0\\]\\[rx' \"$L\" 2>/dev/null | tail -1 | grep -oE 'pps +[0-9]+' | head -1 | grep -oE '[0-9]+'); "
    "ping -c1 -W1 192.168.9.20 >/dev/null 2>&1 && echo RU=up || echo RU=down"
)
UE_PROBE = (
    "M=$(mmcli -L 2>/dev/null|grep -oE 'Modem/[0-9]+'|head -1|cut -d/ -f2); "
    "echo UE=$(mmcli -m $M -K 2>/dev/null|grep '^modem.generic.state '|awk -F: '{print $2}'|tr -d ' '); "
    "echo WWAN=$(ip -4 -o addr show wwan0 2>/dev/null|awk '{print $4}'|head -1); "
    "ping -c1 -W1 192.168.1.250 >/dev/null 2>&1 && echo CAM=up || echo CAM=down; "
    "pgrep -x ffmpeg >/dev/null && echo STREAM=up || echo STREAM=down"
)
def _kv(out):
    d = {}
    for line in out.splitlines():
        if "=" in line:
            k, v = line.split("=", 1); d[k.strip()] = v.strip()
    return d

def status():
    st = {}
    # local: core, cu
    _, out = run("sudo -n docker ps --format '{{.Names}}|{{.Status}}'")
    cores = [l for l in out.splitlines() if l.startswith("oai-") or l.startswith("mysql")]
    healthy = sum(1 for l in cores if "healthy" in l)
    st["core"] = {"up": healthy >= 8, "ready": healthy >= 9, "detail": f"{healthy}/9 healthy" if cores else "down"}
    _, out = run("pgrep -x nr-softmodem >/dev/null && echo up || echo down")
    cu_up = "up" in out; ng = ""
    if cu_up:
        _, lg = run("L=$(ls -t ~/cu_*.log 2>/dev/null|head -1); [ -n \"$L\" ] && grep -ac NGSetupResponse \"$L\"")
        ng = "registered" if lg.strip() not in ("", "0") else "starting"
    st["cu"] = {"up": cu_up, "ready": ng == "registered", "detail": ng or "down"}
    # remote .26 (ptp + du + ru)
    _, out = ssh(DU_HOST, DU_PROBE, 14); d = _kv(out)
    runc = int(d.get("RUN", "0") or 0)
    ts = int(d["TS"]) if d.get("TS", "").lstrip("-").isdigit() else None
    ph = int(d["PH"]) if d.get("PH", "").lstrip("-").isdigit() else None
    locked = runc == 3 and ts is not None and abs(ts) < PTP_LOCK_NS and (ph is None or abs(ph) < PTP_LOCK_NS)
    ptp = {"up": runc == 3, "locked": locked, "ready": locked, "running": runc, "offset_ts": ts, "offset_ph": ph}
    if runc == 0:   ptp["detail"] = "down"
    elif runc < 3:  ptp["detail"] = f"{runc}/3 running"
    elif locked:    ptp["detail"] = f"LOCKED  ({ts} ns)"
    else:           ptp["detail"] = f"syncing  ({ts} ns)" if ts is not None else "syncing"
    st["ptp"] = ptp
    du_up = d.get("DU") == "up"
    f1 = int(d["F1"]) if d.get("F1", "").isdigit() else 0
    rx = int(d["RXPPS"]) if d.get("RXPPS", "").isdigit() else 0
    du_ready = du_up and f1 > 0 and rx > 0
    if not du_up:     du_detail = "down"
    elif du_ready:    du_detail = f"ready - F1 up, RU uplink {rx} pps"
    else:             du_detail = ("F1 up" if f1 else "waiting for F1") + ", " + ("RU uplink ok" if rx else "waiting for RU uplink")
    st["du"] = {"up": du_up, "ready": du_ready, "detail": du_detail}
    st["ru"] = {"up": d.get("RU") == "up", "detail": "reachable" if d.get("RU") == "up" else "unreachable"}
    # remote .39 (ue + camera + stream)
    _, out = ssh(UE_HOST, UE_PROBE, 14); u = _kv(out)
    ue_state = u.get("UE", "") or "unknown"
    wwan = u.get("WWAN", "")
    st["ue"] = {"up": ue_state in ("connected", "registered"), "detail": ue_state}
    reg_ok = ue_state in ("connected", "registered")
    st["ue"]["ready"] = reg_ok
    st["ue_reg"]  = {"up": reg_ok, "ready": reg_ok, "detail": ue_state}
    st["ue_data"] = {"up": wwan.startswith("12.1.1."), "ready": wwan.startswith("12.1.1."), "detail": wwan or "no session"}
    st["camera"] = {"up": u.get("CAM") == "up", "detail": "reachable" if u.get("CAM") == "up" else "unreachable"}
    s_up = u.get("STREAM") == "up"
    flowing = s_up and (time.time() - FEED.last_rx) < 4          # packets really arriving at the edge
    st["stream"] = {"up": s_up, "ready": flowing,
                    "detail": "flowing to the edge" if flowing else ("pushing - no data at the edge yet" if s_up else "stopped")}
    apply_timers(st)
    return st

# ------------------------------------------------------------------ actions
def _act(component, action):
    if component == "core":
        verb = "up -d" if action == "start" else "down"
        return run(f"cd {COMPOSE} && sudo -n docker compose -f docker-compose-basic-nrf.yaml {verb}", 180)
    if component == "cu":
        if action == "start":
            return run(f"pgrep -x nr-softmodem >/dev/null && echo already || "
                       f"(cd {CU_TREE} && setsid sudo -n {CU_TREE}/cmake_targets/ran_build/build/nr-softmodem "
                       f"-O {CU_CONF} > ~/cu_$(date +%Y%m%d_%H%M%S).log 2>&1 < /dev/null & echo started)", 20)
        return run("sudo -n pkill -INT -x nr-softmodem; echo stopped", 20)
    if component == "ptp":
        if action == "start":
            cmd = ("mkdir -p ~/ptp_logs; "
                   f"setsid sudo -n bash {DU_SCR}/ts2phc.sh >~/ptp_logs/ts2phc.log 2>&1 </dev/null & sleep 2; "
                   f"setsid sudo -n bash {DU_SCR}/ptp4l.sh  >~/ptp_logs/ptp4l.log  2>&1 </dev/null & sleep 2; "
                   f"setsid sudo -n bash {DU_SCR}/phc2sys.sh >~/ptp_logs/phc2sys.log 2>&1 </dev/null & sleep 1; echo started")
            return ssh(DU_HOST, cmd, 20)
        return ssh(DU_HOST, "sudo -n pkill -x ts2phc; sudo -n pkill -x ptp4l; sudo -n pkill -x phc2sys; echo stopped", 15)
    if component == "du":
        if action == "start":
            # gate: PTP must be locked
            s = status().get("ptp", {})
            if not s.get("locked"):
                return 1, "BLOCKED: PTP not locked yet - start the PTP chain and wait for the clocks to sync."
            cmd = (f"sudo -n bash {DU_SCR}/oai_host_tuning.sh >/dev/null 2>&1; "
                   f"sudo -n bash {DU_SCR}/vf.sh >/dev/null 2>&1; "
                   f"LOG=~/du_$(date +%Y%m%d_%H%M%S).log; "
                   f"cd {DU_TREE} && setsid sudo -n {DU_TREE}/cmake_targets/ran_build/build/nr-softmodem "
                   f"-O {DU_CONF} --thread-pool {DU_POOL} >$LOG 2>&1 </dev/null & sleep 4; "
                   "pgrep -x nr-softmodem >/dev/null && echo started || echo FAILED")
            return ssh(DU_HOST, cmd, 30)
        return ssh(DU_HOST, "sudo -n pkill -9 -x nr-softmodem; echo stopped", 20)
    if component == "stream":
        if action == "start":
            return ssh(UE_HOST, "setsid bash ~/pegatron_oai_setup/scripts/ue/push_camera_over_5g.sh 3600 1 >/tmp/push.log 2>&1 < /dev/null & echo started", 20)
        # kill the push LOOP first (it respawns ffmpeg), then ffmpeg itself (may be root-owned)
        return ssh(UE_HOST, "kill -9 $(cat /tmp/push_camera.pid 2>/dev/null) 2>/dev/null; "
                            "pkill -9 -f '[p]ush_camera_over_5g' 2>/dev/null; "
                            "sudo -n pkill -9 -x ffmpeg 2>/dev/null; rm -f /tmp/push_camera.pid; echo stopped", 15)
    if component == "ue_reg":          # registration only (attach to the network)
        if action == "start":
            return ssh(UE_HOST, "M=$(mmcli -L|grep -oE 'Modem/[0-9]+'|head -1|cut -d/ -f2); "
                       "sudo -n mmcli -m $M --enable 2>/dev/null; "
                       "bash ~/pegatron_oai_setup/scripts/ue/send_at_command_to_modem.sh 'AT+COPS=0' 10 >/dev/null 2>&1; echo registering", 30)
        return ssh(UE_HOST, "M=$(mmcli -L|grep -oE 'Modem/[0-9]+'|head -1|cut -d/ -f2); sudo -n mmcli -m $M --disable 2>/dev/null; echo deregistered", 20)
    if component == "ue_data":         # PDU session / data plane (needs registration first)
        if action == "start":
            return ssh(UE_HOST, "setsid bash ~/pegatron_oai_setup/scripts/ue/2_connect_to_oai_core_and_get_data_plane.sh >/tmp/ue_gui.log 2>&1 < /dev/null & echo connecting", 20)
        return ssh(UE_HOST, "setsid bash ~/pegatron_oai_setup/scripts/ue/3_disconnect_and_deregister_ue.sh >/tmp/ue_gui.log 2>&1 < /dev/null & echo disconnecting", 20)
    return 1, "unknown component"

def act(component, action):
    rc, out = _act(component, action)
    if component in TRACKED:
        if action == "start" and rc == 0 and "BLOCKED" not in out:
            mark_start(component)
        elif action == "stop":
            mark_stop(component)
    return rc, out

# ------------------------------------------------------------------ feed (EDGE: MPEG-TS on UDP 5004)
# The camera push (push_camera_over_5g.sh) sends MPEG-TS over 5G to 192.168.70.129:5004,
# which lands on THIS host's demo-oai bridge. We receive it here (the edge / core side) and
# fan it out to every browser that opens /feed. No tcpdump, no ffmpeg.
FEED_PORT = 5004
RELAY_PORT = 5005          # local UDP relay for a native player (GStreamer) on this host
class EdgeFeed:
    def __init__(self, port=FEED_PORT):
        self.port = port; self.subs = set(); self.lock = threading.Lock(); self.started = False; self.last_rx = 0.0
    def _reader(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try: s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
        except OSError: pass
        s.bind(("0.0.0.0", self.port))
        relay = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        while True:
            data, _ = s.recvfrom(4096)
            self.last_rx = time.time()
            try: relay.sendto(data, ("127.0.0.1", RELAY_PORT))   # for a local GStreamer window
            except OSError: pass
            with self.lock:
                dead = []
                for q in self.subs:
                    try: q.put_nowait(data)
                    except queue.Full: dead.append(q)
                for q in dead: self.subs.discard(q)
    def ensure(self):
        if not self.started:
            self.started = True
            threading.Thread(target=self._reader, daemon=True).start()
    def subscribe(self):
        self.ensure(); q = queue.Queue(maxsize=4000)
        with self.lock: self.subs.add(q)
        return q
    def unsubscribe(self, q):
        with self.lock: self.subs.discard(q)
FEED = EdgeFeed()

# ------------------------------------------------------------------ http
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, body, ctype="application/json"):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"): return self._serve_file("static/index.html", "text/html")
        if path.startswith("/static/"):  return self._serve_file(path.lstrip("/"), self._ctype(path))
        if path == "/api/status":        return self._send(200, json.dumps(status()))
        if path == "/feed":              return self._stream_feed()
        self._send(404, "not found", "text/plain")
    def do_POST(self):
        if urlparse(self.path).path == "/api/action":
            n = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(n) or b"{}")
            rc, out = act(data.get("component"), data.get("action"))
            return self._send(200, json.dumps({"rc": rc, "out": out[-800:]}))
        self._send(404, "not found", "text/plain")
    def _ctype(self, p):
        return ("text/html" if p.endswith(".html") else "text/css" if p.endswith(".css")
                else "application/javascript" if p.endswith(".js") else "application/octet-stream")
    def _serve_file(self, rel, ctype):
        fp = os.path.join(HERE, rel)
        if not os.path.isfile(fp): return self._send(404, "not found", "text/plain")
        with open(fp, "rb") as f: self._send(200, f.read(), ctype)
    def _stream_feed(self):
        q = FEED.subscribe()
        self.send_response(200); self.send_header("Content-Type", "video/mp2t")
        self.send_header("Cache-Control", "no-cache"); self.end_headers()
        try:
            while True:
                self.wfile.write(q.get(timeout=30))
        except (BrokenPipeError, ConnectionResetError, queue.Empty): pass
        finally:
            FEED.unsubscribe(q)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    FEED.ensure()   # receive the edge stream (and relay to the local player) even with no browser open
    print(f"Camera 5G GUI on http://0.0.0.0:{port}")
    ThreadingHTTPServer(("0.0.0.0", port), H).serve_forever()
