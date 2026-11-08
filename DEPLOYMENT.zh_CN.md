# Passport TV 服务端 · 部署与使用说明

> 本文档记录 Passport TV 电视服务端的部署、启动/使用方法、固件版本管理与经验，便于后续复现和维护。
> **维护约定**：后续每次部署 / 排障 / 迭代后，由 AI **自动**将结论、命令、路径、坑与解决办法整理进本文件对应章节，无需再询问用户。

## 路径约定（可移植）

本文档中的路径**均为相对路径**，以「项目根」为基准，保证换开发机可移植：

- `<仓库根>`：克隆 `guolifu/ai-passport-tv` 仓库的目录（含 `reference/`、`baseline/`）。
- `<项目根>`：`<仓库根>/reference`（TV 服务端与固件所在）。
- `<ESP-IDF 路径>`：ESP-IDF 5.5.3 的安装目录，换机后按实际路径修改（常见 `~/esp/esp-idf`）。
- `<串口>`：设备串口，Linux 常见 `/dev/ttyACM0`，macOS 常见 `/dev/cu.usbmodem...`。
- `~`：当前用户的 home 目录。

例如，本机部署时 `<项目根>` = `/home/billyoung/agent-projects/ai-passport-tv/reference`；换到其它机器后，把 `<项目根>` 替换为该机器上 `reference/` 的实际路径即可，其余命令不变。

## 1. 部署概况

| 项目 | 内容 |
| --- | --- |
| 部署日期 | 2026-11-07 |
| 操作系统 | 示例：Fedora Linux 45 (Server Edition), x86_64 |
| Python | 系统 Python 3.10+（本机为 3.15.0） |
| FFmpeg | 全局安装 `ffmpeg`/`ffprobe` 且在 PATH（或把其所在目录加入 PATH） |
| 服务端代码 | `<项目根>/service/`（Python TV 服务，来自上游 `guolifu/ai-passport-tv`） |
| 服务端口 | TCP `10745`（默认，可用 `server.env` 的 `TV_PORT` 修改） |
| 监听地址 | `0.0.0.0`（`server.env` 的 `TV_BIND`） |
| 本机局域网 IP | `<本机局域网 IP>`（用 `hostname -I` 查看；设备需填写该地址） |
| 配对码 | 见 `<项目根>/pairing.code`，可用 `<项目根>/start-server.sh --show-pairing` 查看 |
| 开机自启 | systemd 用户服务 `passport-tv.service`（已启用 + linger，随系统开机自动后台运行） |
| 频道数 | 16 个 CCTV 频道（CCTV1~15、CCTV17；CCTV16 源暂不可用已跳过） |
| 视频方向 | **竖屏默认**（服务端 `TV_GEOMETRY=240x320`，固件竖屏几何） |
| 固件版本 | `<项目根>/build/FoloToy-AI-Passport-v<版本>-full.bin`（含版本号命名，见第 8 节；当前 v1.0.7） |
| 防火墙 | `firewalld` 需放行 TCP `10745`（`sudo firewall-cmd --permanent --add-port=10745/tcp`） |

## 2. 已完成的部署内容

1. **复用全局 FFmpeg**：确保 `ffmpeg`/`ffprobe` 在 PATH 中（本机通过把全局 FFmpeg 所在目录符号链接到 `~/.local/bin/` 实现）。无需重新下载，也不改动系统包。
2. **生成服务端配置**（运行 `<项目根>/tools/run_server.py --setup-only` 自动生成，均在 `<项目根>/` 下，已被 `.gitignore` 忽略）：
   - `server.env`：服务配置（`TV_BIND=0.0.0.0`、`TV_PORT=10745` 等）
   - `channels.txt`：频道列表（默认含 CCTV1 示例频道）
   - `pairing.code`：4 位配对码（当前为 `DPJQ`）
   - `pairing.token`：内部签名密钥（勿公开）
   - `paired-devices.json`：设备绑定记录（首次配对后生成）
   - `.venv/`：独立 Python 虚拟环境，已安装依赖 `numpy 2.5.4`
