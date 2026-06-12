#!/usr/bin/env bash
# swap memory cleanup

dry_run=0
[ "$1" = "--dry-run" ] && dry_run=1

PROTECTED="Xorg gnome-shell bitcoin-qt tor i2pd"

run_privileged() {
  if [ "$(id -u)" -eq 0 ]; then
    "$@"
  else
    sudo "$@"
  fi
}

# --- 1. DIAGNOSE ---
echo "=== MEMORY STATE ==="
free -h
echo ""

mem_avail_kb=$(awk '/MemAvailable/ {print $2}' /proc/meminfo)
swap_total_kb=$(awk '/SwapTotal/ {print $2}' /proc/meminfo)
swap_free_kb=$(awk '/SwapFree/ {print $2}' /proc/meminfo)
swap_used_kb=$(( swap_total_kb - swap_free_kb ))

echo "MemAvailable: $(( mem_avail_kb / 1024 )) MB"
echo "SwapUsed:     $(( swap_used_kb / 1024 )) MB"
echo ""

# --- 2. FIND SWAP HOGS ---
echo "=== TOP SWAP HOGS ==="
hog_list=$(
  for pid in $(ls /proc | grep '^[0-9]'); do
    swap=$(grep -s VmSwap /proc/$pid/status 2>/dev/null | awk '{print $2}')
    if [ -n "$swap" ] && [ "$swap" -gt 0 ] 2>/dev/null; then
      name=$(cat /proc/$pid/comm 2>/dev/null)
      echo "$swap $pid $name"
    fi
  done | sort -rn | head -20
)
if [ -n "$hog_list" ]; then
  echo "KB        PID  NAME"
  echo "$hog_list"
else
  echo "No processes found in swap."
fi
echo ""

if [ "$dry_run" -eq 1 ]; then
  echo "=== DRY RUN — no changes made ==="
  exit 0
fi

if [ "$(id -u)" -ne 0 ]; then
  echo "=== AUTHORIZING SUDO ==="
  sudo -v || exit 1
  echo ""
fi

# --- 3. DROP CACHES ---
echo "=== DROPPING CACHES ==="
sync && run_privileged sysctl -w vm.drop_caches=3
echo ""

# Re-read MemAvailable after cache drop
mem_avail_kb=$(awk '/MemAvailable/ {print $2}' /proc/meminfo)
echo "MemAvailable after cache drop: $(( mem_avail_kb / 1024 )) MB"
echo ""

# --- 4. SAFETY GATE: flush swap only if RAM can absorb it ---
if [ "$mem_avail_kb" -gt "$swap_used_kb" ]; then
  echo "=== FLUSHING SWAP (safe: MemAvailable > SwapUsed) ==="
  run_privileged swapoff -a && run_privileged swapon -a
  echo "Swap flushed."
else
  echo "=== SWAP FLUSH SKIPPED ==="
  echo "Not enough free RAM to absorb swap ($(( mem_avail_kb / 1024 )) MB available < $(( swap_used_kb / 1024 )) MB in swap)."
  echo ""
  echo "Consider killing these processes first (avoid: $PROTECTED):"
  echo "$hog_list"
fi
echo ""

# --- 5. COMPACT MEMORY ---
echo "=== COMPACTING MEMORY ==="
run_privileged sysctl -w vm.compact_memory=1
echo ""

# --- 6. FINAL STATE ---
echo "=== FINAL MEMORY STATE ==="
free -h
