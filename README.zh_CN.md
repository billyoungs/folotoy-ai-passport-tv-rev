<p align="right"><strong>简体中文</strong> · <a href="README.md">English</a></p>

# Passport TV

把 FoloToy AI Passport 变成一台网络电视。电脑或 Linux 服务器负责拉取直播、缩放和转码，设备通过 Wi-Fi 接收并播放。三个按键可以换台、调音量、调亮度和切换清晰度。

**独立固件 + 独立服务，不需要 FoloTalk 账号或 Admin。** 播放器和 Wi-Fi 生命周期同时作为组件提供，FoloTalk 可以继续集成同一份代码。

- ESP32-C3、8 MB Flash，无 PSRAM。
- 320×180、RGB332、整帧接收后显示；16 kHz 单声道 ADPCM。
- 流畅 / 标清 / 超清：8 / 15 / 30 fps，音频格式不随档位改变。
- 同频道、同档位共享直播转码，每个连接保留独立的有界队列。
- 播放源断流重连时显示 4 fps 黑白电视雪花及 `RECONNECTING...`，恢复后自动回到正常画面；不额外播放雪花噪声。
- 服务支持 **macOS、Linux**。首次启动自动创建虚拟环境、安装 Python 依赖、生成配对码并引导配置频道。

```text
直播源 → Mac / Linux：FFmpeg + TV 服务 → Wi-Fi → AI Passport
```

## 1. 下载项目

```sh
git clone https://github.com/billyoungs/ai-passport-tv.git
cd ai-passport-tv
```

目录说明：

```text
Start TV.command        Mac 双击入口
start-server.sh         Mac / Linux 启动入口
tools/                  启动器、固件构建与校验脚本
firmware/               独立 ESP-IDF 工程及共用组件
service/                Python TV 服务与测试
stream.env.example      已验证的播放参数模板
channels.example.txt    默认 CCTV1 示例频道（含所需 User-Agent）
fonts/                  独立菜单字体的 OFL 许可和来源摘要
```

## 2. 安装服务运行环境

**只运行服务不需要 ESP-IDF。** 需要 Python 3.10+、FFmpeg 和 ffprobe，并保证命令能在终端运行。

