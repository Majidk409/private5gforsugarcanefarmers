#!/bin/bash
# Runs ON the UE host (10.230.4.39). Pulls the CP Plus camera and pushes it as MPEG-TS over
# the 5G uplink to the core/edge, so it crosses RU -> DU -> CU (F1-U) -> UPF.
#
# Usage: push_camera_over_5g.sh [seconds] [subtype]
#   seconds : total run time        (default 900)
#   subtype : 0 = main HD 2560x1440 (sharp but choppy live over lossy 5G)
#             1 = sub  352x288      (default; smooth live view)
#
# Robust by design:
#   - kills ANY leftover ffmpeg first, including root-owned ones (those were the #1 cause of
#     stalls: a stuck ffmpeg keeps a camera RTSP session open and the camera caps concurrent
#     sessions, so every new push starved)
#   - re-adds the camera-subnet address on the USB-eth adapter if a modem/NM reset dropped it
#   - re-asserts rp_filter + the 5G route to the core each run
#   - waits for the camera, then restarts ffmpeg automatically if it exits before the deadline
set -u

T="${1:-900}"; SUB="${2:-1}"
ADAPTER=enx00e04c0f51a8
CAM_IP=192.168.1.250          # the camera (browse to this)
UE_ADAPTER_ADDR=192.168.1.10/24   # the UE host's OWN IP on the camera cable (its gateway side)
CAM_USER="${CAM_USER:-admin}"
CAM_PASS="${CAM_PASS:?set CAM_PASS to the camera password (write @ as %40)}"
CAM="rtsp://${CAM_USER}:${CAM_PASS}@${CAM_IP}:554/cam/realmonitor?channel=1&subtype=${SUB}"
EDGE="udp://192.168.70.129:5004?pkt_size=1316"
log(){ echo "[push] $*"; }

# 1) stop any previous push loop FIRST (it would respawn ffmpeg), then clear ALL ffmpeg
#    (root-owned too) so only our single push runs. Our pid is recorded so Stop can kill the loop.
PIDFILE=/tmp/push_camera.pid
[ -f "$PIDFILE" ] && { old=$(cat "$PIDFILE" 2>/dev/null); [ -n "$old" ] && [ "$old" != "$$" ] && kill -9 "$old" 2>/dev/null; }
echo $$ > "$PIDFILE"; trap 'rm -f "$PIDFILE"' EXIT
sudo -n pkill -9 -x ffmpeg 2>/dev/null; pkill -9 -x ffmpeg 2>/dev/null; sleep 2
if [ "$(pgrep -c -x ffmpeg)" != 0 ]; then log "WARN: an ffmpeg survived the kill"; fi

# 2) make sure the UE host still has ITS OWN address on the camera cable (drops on resets);
#    without it the UE can't reach the camera at 192.168.1.250 at all
if ! ip -4 addr show "$ADAPTER" 2>/dev/null | grep -q '192\.168\.1\.10'; then
  log "re-adding the UE host's address ($UE_ADAPTER_ADDR) on $ADAPTER"
  sudo -n ip addr add "$UE_ADAPTER_ADDR" dev "$ADAPTER" 2>/dev/null
fi

# 3) force UE -> core/edge over 5G
sudo -n sysctl -qw net.ipv4.conf.all.rp_filter=0 net.ipv4.conf.wwan0.rp_filter=0 2>/dev/null
sudo -n ip route replace 192.168.70.128/26 via 12.1.1.1 dev wwan0 2>/dev/null

# 4) wait for the camera to answer
ok=0; for i in $(seq 1 10); do ping -c1 -W1 "$CAM_IP" >/dev/null 2>&1 && { ok=1; break; }; sleep 1; done
[ "$ok" = 1 ] || { log "ERROR: camera $CAM_IP unreachable (check cable/power/adapter)"; exit 1; }
log "camera up; streaming subtype=$SUB to the edge over 5G"

# 5) stream; auto-restart if ffmpeg dies before the deadline
END=$(( $(date +%s) + T ))
while [ "$(date +%s)" -lt "$END" ]; do
  left=$(( END - $(date +%s) )); [ "$left" -lt 1 ] && break
  ffmpeg -hide_banner -loglevel error -rtsp_transport tcp -i "$CAM" \
         -c copy -f mpegts "$EDGE" -t "$left"
  rc=$?; [ "$(date +%s)" -lt "$END" ] && { log "ffmpeg exited (rc=$rc); restarting in 2s"; sleep 2; }
done
log "done"
