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

# 版本号机制：从构建产物读取项目版本，输出带版本的合并固件文件名，
# 并在每次生成新固件后自动删除旧的带版本号合并固件。
version="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["project_version"])' \
    "$out/project_description.json" 2>/dev/null || true)"
if [[ -z "${version}" ]]; then
    version="unknown"
fi
versioned_full="${out}/FoloToy-AI-Passport-v${version}-full.bin"
install -m 0644 "$out/FoloToy-AI-Passport-full.bin" "$versioned_full"
# 删除本次生成之外的所有旧版本化合并固件（保留稳定别名 full.bin）。
find "$out" -maxdepth 1 -type f \
    -name 'FoloToy-AI-Passport-*-full.bin' \
    ! -name "FoloToy-AI-Passport-v${version}-full.bin" \
    ! -name 'FoloToy-AI-Passport-full.bin' -delete
echo "Merged firmware: $versioned_full"