macOS 安装 [Homebrew](https://brew.sh/) 后执行：

```sh
brew install python ffmpeg
```

Ubuntu 22.04+ / Debian 12+：

```sh
sudo apt-get update
sudo apt-get install python3 python3-venv python3-pip ffmpeg
```

其他 Linux 发行版使用自己的包管理器安装相同工具。检查：

```sh
python3 --version
ffmpeg -version
ffprobe -version
```

安装来源：[FFmpeg](https://ffmpeg.org/download.html)、[Homebrew FFmpeg](https://formulae.brew.sh/formula/ffmpeg)。

## 3. 一键启动 TV 服务

**Mac：** 在 Finder 双击 `Start TV.command`。也可以像 Linux 一样在项目根目录执行：

```sh
./start-server.sh
```

首次运行会：

1. 检查 Python、FFmpeg 和 ffprobe。
2. 创建 `server.env`、`channels.txt`、四位 `pairing.code` 及保存在 `pairing.token` 的内部签名密钥，已有文件保持不变。
3. 默认使用示例表中的 CCTV1；只有频道表为空时，才询问频道名称和直播源 URL。
4. 创建项目内 `.venv`，安装 `service/requirements.txt`。
5. 显示电脑地址候选和端口，开始监听设备连接。

第一次安装 Python 依赖需要网络；以后依赖未变化且可正常导入时不会再次安装。脚本不会安装系统软件、注册后台服务或修改防火墙。终端保持打开，`Ctrl+C` 停止；再次运行同一入口继续使用原配置。Mac 电脑需要保持唤醒。

如下载 ZIP 后提示不可执行，先在终端运行：

```sh
chmod +x start-server.sh 'Start TV.command'
```

可选命令：

```sh
./start-server.sh --setup-only     # 配置并安装依赖，暂不播放
./start-server.sh --check          # 只校验环境和配置，不安装、不启动
./start-server.sh --show-pairing   # 在本机显示设备需要填写的配对码
```

无人值守机器先手动填写 `channels.txt`，再运行脚本。没有频道的非交互启动会明确退出，不会卡在输入提示。可用 `PYTHON=/path/to/python3 ./start-server.sh` 选择 Python。

### 频道配置

编辑根目录的 `channels.txt`，每行一个频道：

```text
# id | 设备显示名称 | HTTP/HTTPS 直播源 URL [| 可选 User-Agent]
news | 新闻 | https://example.com/live/news.m3u8
sports | 体育 | https://example.com/live/sports.m3u8 | MyPlayer/1.0
```

上面两个 example.com 地址仅用于说明格式。仓库的 `channels.example.txt` 已提供一条 CCTV1 地址及所需的 `AptvPlayer-UA`，首次启动会复制到本机 `channels.txt`，已有配置不会覆盖。可直接先用 CCTV1 测试，再添加其他你有权使用的频道。第三方源的可用性可能变化；连接失败时可替换地址。
`id` 使用不含空格的 ASCII 字符，最多 15 字节，不可重复；显示名称可以是中文。
设备默认接收前 40 个频道，首个频道为首次播放默认频道。修改后重启服务。源地址若含访问凭据，只保留在本机的频道表，不要提交 Git。

项目面向直播；不提供影片、不包含私人频道，也没有点播进度管理。

### 服务和播放配置

编辑自动生成的 `server.env`，每行 `TV_NAME=value`，修改后重启。它不是 shell 脚本，不执行命令。优先级为：启动进程的环境变量 > `server.env` > `stream.env.example`。

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `TV_BIND` | `0.0.0.0` | 监听本机各 IPv4 接口；只在本机访问可改为 `127.0.0.1` |
| `TV_PORT` | `10745` | 设备连接的 TCP 端口 |
| `TV_FPS` | `30` | 保留超清目标；设备仍可选择 8/15/30 三档 |
| `TV_ADAPTIVE` | `0` | 不启用自适应降预算 |
| `TV_RATE_START/MIN/MAX` | 各 `1000000` | 固定发送预算，单位字节/秒 |
| `TV_PACKET_TARGET` | `12288` | 12 KiB 媒体协议包目标 |
| `TV_ENCODE` | `full` | 保留整帧编码路径 |
| `TV_WRITE_PACING_RATE` | `1000000` | 应用层平滑发送，字节/秒 |
| `TV_WRITE_PACING_CHUNK` | `12288` | 单次平滑写入上限 |
| `TV_TCP_PACING_RATE` | Linux `1100000` | Linux 内核发包节奏；Mac 启动器强制设为 `0` |
| `TV_MAX_SESSIONS` | `2` | 总并发连接上限 |
| `TV_MAX_PER_ADDRESS` | `2` | 同一来源 IP 的连接上限 |
| `TV_SHARED_LIVE` | `1` | 同频道同档位共享转码 |

Mac 只关闭其不支持的 Linux 内核选项，**保留应用层平滑发送，画面、帧率、音频格式和发送预算不降低**。实际帧率还取决于直播源、电脑性能及网络。

配对码保存在根目录 `pairing.code`，为 **4 位字母数字码**，不区分大小写、可供多台设备配对，**默认永久有效**；服务重启后保持。运行 `./start-server.sh --show-pairing` 在本机查看。手动修改时保留文件权限 `0600`。

`pairing.token` 的长随机值是内部签名密钥，不再填写到配置页面。迁移服务时应保留它和 `paired-devices.json`，这些文件不要公开。已配置旧长码的设备仍可播放。更换短配对码只阻止使用旧码建立新绑定，已有绑定保持；更换内部签名密钥会使已签发的播放凭据失效。无需也不要另设 `TV_PAIRING_TOKEN`。

配对接口与 TV 播放共用同一个 TCP 端口。设备也支持部署方提供的 HTTPS 配对入口。若公网映射或网关地址与 TV 监听地址不同，可用 `TV_PUBLIC_HOST`、`TV_PUBLIC_PORT` 指定设备实际连接的播放地址和端口。

`server.env`、`channels.txt`、`pairing.code`、`pairing.token`、`paired-devices.json` 和 `.venv` 均被 Git 忽略。

## 4. 编译设备固件

固件使用 **ESP-IDF v5.5.3**。先按 [Espressif 的 Mac / Linux 安装指南](https://docs.espressif.com/projects/esp-idf/en/v5.5.3/esp32c3/get-started/linux-macos-setup.html)安装系统依赖，再安装并激活工具链：

```sh
mkdir -p ~/esp
cd ~/esp
git clone -b v5.5.3 --recursive https://github.com/espressif/esp-idf.git
cd esp-idf
./install.sh esp32c3
. ./export.sh
```

回到本项目根目录：

```sh
./tools/build.sh
```

首次构建会下载 `dependencies.lock` 锁定的 ESP-IDF 组件。脚本使用独立的 `build/sdkconfig`，不需要 FoloTalk 邀请码、Wi-Fi 密码或服务端密钥。

产物：

| 路径 | 用途 |
| --- | --- |
| `build/FoloToy-AI-Passport-full.bin` | 新设备的合并镜像 |
| `build/FoloToy-AI-Passport.bin` | 应用分区镜像 |
| `dist/firmware/<完整镜像 SHA-256>/` | 镜像、匹配 ELF/MAP、分区表和校验清单 |

脚本执行分区边界、合并镜像及归档校验。可重新验证某个归档：

```sh
python3 tools/archive_firmware.py verify dist/firmware/<完整镜像-SHA256>
```

### 刷写与已有配置

使用数据线连接设备，确认它是目标 AI Passport。Mac 可用串口通常为 `/dev/cu.usbmodem...`，Linux 通常为 `/dev/ttyACM...`；下面用 `PORT` 占位，须替换为实际串口。

**新设备或允许重新设置的设备：** 在已激活的 ESP-IDF 环境中写入合并镜像：

```sh
python -m esptool --chip esp32c3 --port PORT write_flash 0x0 build/FoloToy-AI-Passport-full.bin
```

**保留已有 FoloTalk 账号和配置：不要使用上面的完整镜像命令。** 只有确认原设备分区表与 `firmware/partitions.csv` 完全一致后，才可只更新应用分区：

```sh
python -m esptool --chip esp32c3 --port PORT write_flash 0x10000 build/FoloToy-AI-Passport.bin
```

分区基线：NVS `0x9000/0x6000`，应用 `0x10000/0x76d000`，照片 `0x77d000/0x80000`，恢复区 `0x7fd000/0x3000`。未确认兼容性不要刷写；不需要执行整片擦除。独立版只读继承已保存 Wi-Fi，自己的连接配置写入 `ptv_config`，按服务入口隔离的设备凭据写入 `ptv_auth`，清晰度使用 `ft_tv`；不改原账号、照片或恢复数据。

## 5. 设备 Wi-Fi 和 TV 连接

1. 设备首次进入设置时，显示 `PassportTV-xxxx-xxx` **无密码热点**。
2. 用手机连接该热点。提示“无互联网”时仍保持连接；手动打开 `http://192.168.4.1`。
3. 在复用的 Wi-Fi 页面扫描并选择 **2.4 GHz** 网络，输入路由器密码，保存并等待联网。
4. 直接进入电视连接设置，没有昵称或头像页面。填写：

   | 字段 | 示例 / 说明 |
   | --- | --- |
   | 电视服务器 | 电脑局域网地址，如 `192.168.1.100`；也支持带 `http://` 或 `https://` 的配对服务入口（不含路径） |
   | 端口 | `10745`，与 `TV_PORT` 一致 |
   | 播放配对码 | `./start-server.sh --show-pairing` 显示的内容 |

5. 保存后设备退出热点并播放，重启自动连接。已有可用网络可能直接跳到电视设置。

**设备连接的是运行服务的电脑地址，不是 `192.168.4.1`、`0.0.0.0` 或 `localhost`。**
电脑连 5 GHz、设备连 2.4 GHz 可以，只要同一局域网且没有 AP/访客隔离。电脑防火墙需要允许 TCP `10745` 入站。建议给电脑保留固定 DHCP 地址，避免重启后地址变化。

使用远程 Linux 服务器时，填写公网域名/IP 和外部映射端口，放行对应 TCP 端口；不要求部署任何 Admin。当前 FAV1/TCP 不加密，配对码只做接入校验，远程连接宜使用受控网络或 VPN。

## 6. 设备操作

以下播放快捷键在菜单关闭、正常播放画面下使用：

- 短按 OK：打开电视菜单，“频道”和“清晰度”是两个独立入口；UP/DOWN 选择，OK 确认。进入清晰度始终只需 OK → DOWN → OK，不用滚动频道列表。
- 主菜单、频道列表和清晰度页打开时，四角显示 Wi-Fi 信号图标、电池图标与百分比、FPS 和接收速率，每秒更新一次；电量不高于 20% 时为橙色，不高于 10% 时为红色。
- 频道列表保留选择和滚动位置；长按 OK 返回电视菜单，再长按关闭菜单。清晰度有流畅 8 fps、标清 15 fps、超清 30 fps 三档。
- **双击 OK：打开亮度页**；短按 UP/DOWN 调亮/调暗，短按 OK 返回播放。亮度不在频道菜单中。
- 短按 UP/DOWN：切换频道。
- 长按 UP/DOWN：增大/减小音量，每次 10%。
- 双击 UP 或 DOWN：查看播放状态。
- 亮度页或状态页打开时，长按 OK：关闭页面并返回播放。
- 播放时长按 OK：暂停并退出播放；暂停页短按 OK：继续。
- 暂停页长按 OK：重新设置网络/TV 地址/配对码。配对码留空只保留同一服务的已有绑定；更换服务需要对应配对码。

## 7. 常见问题

| 现象 | 检查 |
| --- | --- |
| 找不到 Python / FFmpeg | 按第 2 节安装；Finder 启动会补充常见 Homebrew PATH |
| 创建虚拟环境失败 | Linux 安装 `python3-venv`；选择 Python 3.10+ |
| 首次安装依赖很慢 | 首次需要下载 NumPy，可配置 pip 镜像；后续不重复安装健康的依赖 |
| 启动提示没有频道 | 将 `channels.example.txt` 中的 CCTV1 条目复制到本机 `channels.txt`，或填写其他真实源 |
| 设备提示重新连接 | 确认电脑服务仍运行、地址和 TCP 端口正确、配对码一致；查看服务终端错误 |
| 电脑可以访问但设备连不上 | 电脑不要只监听 `127.0.0.1`，检查防火墙、访客隔离和端口映射 |
| 直播源报 403 / 超时 | 在服务端检查 URL 是否有效，是否需要第四字段 User-Agent |
| Mac 报 Linux pacing 错误 | 使用本项目启动器；手动启动时须设置 `TV_TCP_PACING_RATE=0` |
| 修改配置没生效 | 停止并重启服务；已有设备退出电视再进入 |
| 设备上不是 30 fps | 确认选择超清；源帧率、转码能力和网络共同决定实际值 |

## 开发、测试与集成

```sh
python3 -m unittest discover -s tests -v
python3 tests/smoke_server.py
cd service
../.venv/bin/python -m unittest discover -s tests -v
```

`firmware/components/passport_tv` 是共用播放器，`passport_wifi` 是共用连接/开放热点生命周期。FoloTalk 继续使用自己的账号、签名票据、观看状态和内存交接；独立服务使用单独的配对码入口。两者共用媒体/转码/发送实现，独立版不链接社交或电台代码。

固件已在 ED48 验证开放热点网页配网、保存网络及清晰度后的重启、暂停/恢复和三档切换。设备软件帧计数不等于外部屏幕/声音采集，不能替代所有网络环境下的验收。启动器和服务的测试以当前提交为准；本项目不包含生产配置、私人影片或凭据。

## 许可

代码保留 MIT 许可；上游 TV 服务来源和许可见 `service/UPSTREAM_COMMIT`、`service/LICENSE.upstream`。
独立菜单字体来自 Adobe Source Han Sans，使用 OFL-1.1，来源及输出摘要见 `fonts/manifest.json`、`fonts/LICENSE.txt`。FFmpeg 是用户单独安装的软件，遵循其构建版本对应的许可。

### 统一配对接口

开源和私有部署使用同一套设备请求格式。配置页面只有一个「配对码」输入框，内部设备凭据和播放票据不在页面中显示。

- `POST /social/tv/activate`：JSON `{"device":"<12位小写十六进制设备ID>","code":"<4位字母数字码>"}`。
- `POST /social/tv/config`：JSON `{"device":"<设备ID>"}`。
- 两者均携带 `Authorization: Bearer <64位小写十六进制设备随机凭据>`，返回 JSON `{"host":"<TV地址>","port":10745,"ticket":"<播放凭据>"}`。

设备在配对前保存随机凭据，成功响应丢失后可恢复，避免重复核销。每次建立播放连接、切台、切档或断线重连前刷新连接信息，不逐帧鉴权。共享播放器提供可选的连接信息回调，FoloTalk 和旧静态配对配置可继续不设置该回调。私有构建只通过编译配置选择配对入口，与开源版使用同一份应用及客户端源码，不再在构建时修改播放器。

配对规则由服务端决定：附带的开源服务默认配对码永久有效、可重复使用，设备绑定也不自动过期；私有后台可继续执行一次使用、过期及设备绑定规则。私有 Admin、密钥、数据库和专有规则实现不纳入开源导出。HTTP/FAV1 为明文传输，按部署环境使用可信局域网、VPN 或 HTTPS 网关。
