#!/usr/bin/env bash
#
# manage_services.sh — Start, stop and inspect the GUI label tool services.
#
# Each service is a Python module launched in the background. Multiple
# configurations can run side by side: the config file name is turned into an
# instance id (e.g. config.ubuntu.v1.yml -> ubuntu_v1) and every instance keeps
# its PID and log files in its own directory under run/.
#
# Usage:
#   ./scripts/manage_services.sh start   config/config.ubuntu.v1.yml
#   ./scripts/manage_services.sh stop    config/config.ubuntu.v1.yml
#   ./scripts/manage_services.sh restart config/config.ubuntu.v1.yml
#   ./scripts/manage_services.sh status  config/config.ubuntu.v1.yml

set -euo pipefail

# Repository root, resolved relative to this script's location.
BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly BASE_DIR

# Seconds to wait for a graceful shutdown before sending SIGKILL.
# vm.manager stops Docker containers on shutdown, which can take a dozen
# seconds; killing too early would leave orphaned containers behind.
readonly GRACEFUL_TIMEOUT=15

# Services to manage, as "python.module:log_file_name" pairs.
readonly SERVICES=(
  "gui_label_tool.frontend.app:frontend.log"
  "gui_label_tool.annotation.service:annotation.log"
  "gui_label_tool.vm.service:vm.log"
  "gui_label_tool.qemu_log.service:qemu_log.log"
)

# Populated by resolve_config() once the config argument is validated.
CONFIG_PATH=""
INSTANCE_ID=""
PID_DIR=""
LOG_DIR=""

usage() {
  cat <<EOF
Usage: $0 {start|stop|restart|status} <path/to/config.yml>

Commands:
  start     Launch every service for the given config.
  stop      Gracefully stop every service for the given config.
  restart   Stop then start every service for the given config.
  status    Show the running state of every service for the given config.

The config path may be absolute or relative to the repository root.
EOF
}