3. **校验通过**：`<项目根>/start-server.sh --check` 输出 `PASS: Python / FFmpeg / channels / pairing / server configuration`。
4. **服务端启动验证**：服务监听 `0.0.0.0:10745`，当前日志 `FoloTalk TV listening on 0.0.0.0:10745 max_sessions=2 channels=16`。
5. **配置开机自启**：创建 systemd 用户服务 `~/.config/systemd/user/passport-tv.service`，已 `enable`；用户 `linger` 已开启（`Linger=yes`），服务随系统开机在后台自动运行，无需登录。
6. **放行防火墙**：配对失败根因是 `firewalld` 未放行 TCP `10745`；需 `sudo firewall-cmd --permanent --add-port=10745/tcp && sudo firewall-cmd --reload`（需用户执行，因为需要 root）。
7. **扩展频道**：`<项目根>/channels.txt` 现含 16 个可用 CCTV 频道（CCTV1~15、CCTV17），服务端日志 `channels=16`。
8. **固件版本管理**：在 `<项目根>/tools/` 加入版本化输出（`build.sh` 读取 `project_version` 生成 `FoloToy-AI-Passport-v<版本>-full.bin` 并删除旧版）与 `bump_version.py`（版本递增规则：1.0.0 起，每一位到 10 进位）。
9. **竖屏默认**：服务端 `TV_GEOMETRY=240x320`（ffmpeg `transpose` 旋转视频为竖屏）；固件几何/渲染/BSP 改为竖屏 240x320。
10. **修复热点搜不到**：`configure()` 原在开启热点时立即触发 STA 扫描，APSTA 模式下扫描会切换信道、打断 softAP 信标，导致其他设备搜不到热点；改为等用户在配置页点击「扫描」时再触发。
11. **修复电池恒 0%**：`bsp_battery.c` 原误用「优特利 500mAh」profile；改为与 FoloToy/ai-passport 参考固件一致的「万锂新能源 500mAh profile2 XIL40X 20261020」vendor profile，并恢复初始化日志（`CW2017 ready: SOC=... voltage=... mV`）。
12. **⚠️ v1.0.6 开机无限重启（已回退）**：为修「配网页搜不到 SSID」，v1.0.6 在 `configure()` 启动路径加了 `passport_wifi_scan_sync()`（在 launcher 任务栈 4096 内执行 `esp_wifi_start()`+阻塞扫描），导致开机无限闪屏/重启。**已回退该预扫描**，v1.0.7 恢复可开机。配网页仍依赖「手动输入」SSID 兜底（`setup.html` 已有）；APSTA 模式下 `esp_wifi_scan_start` 会超时（`ESP_ERR_WIFI_TIMEOUT`）导致扫描列表为空，需真机另行设计安全的扫描方案（如独立任务+更大栈）。

## 3. 如何启动 / 停止服务端

### 开机自启（systemd 用户服务，已配置，推荐）
服务端已注册为 systemd 用户服务 `passport-tv.service`，并已启用 + 开启 `linger`，**随本机系统开机自动在后台运行**，无需登录。

```sh
systemctl --user status  passport-tv.service          # 查看状态
systemctl --user start   passport-tv.service          # 启动
systemctl --user stop    passport-tv.service          # 停止
systemctl --user restart passport-tv.service          # 重启（改配置后）
journalctl --user -u passport-tv.service -f           # 实时查看日志
systemctl --user is-enabled passport-tv.service       # 确认是否开机自启
```

- **取消开机自启**：`systemctl --user disable passport-tv.service`（再 `stop` 停止当前运行）。
- 手动 `nohup` 后台方式已不必要；日常管理请用 `systemctl --user`。

### 启动（前台，调试用）
在 `<项目根>` 下执行：

```sh
cd <项目根>
./start-server.sh
```

- 首次启动会自动创建虚拟环境、生成配置；之后直接启动并保持前台运行。
- 保持终端打开，按 `Ctrl+C` 停止播放/服务。
- 服务端会打印本机 IP 与端口，供设备填写。
- 若已通过 systemd 运行，请先 `systemctl --user stop passport-tv.service` 释放端口。

> ⚠️ **不要**直接用 `python -m server.standalone` 启动：若不设置 `TV_CHANNELS_FILE`，服务端会回退到**内置默认频道**（而非你的 `channels.txt`）。

### 停止（systemd 方式）
```sh
systemctl --user stop passport-tv.service
```
旧的手动后台进程可用 `pkill -f "[s]erver.standalone"` 兜底清理。

### 常用辅助命令
```sh
./start-server.sh --check          # 只校验环境与配置，不启动
./start-server.sh --setup-only     # 只生成配置/安装依赖，不启动
./start-server.sh --show-pairing   # 显示配对码
```

## 4. 设备如何连接

