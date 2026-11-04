<p align="right"><strong>简体中文</strong> · <a href="README.md">English</a></p>

# Passport 共用 Wi-Fi 生命周期

从 FoloTalk 既有 STA / 配网生命周期提取。两个宿主共用初始化、完整扫描、
按信号选择已保存网络、隐藏网络回退、连接超时/错误清理和实时 SSID/RSSI/IP
读取。组件不读取或保存凭据，FoloTalk 包装层保留原网络历史和配置保存规则。
独立宿主只读继承已保存网络，不写 FoloTalk 键；成功连接另存到 `ptv_config`。

两个宿主调用同一个 `WIFI_AUTH_OPEN` 设置热点函数，不为独立版另加密码；
设置 HTTP 请求仍限制为设置网络接口和匹配 Origin。

配网准备保留既有 STA 连接。FoloTalk 的官方配网管理器继续负责 Wi-Fi 启停；
独立宿主使用同一无密码 AP 辅助接口。自动重连默认关闭，仅由独立宿主在连接
成功后显式开启。
