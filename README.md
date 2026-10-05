# CaneSense: watching crops over a private 5G network

## What this is

A camera watches plants. The picture travels over our own private 5G network (not a phone
company's network) to a computer, and is shown live on a screen. The long-term goal is for the
computer to look at the picture and spot sick sugarcane leaves.

This page describes the part that works today: getting the camera picture across 5G, and a
control page that starts everything in the right order and shows what is happening.

## What works today, and what does not yet

- Works: the live camera picture crossing our private 5G network and showing on the screen.
- Works: a control page to start and stop each part, see its status, and time how long it takes
  to get ready.
- Not yet: the plant-disease AI is not connected to this page. The page shows the live picture
  and the state of the 5G system. It does not show any plant health results yet.

## The pieces, in plain words

| Piece | What it does | Think of it as |
|---|---|---|
| Camera | Takes the picture of the plants | A security camera |
| 5G modem | Sends the camera's picture into the 5G network | The SIM and modem inside a phone |
| Radio unit | The antenna box that talks to the modem over the air | A mobile phone tower |
| DU and CU | Together they run the radio unit and pass data along | The tower's brain, split in two |
| 5G core | Checks the modem is allowed on, then moves data to its destination | The phone company's central office |
| Clock sync (PTP) | Keeps all the parts on exactly the same time, using GPS | A shared wall clock |
| Control page | Starts, stops and monitors everything | A dashboard |

## How the picture travels

    Camera -> 5G modem -> Radio unit -> DU -> CU -> 5G core -> Screen

The network is private and only used for this project. It uses a test SIM and is not connected
to any phone company.

## The control page

Open it in a web browser: http://10.230.4.33:8080

- The top diagram shows the chain above as boxes.
  - Green box: ready.
  - Amber box: starting up.
  - Red box: off or not working.
  - Dashed line: not connected yet.
- The Pipeline list on the right has one row per part, with Start and Stop buttons.
  - A timer counts up while a part starts ("starting 00:12").
  - When it is ready the timer stops and shows how long it took ("ready in 00:30").
- A "Next step" bar at the top of that list says which part to start next, or which one you are
  waiting for.
- The Field view box shows the live camera picture.
- "Start stack" starts the first three parts for you. "Stop stack" shuts everything down.

## Starting it, step by step

Start each part only when the one before it shows green and "ready".

| Step | Part | It is ready when |
|---|---|---|
| 1 | CN5G (the 5G core) | all 9 of its services are healthy |
| 2 | CU | it has registered with the core |
| 3 | PTP (clock sync) | the clocks are locked together |
| 4 | DU | it is connected to the CU and hearing the radio unit |
| 5 | UE reg | the 5G modem has joined the network |
| 6 | Data | the modem has been given a network address |
| 7 | Stream | the camera picture is arriving |

Notes:

- Clock sync usually takes well under a minute. To be safe, give the radio unit about a minute
  to catch up before starting the DU.
- The Start button for the DU stays greyed out until the clocks are locked. The Data button
  stays greyed out until the modem has joined the network.

## If something looks wrong

- A red dot or box: that part is off. Press its Start button, or check the one before it.
- The picture is black: look at the Stream row. It must say "flowing to the edge". If not,
  check that the Data row is green.
- The modem will not join the network: stop the DU, wait for the radio unit to settle, then
  start the DU again and retry.
- A part stays amber for a long time: press its Stop button, wait a few seconds, then Start it
  again.

## Good to know

- The live picture is deliberately small so it stays smooth over 5G. The camera can send a
  sharper picture, but it tends to stutter over 5G, so the control page uses the smaller one.
- The control page does not start by itself after a computer restart. See the notes below.
- This setup is kept separate from our post-quantum security demo. They use different machines
  and do not affect each other.

## For the technical team

Everything below is exact: the paths and commands are what the control page really runs.
Passwords are deliberately not written here. Ask the project owner for access.

### Where everything is

Machines:

| Machine | Address | User | Role |
|---|---|---|---|
| Core and CU computer | 10.230.4.33 | snsrl | 5G core, CU, and the control page (port 8080) |
| DU computer | 10.230.4.26 | snsrl2 | DU and clock sync. It also hosts a separate experiment that this setup leaves alone |
| Modem computer | 10.230.4.39 | snsrl | The 5G modem, and the cable to the camera |
| Radio unit | 192.168.9.20 | - | Antenna unit, reached through the DU computer |
| Camera | 192.168.1.250 | - | Reached through the modem computer |

The control page is at: http://10.230.4.33:8080

Control page files, on 10.230.4.33:

| Path | What it is |
|---|---|
| /home/snsrl/camera_gui/server.py | the backend, the running program |
| /home/snsrl/camera_gui/static/index.html | the page layout and the CaneSense header |
| /home/snsrl/camera_gui/static/style.css | the green theme |
| /home/snsrl/camera_gui/static/app.js | the buttons, timers and next-step bar |
| /home/snsrl/camera_gui/static/mpegts.js | the video player library |
| /home/snsrl/camera_gui/README.md | this file |
| /home/snsrl/camera_gui/show_feed.sh | an optional extra video window on that screen |
| /home/snsrl/camera_gui/f1u_to_ts.py | left over from the earlier packet-tap demo; the control page no longer uses it |

A second copy of these files is on 10.230.4.34 at /home/snsrl/camera_gui/. That is where changes
are edited, and they are then copied to 10.230.4.33. Editing a file on 10.230.4.34 changes nothing
in the control page until it is copied across. That copy does not have show_feed.sh.

To restart the control page by hand, on 10.230.4.33:

    cd /home/snsrl/camera_gui && python3 server.py

It does not start by itself after a reboot.

### How the control page works

The browser opens the page from `server.py`, which answers three calls:

| Call | Used by | What it does |
|---|---|---|
| GET /api/status | the page, every 2 seconds | runs the checks listed further down and returns the state of every card |
| POST /api/action | every Start and Stop button | takes a component name and start or stop, and runs the commands in the button map below |
| GET /feed | the Play feed button | streams the camera video that arrives on UDP port 5004 |

Commands for the core and the CU run on 10.230.4.33 itself. Commands for the DU, the clock sync,
the modem and the camera run over SSH from 10.230.4.33, using the key `~/.ssh/pqc_dashboard_ed25519`
(as snsrl2@10.230.4.26 and snsrl@10.230.4.39). Where root is needed they use sudo without a
password.

### Button map: header buttons

**Start stack**
- Goes to: the page's own script, which calls POST /api/action three times, 2.5 seconds apart.
- Runs: CN5G Start, then CU Start, then PTP Start (each exactly as described below).
- It does not start the DU, UE reg, Data or Stream. Start those one by one, following the Next
  step bar.

**Stop stack**
- Goes to: the page's own script, which calls POST /api/action seven times, 1.2 seconds apart.
- Runs Stop in this order: Stream, Data, UE reg, DU, PTP, CU, CN5G.

### Button map: Pipeline rows

**CN5G** (runs on 10.230.4.33)
- Start: `cd /home/snsrl/disaggregated_core/oai-cn5g-fed/docker-compose && sudo docker compose -f docker-compose-basic-nrf.yaml up -d`
  This starts the 9 core containers: mysql, oai-nrf, oai-udr, oai-udm, oai-ausf, oai-amf,
  oai-smf, oai-upf and oai-ext-dn.
- Stop: the same command with `down` instead of `up -d`.

**CU** (runs on 10.230.4.33)
- Start: if no `nr-softmodem` is already running, it starts
  `sudo /home/snsrl/openairinterface5g/cmake_targets/ran_build/build/nr-softmodem -O /home/snsrl/openairinterface5g/ci-scripts/conf_files/gnb-cu.sa.band78.106prb.conf`
  in the background.
- Log: /home/snsrl/cu_YYYYMMDD_HHMMSS.log on 10.230.4.33.
- Stop: `sudo pkill -INT -x nr-softmodem` (a polite shutdown signal).

**PTP** (runs on 10.230.4.26, over SSH)
- Start: runs three scripts in the background as root, 2 seconds apart, from
  /home/snsrl2/pegatron_oai_setup/scripts/du/:
  1. `ts2phc.sh` runs `ts2phc -f /etc/ts2phc.conf -s nmea -m -q -l 7`. It sets the network card's
     clock from GPS.
  2. `ptp4l.sh` runs `ptp4l -i enp101s0f0np0 -m -2 -f /etc/linuxptp/ptp4l_pegatron.conf`. This is
     the time source the radio unit synchronises to.
  3. `phc2sys.sh` runs `phc2sys -s enp101s0f0np0 -O -37 -m`. It copies the card's clock to the
     computer's clock.
- Logs: /home/snsrl2/ptp_logs/ts2phc.log, ptp4l.log and phc2sys.log on 10.230.4.26.
- Stop: `sudo pkill -x ts2phc`, `sudo pkill -x ptp4l`, `sudo pkill -x phc2sys`.

**DU** (runs on 10.230.4.26, over SSH)
- Start is refused with a "BLOCKED" message unless PTP is locked. When it is allowed:
  1. `sudo bash /home/snsrl2/pegatron_oai_setup/scripts/du/oai_host_tuning.sh` prepares the
     computer (loads vfio-pci, turns off real-time throttling, sets the CPUs to performance mode,
     removes leftover hugepages).
  2. `sudo bash /home/snsrl2/pegatron_oai_setup/scripts/du/vf.sh` creates the fronthaul virtual
     network card with the MAC address 00:11:22:33:44:66 that the radio unit sends to.
  3. It starts
     `sudo /home/snsrl2/openairinterface5g/cmake_targets/ran_build/build/nr-softmodem -O /home/snsrl2/openairinterface5g/ci-scripts/conf_files/gnb-du.sa.band78.273prb.fhi72.pegatron.conf --thread-pool 7,23,7,23,7,23,7,23`
     in the background.
- Log: /home/snsrl2/du_YYYYMMDD_HHMMSS.log on 10.230.4.26.
- Stop: `sudo pkill -9 -x nr-softmodem`.

**UE reg** (runs on 10.230.4.39, over SSH)
- Start: `sudo mmcli -m <modem> --enable`, then
  `bash /home/snsrl/pegatron_oai_setup/scripts/ue/send_at_command_to_modem.sh 'AT+COPS=0'`,
  which tells the modem to choose the network automatically.
- Stop: `sudo mmcli -m <modem> --disable` (modem radio off).

**Data** (runs on 10.230.4.39, over SSH)
- The Start button stays greyed out until UE reg is ready.
- Start: runs `/home/snsrl/pegatron_oai_setup/scripts/ue/2_connect_to_oai_core_and_get_data_plane.sh`
  in the background. It checks the modem and registration, creates a data bearer with the name
  "default", connects it, puts the address 12.1.1.2 on the modem's network port (wwan0) and
  tests it with a ping.
- Log: /tmp/ue_gui.log on 10.230.4.39.
- Stop: runs `/home/snsrl/pegatron_oai_setup/scripts/ue/3_disconnect_and_deregister_ue.sh`. The data
  session drops but the modem stays registered.

**Stream** (runs on 10.230.4.39, over SSH)
- Start: runs `bash /home/snsrl/pegatron_oai_setup/scripts/ue/push_camera_over_5g.sh 3600 1` in the
  background. The 3600 is the run time in seconds and the 1 selects the small picture. The
  script:
  1. stops any older push, including ones owned by root,
  2. makes sure 10.230.4.39 has its own address (192.168.1.10) on the camera cable,
  3. sends traffic for 192.168.70.0/26 out through the 5G modem,
  4. waits for the camera at 192.168.1.250,
  5. runs ffmpeg, which reads the camera's picture and sends it as video over UDP to
     192.168.70.129 port 5004. That address is 10.230.4.33, so the picture crosses the whole
     5G path to get there,
  6. restarts ffmpeg by itself if it stops.
- Log: /tmp/push.log on 10.230.4.39. Its process id is kept in /tmp/push_camera.pid.
- Stop: kills the push script using that process id, then kills ffmpeg.

### Button map: Field view

**Play feed**
- Goes to: the page's video player, which opens GET /feed on 10.230.4.33.
- `server.py` listens on UDP port 5004 on 10.230.4.33, which is where the camera picture
  arrives, and passes it to every open page. Nothing is saved to disk.
- It also copies the stream to 127.0.0.1 port 5005 for the optional `show_feed.sh` window.
- It starts by itself when the page opens.

The Next step bar, the timers and the diagram only display information. They have no buttons.

### What each card checks

| Card | Checked on | How | Ready when |
|---|---|---|---|
| CN5G | 10.230.4.33 | `docker ps` | all 9 containers say "healthy" |
| CU | 10.230.4.33 | `nr-softmodem` is running, and the newest ~/cu_*.log contains "NGSetupResponse" | it has registered with the core |
| PTP | 10.230.4.26 | the three clock programs are running, and the last offsets in ts2phc.log and phc2sys.log | all three run and both offsets are under 1000 ns |
| DU | 10.230.4.26 | `nr-softmodem` is running, and the newest ~/du_*.log contains "received F1 Setup Response" and a receive rate above zero | all three are true |
| UE reg (and the UE box) | 10.230.4.39 | `mmcli -m <modem> -K`, the modem state | "registered" or "connected" |
| Data | 10.230.4.39 | the address on wwan0 | it starts with 12.1.1. |
| Stream | 10.230.4.39 and 10.230.4.33 | ffmpeg is running on 10.230.4.39, and video was seen on UDP 5004 in the last 4 seconds | both are true |
| O-RU box | 10.230.4.26 | ping to 192.168.9.20 | it answers (colour only, no button) |
| Camera box | 10.230.4.39 | ping to 192.168.1.250 | it answers (colour only, no button) |

A card's timer starts when its Start button succeeds. The "ready in" time is the first moment the
page saw the card ready, so it can be up to 2 seconds late.

### Where to look when something fails

| Problem | Look at |
|---|---|
| CU will not register | /home/snsrl/cu_*.log on 10.230.4.33 (newest file) |
| DU will not come up | /home/snsrl2/du_*.log on 10.230.4.26 (newest file) |
| Clocks will not lock | /home/snsrl2/ptp_logs/ on 10.230.4.26 |
| Data session fails | /tmp/ue_gui.log on 10.230.4.39 |
| Picture does not arrive | /tmp/push.log on 10.230.4.39 |
| The control page itself | /tmp/camera_gui.log on 10.230.4.33, if it was started in the background that way |

### About this repository

- `gui/` is the control page. On the core and CU computer it lives at `/home/snsrl/camera_gui/`
  (`server.py` and the `static/` folder).
- `scripts/push_camera_over_5g.sh` runs on the modem computer. The camera password is not stored
  in it. Set it first, for example `export CAM_PASS='the-camera-password'`. Write `@` as `%40`.
- The other scripts the buttons call (clock sync, DU preparation, modem connect) belong to the
  main 5G setup and are not part of this repository.