1. 确保电脑（本机）与服务端已启动，且手机/设备与电脑在同一局域网。
2. 在 AI Passport 设备上进入电视连接设置，填写：
   - **电视服务器**：`<本机局域网 IP>`（用 `hostname -I` 查看）
   - **端口**：`10745`
   - **播放配对码**：运行 `<项目根>/start-server.sh --show-pairing` 查看（见 `<项目根>/pairing.code`）
3. 保存后设备退出热点并开始播放；重启后会自动重连。
4. **必须放行防火墙**：本机 `firewalld` 默认拦截 TCP `10745`，会导致配对一直「未完成」。请执行：
   ```sh
   sudo firewall-cmd --permanent --add-port=10745/tcp
   sudo firewall-cmd --reload
   ```
   放行后设备重新配对即可成功。

## 5. 如何配置频道

编辑 `<项目根>/channels.txt`，格式为：

```text
# id | 显示名 | HTTP/HTTPS 直播源 URL [| 可选 User-Agent]
ch000 | CCTV1 | http://101.35.240.114:88/live.php?id=CCTV1 | AptvPlayer-UA
```

- `id`：唯一，不含空格，≤15 字节可打印 ASCII。
- 显示名可含中文。
- 若直播源需要特定 User-Agent，追加在第四字段。
- 修改后**重启服务端**生效。前 40 个频道会下发到设备，第一个为默认频道。
- 示例 `channels.example.txt` 提供 CCTV1 条目（含所需 `AptvPlayer-UA`），可参考替换。
- **当前频道**：`channels.txt` 含 16 个 CCTV（CCTV1~15、CCTV17），源模式为 `http://101.35.240.114:88/live.php?id=CCTV<序号>`，第四字段 `AptvPlayer-UA`。CCTV16 源当前不可用已跳过。频道源可用 `ffprobe -user_agent "AptvPlayer-UA" <url>` 验证。
- 增加频道后重启服务端：`systemctl --user restart passport-tv.service`；设备重新进电视拉取新列表。

## 6. 关键配置项（`<项目根>/server.env`）

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `TV_BIND` | `0.0.0.0` | 监听地址；仅本机调试可改 `127.0.0.1` |
| `TV_PORT` | `10745` | 设备连接的 TCP 端口 |
| `TV_FPS` | `30` | 超清目标帧率（设备可选 8/15/30） |
| `TV_ADAPTIVE` | `0` | 自适应降预算开关 |
| `TV_RATE_START/MIN/MAX` | 各 `1000000` | 发送预算（字节/秒） |
| `TV_PACKET_TARGET` | `12288` | 媒体协议包目标大小 |
| `TV_ENCODE` | `full` | 编码路径 |
| `TV_TCP_PACING_RATE` | Linux `1100000` | Linux 内核发包节奏 |
| `TV_WRITE_PACING_RATE` | `1000000` | 应用层平滑发送 |
| `TV_MAX_SESSIONS` | `2` | 总并发连接上限 |
| `TV_MAX_PER_ADDRESS` | `2` | 同来源 IP 连接上限 |
| `TV_SHARED_LIVE` | `1` | 同频道同档位共享转码（当前设为 `0`，每会话独立编码） |
| `TV_GEOMETRY` | `320x180` | 服务端下发视频几何；**当前设为 `240x320`（竖屏）**，配合固件竖屏几何 |

修改 `server.env` 后需重启服务端：`systemctl --user restart passport-tv.service`。

## 7. 经验与注意事项（落盘）

