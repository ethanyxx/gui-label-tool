# GUI Label Tool

Record and annotate GUI interaction trajectories on a virtual desktop. A human
operates a desktop running inside a QEMU virtual machine (in Docker); the tool
captures every input action, pairs each one with the screenshot taken just
before it, and stores a reviewable, exportable trajectory.

## How it works

The backend is a four-stage pipeline plus a frontend, each a small package under
`gui_label_tool/`:

```
vm        → boots/stops the QEMU virtual machine (Docker container) being labeled
qemu_log  → tails QEMU's input-event trace and merges it into semantic actions
annotation→ pairs each action with a pre-action screenshot and stores it
frontend  → Gradio control console: drive a session, then review/prune/export
```

Inside the VM, `vm_service/recorder.py` serves screenshots over HTTP (it
is mounted into the guest via the shared folder). The `Event` model in
`gui_label_tool/qemu_log/parser.py` is the data contract that flows
`qemu_log → annotation → frontend`.

## Architecture

Four FastAPI/Gradio services run on the host (ports from `config.yml`). The
frontend orchestrates a session; events and screenshots flow one way into the
annotation store.

```
                                 Browser
                  ┌──────────────────┴──────────────────┐
                  │ HTTP control                          │ noVNC (VNC view)
                  ▼                                       │
        ┌──────────────────────┐                          │
        │  frontend  (Gradio)   │  :8810                   │
        │  drive · poll · review│                          │
        └───┬────────┬───────┬──┘                          │
   start/   │        │       │  poll /events,              │
   stop ────┘  start/│       │  exclude, finalize          │
            container│tail   │                             │
                 ▼   │       ▼                             │
        ┌──────────┐ │ ┌──────────────┐                    │
        │   vm     │ │ │  annotation  │  :8012              │
        │  :8013   │ │ │  store + pair│──► data/annotations │
        └────┬─────┘ │ └──────▲───────┘   (json + pngs)     │
   docker run│       │        │ POST /annotation/event      │
             │   ┌───┴──────┐ │ + GET /screenshot per event │
             │   │ qemu_log │─┘                             │
             │   │  :8011   │ tails the input-event trace   │
             │   └────▲─────┘                               │
             ▼        │ tail data/qemu_logs/*.log           │
   ┌─────────────────────────────────────────────┐         │
   │      QEMU VM container  (Docker + KVM)        │◄────────┘
   │                                               │  VNC :8007
   │   desktop (X11) ──input_event trace──► /qemu_logs (→ host)
   │   recorder.py  screenshot server  :5001       │
   └───────────────────────────────────────────────┘
```

Flow during a recording session:

1. **frontend** → **vm**: `docker run` the QEMU container (VNC + `recorder.py`
   screenshot server inside it); the VNC view is embedded back in the browser.
2. The guest desktop's input events are traced by QEMU into a log file mounted
   onto the host (`data/qemu_logs/`).
3. **qemu_log** tails that log, decodes + merges raw events into semantic actions
   (`Event`), and POSTs each to **annotation** `/annotation/event`.
4. **annotation** fetches a pre-action screenshot from `recorder.py` (`:5001`),
   pairs it with the action, and persists the trajectory to `data/annotations/`.
5. **frontend** polls `/annotation/events` to render the live trajectory, where
   the operator can exclude events and then finalize (which stops the container).

## Requirements

- Python 3.12+ (uv installs a managed interpreter for you)
- Docker with KVM (`/dev/kvm`) for the QEMU desktop container
- A bootable disk image (`.qcow2`) for the guest OS

## Install

This project uses [uv](https://docs.astral.sh/uv/). `uv sync` creates a local
`.venv` and installs the project plus the `dev` tools from the lockfile:

```bash
uv sync               # runtime deps + dev tools (ruff, black)
uv sync --no-dev      # runtime deps only
```

## Configure

Copy the example config and edit it for your setup:

```bash
cp config/config.example.yml config/config.mine.yml
```

Key fields:

- `runtime.target_os` — `ubuntu` or `windows`
- `ports` — host ports for the four services (`qemu_log` / `annotation` / `vm` / `frontend`)
- `vm` — the disk image, the shared folder, container ports and resources
- `annotation.storage_dir` — where trajectories are written (defaults under `data/`)

## Run

Bring up all four services for a config (PID/log management; multiple configs can
run side by side):

```bash
./scripts/manage_services.sh start   config/config.mine.yml
./scripts/manage_services.sh status  config/config.mine.yml
./scripts/manage_services.sh stop    config/config.mine.yml
```

Then open the frontend at `http://<host>:<ports.frontend>` (8810 in the example).

To run a single service in the foreground (handy for debugging):

```bash
uv run python -m gui_label_tool.vm.service
uv run python -m gui_label_tool.qemu_log.service
uv run python -m gui_label_tool.annotation.service
uv run python -m gui_label_tool.frontend.app
```

## Layout

```
gui_label_tool/   backend packages (vm, qemu_log, annotation, frontend) + config.py
vm_service/       code mounted into the VM and run inside it (screenshot server)
config/           YAML config files
imgs/             input disk images
data/             produced trajectories (annotations, recorded qemu_logs)
run/              process runtime state (pids, service logs)
scripts/          manage_services.sh
```

## Develop

```bash
uv run ruff check gui_label_tool
```

## License

MIT. See [CHANGELOG.md](CHANGELOG.md) for version history.
