# astrbot_plugin_mc_query

Minecraft 服务器查询 AstrBot 插件：查询服务器并在书页卡片上展示信息。

## 指令

- `/mcadd <名称> <域名> [介绍]` — 添加服务器（**仅 AstrBot 管理员**使用）。域名可带端口，如 `mc.example.com:25565`。
- `/mc [域名]` — 查询服务器并渲染图片；不带域名时使用本会话最近添加的服务器。

## 安装

将本目录复制到 AstrBot 的 `data/plugins/astrbot_plugin_mc_query/`，重启 AstrBot。

## 配置（AstrBot WebUI 插件设置可改）

| 配置项 | 说明 | 默认 |
| --- | --- | --- |
| `show_logo` | 是否显示服务器图标 | 开 |
| `show_domain` | 是否显示服务器域名 | 开 |
| `show_name` | 是否显示服务器名称 | 开 |
| `show_motd` | 是否显示 MOTD | 开 |
| `show_online_mode` | 是否显示正版/离线标识 | 开 |
| `show_ping` | 是否显示 Ping(毫秒) | 开 |
| `show_online` | 是否显示在线人数 | 开 |
| `show_version` | 是否显示版本(含核心) | 开 |
| `render_as_image` | 渲染图片，否=以文本发送 | 开 |
| `query_timeout` | 单次查询超时(秒) | 8 |
| `mc_query_retry` | 查询失败额外重试次数 | 2 |
| `query_failed_text` | 查询失败回复文案 | 默认文案 |

## 说明

- 卡片尺寸 730×900，带透明书页背景。
- Ping 按阈值上色：<40ms 绿、40–60 黄、>60 红；在线人数始终绿色。
- 版本后括号内为服务器核心（Paper/Fabric/Forge/NeoForge/Folia 等），检测不到则不显示。
- 正版/离线为自动探测，部分代理/反bot 服可能无法判定（则不显示该标识）。