- **FFmpeg 复用**：确保 `ffmpeg`/`ffprobe` 在 PATH 中（例如把全局 FFmpeg 所在目录符号链接到 `~/.local/bin`），启动器用 `shutil.which('ffmpeg'/'ffprobe')` 找到它们，避免重复下载大体积静态包。
- **网络较慢时的依赖安装**：首次 `pip install numpy` 从 pypi.org 直连较慢（可配置国内镜像加速，如清华 PyPI：`pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple`）。本机最终通过直连完成（numpy 2.5.4）。
- **Python 版本**：服务端要求 3.10+；本机 3.15.0 满足，numpy 已提供对应 cp315 wheel。
- **无需 ESP-IDF**：运行服务端不需要 ESP-IDF，只需 Python + FFmpeg + ffprobe。
- **敏感文件**：`pairing.token`、`paired-devices.json` 不要公开；迁移服务时需一并保留。
- **配对码**：默认永久有效、可重复使用；换码只阻止新绑定，不影响已有绑定。
- **必须用启动器启动**：直接运行 `python -m server.standalone` 时不会设置 `TV_CHANNELS_FILE` 环境变量，服务端会加载内置默认频道而不是 `channels.txt`。请始终使用 `<项目根>/start-server.sh`（后台运行见上文）。
- **重启后地址变化**：建议给电脑配置固定 DHCP 地址，否则设备需更新服务器 IP。
- **防火墙**：远程/跨网段连接需放行 TCP `10745`；当前协议为明文，公网使用建议配 VPN 或 HTTPS 网关。
- **电池恒 0%（已修复）**：根因是 `bsp_battery.c` 用了错误的电池 profile。已改为与参考固件一致的「万锂新能源 500mAh profile2 XIL40X 20261020」vendor 数据。**烧录当前固件（v1.0.7）后需真机确认**：启动串口日志应打印 `CW2017 ready: SOC=...%, voltage=... mV`，电池电量应显示真实百分比而非 0%。若仍为 0%，再检查电池是否未接/已耗尽。
- **配网页搜不到 SSID（待安全修复）**：根因是 APSTA 模式下 `esp_wifi_scan_start` 超时导致扫描列表为空。曾尝试「开热点前预扫描」（v1.0.6），但导致开机无限重启，已回退。**当前可靠做法是配置页「手动输入」SSID**（`setup.html` 已支持）。后续如需扫描列表，应改用独立任务（更大栈）执行扫描并避免阻塞 launcher，且需真机验证。
- **热点可被发现（已修复）**：开热点时不再自动 STA 扫描（避免打断 softAP 信标），热点可被稳定搜索到。

## 8. 固件版本管理

- **版本来源**：`<项目根>/firmware/CMakeLists.txt` 的 `project(FoloToy-AI-Passport VERSION X.Y.Z)`。
- **版本化输出**：`<项目根>/tools/build.sh` 读取构建产物 `project_version`，生成 `<项目根>/build/FoloToy-AI-Passport-v<版本>-full.bin`，并**自动删除其它旧版本化固件**（保留稳定别名 `FoloToy-AI-Passport-full.bin`）。
- **版本递增**：`python3 <项目根>/tools/bump_version.py`（初始 `1.0.0`；每一位到 10 进位，如 `1.0.9→1.1.0`、`9.9.9→10.0.0`）。常用：`--current` 查看、`--dry-run` 预览、无参递增。
- **升级流程**：
  ```sh
  cd <项目根>
  python3 tools/bump_version.py            # 递增版本
  source <ESP-IDF 路径>/export.sh
  ./tools/build.sh                         # 生成新版并删除旧版
  ```
- **当前固件**：`<项目根>/build/FoloToy-AI-Passport-v1.0.7-full.bin`。

## 9. 竖屏默认（显示方向）

- **物理屏幕**：ST7789P3 240x320（纵向）。
- **服务端**：`<项目根>/server.env` 设 `TV_GEOMETRY=240x320`；`<项目根>/service/server/frames.py` 的 `FIT` 在几何为竖屏时自动 `transpose=1`，把横向源旋转并 pad 成 240x320 下发。
- **固件**：`<项目根>/firmware/components/passport_tv/av_protocol.h` 几何宏为竖屏 240x320（画布/视频同尺寸，16 行条带）；`<项目根>/firmware/components/bsp/src/bsp_display.c` raw 模式用竖屏方向；`<项目根>/firmware/main/passport_tv_main.c` 的 `screen()` 开机/暂停页为竖屏布局。
- **菜单**：控制页为 2 项（频道/清晰度）。
- **已知限制**：固件几何是**编译期固定**的，无法在运行中切换横/竖视频。曾尝试加「显示模式」菜单并做服务端 per-session 方向，但固件 CONFIG 校验写死竖屏几何，横向会导致连接被拒，因此**已移除该菜单**，固件固定竖屏。

## 10. 项目来源

- 上游仓库：`https://github.com/guolifu/ai-passport-tv`
- 服务端代码位于 `<项目根>/service/`；启动器为 `<项目根>/tools/run_server.py`，入口脚本 `<项目根>/start-server.sh`。
- 服务端依赖：`numpy`（用于画面劣化处理；`ladder` 等部分模式仅依赖标准库）。

## 11. 卸载与清理方法

> 用于彻底移除本机部署、或遇到问题时干净地重置，避免留下「坑」。命令均在 `<项目根>` 下执行。

### 11.1 停止服务端

