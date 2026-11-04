#!/usr/bin/env bash
set -euo pipefail
app_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
out="${1:-${app_root}/build}"
mkdir -p "$out"
out="$(cd "$out" && pwd)"
command -v idf.py >/dev/null || { echo 'Activate ESP-IDF 5.5.3 first.' >&2; exit 1; }
idf.py -C "$app_root/firmware" -B "$out" \
    -D "SDKCONFIG=$out/sdkconfig" -D "SDKCONFIG_DEFAULTS=$app_root/firmware/sdkconfig.defaults" build
idf.py -C "$app_root/firmware" -B "$out" merge-bin -o "$out/FoloToy-AI-Passport-full.bin"
python3 "$app_root/tools/verify_firmware.py" "$out"
python3 "$app_root/tools/archive_firmware.py" create "$out" --archive-root "$app_root/dist/firmware"
