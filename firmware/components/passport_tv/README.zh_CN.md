<p align="right"><strong>简体中文</strong> · <a href="README.md">English</a></p>

# Passport TV 共享播放器

本组件是 FoloTalk 与独立应用唯一共用的电视接收、整帧缓冲、解码、音频时钟
和菜单实现。[独立应用文档](../../../README.zh_CN.md)说明配置与导出。
保留原 `ft_tv_*` 接口，`passport_tv.h` 同时提供与产品名称无关的别名。

宿主初始化 Wi-Fi、BSP 显示/音频/按键和 NVS，提供 DMA 条带、10 KiB 控制/帧
工作区及 8～24 KiB 额外帧存储。启动前必须释放其他屏幕和音频所有者，等待
`done` 后才能收回工作区。可选 `prepare` 负责交接宿主空闲资源，`settings`
接收音量和亮度变化；`allocation_failed` 是不得分配内存或阻塞的宿主内存失败
记录回调，退出时恢复。独立宿主不提供此回调时使用内置空操作。

`ticket` 字段是 HELLO 鉴权载荷：FoloTalk 提供签名票据，独立应用提供配对码。
鉴权策略保留在服务入口，不属于媒体解码器。组件不包含账号、配网或 Admin 请求。

CMake 保留已验收的 `-O2` 文件和接收/ACK 路径 IRAM 布局。
`PASSPORT_TV_FONT_DIR` 选择构建时的字体资源，工作区默认保留 FoloTalk 的原文字
布局；独立应用和导出包使用附带许可的 OFL 字库。公开历史字库前须核查来源，
白名单导出器会显式替换它们。