- 已配置 systemd 服务：`systemctl --user stop passport-tv.service`。
- 前台调试进程：按 `Ctrl+C`。
- 旧手动后台进程兜底：`pkill -f "[s]erver.standalone"`。
- 确认已停止：
  ```sh
  systemctl --user is-active passport-tv.service   # inactive = 已停止
  ss -tln | grep 10745                             # 无输出 = 端口已释放
  ```

### 11.2 停用并移除开机自启（systemd 用户服务）

```sh
systemctl --user disable passport-tv.service   # 取消开机自启
systemctl --user stop    passport-tv.service   # 停止当前运行
rm -f ~/.config/systemd/user/passport-tv.service
systemctl --user daemon-reload
```

### 11.3 备份（如需保留设备绑定 / 频道）

若将来要恢复或迁移，先备份以下文件（它们无法从源码重建）：

```sh
cd <项目根>
mkdir -p ~/tv-backup
cp pairing.token pairing.code paired-devices.json channels.txt server.env ~/tv-backup/
chmod 600 ~/tv-backup/pairing.token ~/tv-backup/pairing.code ~/tv-backup/paired-devices.json
```

> ⚠️ `pairing.token` 一旦删除，已签发播放凭据失效，设备需重新配对。**要保留绑定，务必先备份。**

### 11.4 删除运行时生成文件（恢复干净状态）

以下均为 `.gitignore` 忽略的运行时产物，删除不影响源码；下次 `./start-server.sh` 或 systemd 服务会自动重建：

```sh
cd <项目根>
rm -f server.env channels.txt pairing.code pairing.token paired-devices.json server.log
rm -rf .venv
```

### 11.5 移除 FFmpeg 符号链接（恢复 PATH 原状）

```sh
rm -f ~/.local/bin/ffmpeg ~/.local/bin/ffprobe
```

- 只删符号链接，**不影响**全局 FFmpeg（真实二进制仍在其原始目录）。
- 验证：`command -v ffmpeg ffprobe` 应无输出。

### 11.6 卸载完成验证

```sh
systemctl --user is-active passport-tv.service 2>/dev/null || echo "服务已停止"
test ! -f ~/.config/systemd/user/passport-tv.service && echo "systemd unit 已移除"
command -v ffmpeg ffprobe || echo "FFmpeg 符号链接已移除"
ss -tln | grep 10745 || echo "端口 10745 已释放"
```

### 11.7 未来可能遇到的坑（卸载 / 清理相关）

| 现象 | 原因与处理 |
| --- | --- |
| 端口 10745 被占用 | 有残留旧进程。`ss -tlnp \| grep 10745` 找到 PID 后 `kill`，或 `systemctl --user restart passport-tv.service`。 |
| 配对码 / 密钥丢失 | `pairing.token` 删除后旧设备播放失效。若需保留绑定，按 11.3 备份；否则在设备上重新配对。 |
| `.venv` 损坏 | 直接 `rm -rf .venv`，再 `systemctl --user restart passport-tv.service` 会自动重建并重装依赖。 |
| 误删 `channels.txt` | 启动器不会自动覆盖已存在的频道文件；可从 `channels.example.txt` 复制模板，或先删除旧文件再 `--setup-only`。 |
| systemd 服务起不来 | `journalctl --user -u passport-tv.service -e` 查看日志；常见原因为端口被占或 `channels.txt` 为空。 |
| 残留 `~/.local/bin` 符号链接 | 不清理可能影响其他脚本检测到 ffmpeg；不再需要时按 11.5 删除。 |
| 卸载不彻底 | 按 11.1~11.6 顺序执行，最后用 11.6 验证。 |

## 12. 后续迭代文档沉淀约定

为避免上下文 token 爆炸，后续迭代的经验由 AI **自动沉淀**到本文件，**不需要再询问用户**：

1. **每次部署 / 排障 / 迭代完成后**，AI 自动将结论、命令、路径、坑与解决办法追加/更新到本文件对应章节，或新建 `NOTES-YYYY-MM-DD.md`。
2. **只记录结果与要点**：结论、可复现命令、关键路径、报错与解法；不粘贴大段日志。
3. **保持章节结构**：部署概况 → 已部署内容 → 启动/停止 → 设备连接 → 频道配置 → 配置项 → 经验 → 版本管理 → 竖屏默认 → 项目来源 → 卸载清理。
4. **更新本文件时**：新增内容放入对应章节，旧结论被替代时标注或删除，避免文档膨胀。
5. 本文件即自动沉淀的权威落盘文档，后续迭代以此为准持续维护。