# Resolve the config argument into absolute paths and derive the instance id
# along with its dedicated PID and log directories.
resolve_config() {
  local config_arg="$1"

  if [[ "$config_arg" = /* ]]; then
    CONFIG_PATH="$config_arg"
  else
    CONFIG_PATH="$BASE_DIR/$config_arg"
  fi

  if [[ ! -f "$CONFIG_PATH" ]]; then
    echo "❌ Config file not found: $CONFIG_PATH" >&2
    exit 1
  fi

  # config.ubuntu.v1.yml -> ubuntu_v1
  local basename
  basename="$(basename "$CONFIG_PATH")"
  INSTANCE_ID="$(echo "$basename" | sed 's/config\.\(.*\)\.yml/\1/')"

  PID_DIR="$BASE_DIR/run/pids/$INSTANCE_ID"
  LOG_DIR="$BASE_DIR/run/logs/$INSTANCE_ID"

  echo "Using config:  $CONFIG_PATH"
  echo "Instance id:   $INSTANCE_ID"
  echo ""
}

# Derive a filesystem-safe PID file name from a module name.
pid_file_for() {
  local module_name="$1"
  echo "$PID_DIR/$(echo "$module_name" | tr '/.' '_').pid"
}

# Read the PID stored in a file, or nothing if the file is absent/empty.
# Must succeed (exit 0) even when the file is missing: callers assign its
# output via command substitution under `set -e`, so a non-zero return would
# abort the whole script on a fresh start (no PID files yet).
read_pid() {
  local pid_file="$1"
  if [[ -f "$pid_file" ]]; then
    cat "$pid_file"
  fi
}

# Return success if the given PID refers to a live process.
is_running() {
  local pid="$1"
  [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

start_services() {
  echo "Starting all services [$INSTANCE_ID]..."
  echo "-------------------------"

  mkdir -p "$PID_DIR" "$LOG_DIR"

  local service_info module_name log_file_name pid_file log_file pid existing_pid
  for service_info in "${SERVICES[@]}"; do
    module_name="${service_info%%:*}"
    log_file_name="${service_info#*:}"
    pid_file="$(pid_file_for "$module_name")"
    log_file="$LOG_DIR/$log_file_name"

    existing_pid="$(read_pid "$pid_file")"
    if is_running "$existing_pid"; then
      echo "🟡 [skip] $module_name already running (PID: $existing_pid)."
      continue
    elif [[ -n "$existing_pid" ]]; then
      echo "🧹 [clean] Removing stale PID file: $pid_file"
      rm -f "$pid_file"
    fi

    echo "🚀 [start] $module_name..."
    OSWORLD_CONFIG_PATH="$CONFIG_PATH" \
    nohup uv run --project "$BASE_DIR" python -m "$module_name" > "$log_file" 2>&1 &

    pid=$!
    echo "$pid" > "$pid_file"
    echo "   ✅ Started, PID: $pid. Log: $log_file"
  done

  echo "-------------------------"
  echo "🎉 All services started [$INSTANCE_ID]."
  echo "💡 Stop:   $0 stop $CONFIG_PATH"
  echo "💡 Status: $0 status $CONFIG_PATH"
}

stop_services() {
  echo "Stopping all services [$INSTANCE_ID]..."
  echo "-----------------------"

  if [[ ! -d "$PID_DIR" ]]; then
    echo "🤷 PID directory '$PID_DIR' not found. Services are probably not running."
    return
  fi

  local service_info module_name pid_file pid waited
  for service_info in "${SERVICES[@]}"; do
    module_name="${service_info%%:*}"
    pid_file="$(pid_file_for "$module_name")"
    pid="$(read_pid "$pid_file")"

    if [[ -z "$pid" ]]; then
      echo "🔵 [skip] $module_name not running (no PID file)."
      continue
    fi

    if ! is_running "$pid"; then
      echo "🧹 [clean] $module_name not running but PID file exists, removing."
      rm -f "$pid_file"
      continue
    fi

    echo "🛑 [stop] $module_name (PID: $pid)..."
    kill "$pid" 2>/dev/null || true

    # Poll for a graceful exit before forcing it.
    waited=0
    while [[ "$waited" -lt "$GRACEFUL_TIMEOUT" ]] && is_running "$pid"; do
      sleep 1
      waited=$((waited + 1))
    done

    if is_running "$pid"; then
      echo "   ⚠️ Did not exit within ${GRACEFUL_TIMEOUT}s, forcing termination..."
      kill -9 "$pid" 2>/dev/null || true
    fi

    rm -f "$pid_file"
    echo "   ✅ Stopped."
  done

  # Drop the PID directory once it is empty.
  if [[ -d "$PID_DIR" && -z "$(ls -A "$PID_DIR")" ]]; then
    rmdir "$PID_DIR"
  fi

  echo "-----------------------"
  echo "👋 All services stopped [$INSTANCE_ID]."
}

status_services() {
  echo "Service status [$INSTANCE_ID]"
  echo "-----------------------"

  if [[ ! -d "$PID_DIR" ]]; then
    echo "❌ This instance has never been started (PID directory missing)."
    return
  fi

  local service_info module_name pid_file pid found_any=0
  for service_info in "${SERVICES[@]}"; do
    module_name="${service_info%%:*}"
    pid_file="$(pid_file_for "$module_name")"
    pid="$(read_pid "$pid_file")"

    if [[ -z "$pid" ]]; then
      echo "⭕ $module_name (not started)"
    elif is_running "$pid"; then
      echo "✅ $module_name (PID: $pid)"
      found_any=1
    else
      echo "❌ $module_name (PID: $pid - stopped)"
    fi
  done

  if [[ "$found_any" -eq 0 ]]; then
    echo "No running services."
  fi

  echo "-----------------------"
}

main() {
  local command="${1-}"
  local config_arg="${2-}"

  case "$command" in
    start|stop|restart|status) ;;
    -h|--help|help)
      usage
      exit 0
      ;;
    "")
      echo "❌ Missing command." >&2
      usage >&2
      exit 1
      ;;
    *)
      echo "❌ Unknown command: $command" >&2
      usage >&2
      exit 1
      ;;
  esac

  if [[ -z "$config_arg" ]]; then
    echo "❌ A config file is required." >&2
    usage >&2
    exit 1
  fi

  resolve_config "$config_arg"

  case "$command" in
    start)   start_services ;;
    stop)    stop_services ;;
    restart) stop_services; echo ""; start_services ;;
    status)  status_services ;;
  esac
}

main "$@"
