# DDBot

Telegram 频道内容发布与管理 Bot。管理员可在私聊中创建文字或单图内容、配置一个链接按钮、预览并二次确认，然后独立或同时发布到：

- `@AliceSmartPicksEN`
- `@AliceSmartPicksKR`
- `@alicesmartpick`
- 俄语频道：配置 `CHANNEL_ALICE_RUSSIAN` 后显示俄语发布选项和俄语按钮模板。

俄语话题默认监听群 `-1003869352469` 的 Topic `348731`，可通过 `TOPIC_RUSSIAN` 修改。同时配置俄语频道时，频道消息也会转发到该话题；未配置频道时只启用话题监听及最新消息重推。正文仍使用管理员输入的原文，不会自动翻译。

所有定时推送都会按目标 Topic 检查最新消息：若是本 Bot 自己发送的，则跳过本轮，等该 Topic 有其他发送者的新消息后，在下个定时周期恢复推送。跳过不会停止任务；其他 Topic 独立运行。关闭 `TOPIC_LATEST_PUSH_ENABLED` 只关闭最新消息自动重推，仍会监听消息供已有定时任务判断。

发布记录保存在 SQLite，包含每个频道独立的消息 ID、状态和失败原因。支持一次上传 1–10 张图片；多图会作为 Telegram 原生相册发布，并保存相册内全部消息 ID，便于后续同步修改或删除。

失败的频道转发允许后续更新安全重试。相册删除和图片替换会逐条处理消息，并在 SQLite 中保留尚未删除的旧消息 ID，避免一次失败后永久残留。快速重复点击“确认发布”只会执行一次。

频道消息会自动转发到群组 Topic：英语频道对应 Topic `28604`，韩语频道对应 Topic `23669`。繁体中文频道到 Topic `28601` 的转发默认关闭；只有显式设置 `RELAY_TRADITIONAL_TO_TOPIC=true` 才会开启。快捷模板为每种语言提供三行固定按钮，繁中模板使用繁体文案。

发布时可选择仅发布一次，或按 30 分钟、1 小时、6 小时、24 小时定时推送。首次内容会立即转发到对应 Topic，之后的重复内容直接发送到 Topic。同一 Topic 发布新内容时，其旧定时任务自动停止；也可在“已发布内容”的详情中手动停止。任务保存在 SQLite，Bot 重启后会继续执行。

Bot 会监听已启用 Topic 的最新消息；当 `TOPIC_LATEST_PUSH_ENABLED=true` 时，每 3600 秒检查一次并自动重推。最新消息由用户发送且为文字或单张图片时，会重新发送到原 Topic；图片配文会一并保留。Bot 自己发送的最新消息、以 `/` 开头的 Bot 命令、已经推送过的源消息以及暂不支持的消息类型均会跳过；循环定时任务也不会发送 Bot 命令。最新消息和推送源 `message_id` 保存在 SQLite，进程重启后仍可去重，各 Topic 的异常互不影响。Telegram Bot API 不提供聊天历史读取接口，因此只会从本功能启用并运行后收到的消息开始记录，启用前的历史消息不会自动补采。

正文目前不会自动翻译。管理员输入内容时，频道和对应 Topic 收到的仍是原文；“对应语言”目前只作用于三行快捷按钮模板。若要自动翻译正文，需要另行配置翻译服务，并在发布前生成英文、韩文和繁体中文内容。

## 快速开始

需要 Python 3.11 或更高版本。

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
Copy-Item .env.example .env
```

编辑 `.env`：

```dotenv
BOT_TOKEN=从_BotFather_获取的_token
ADMIN_USER_IDS=管理员Telegram用户ID,另一个管理员ID
DATABASE_PATH=data/ddbot.sqlite3
CHANNEL_ALICE_EAI=@AliceSmartPicksEN
CHANNEL_ALICE_KOREAN=@AliceSmartPicksKR
CHANNEL_ALICE_TRADITIONAL=@alicesmartpick
# 如需发布到俄语频道，填写真实频道并取消注释
# CHANNEL_ALICE_RUSSIAN=@your_russian_channel
TOPIC_RUSSIAN=348731
ALICE_START_URL=https://thealiceai.com/?code=N6WVL
TOPIC_TRADITIONAL=28601
RELAY_TRADITIONAL_TO_TOPIC=false
TOPIC_LATEST_PUSH_ENABLED=false
TOPIC_LATEST_PUSH_INTERVAL_SECONDS=3600
FLOW_TIMEOUT_MINUTES=30
TARGET_GROUP_ID=-1003869352469
TOPIC_EAI=28604
TOPIC_KOREAN=23669
```

将 Bot 加入三个频道，并授予发布、编辑和删除消息权限，然后启动：

```powershell
python -m ddbot.main
```

或安装后运行 `ddbot`。

## 管理命令

- `/start`：管理菜单
- `/new`：创建内容
- `/posts`：最近发布记录
- `/cancel`：取消当前操作

Bot 只处理 `ADMIN_USER_IDS` 白名单用户；其他用户统一收到“您没有操作权限”。发布、编辑和删除均逐频道执行，一个频道失败不会中断另一个频道，结果及失败原因会分别显示并持久化。

## 运行检查

```powershell
pytest
ruff check .
```

## 部署提示

- 使用长轮询运行，同一个 Token 不要启动多个实例。
- SQLite 已启用 WAL；请持久化 `data/` 目录并定期备份。
- Telegram 图片文案上限低于普通文字。若发送超限，API 错误会按频道显示。
- Telegram 原生相册不支持内联按钮；发布两张及以上图片时保留正文，但不会附带按钮模板。
- 文字正文上限为 4096 个 Telegram UTF-16 单位，图片说明上限为 1024；Bot 会在发布前检查。
- Telegram Bot API 不提供频道消息删除事件，因此从源频道手动删除消息时，已经转发到 Topic 的副本无法自动感知并删除。
- 会话状态存于内存，进程重启后未完成的草稿会丢失，已发布记录不会丢失。
- 定时任务持久化在 SQLite；停止任务只阻止后续推送，不会删除已经发送的消息。
- 自动转发需要 Bot 同时是源频道管理员，并有目标群组及对应 Topic 的发言权限。
- Topic 最新消息自动重推由 `TOPIC_LATEST_PUSH_ENABLED` 整体控制，本部署包默认关闭；设为 `true` 后，英语、韩语、俄语及已开启频道转发的繁中 Topic 会参与。保持 `TOPIC_LATEST_PUSH_INTERVAL_SECONDS=3600` 即每小时执行一次。
- Bot 必须能收到目标群组中的普通消息（建议设为群管理员；否则需通过 BotFather 关闭 privacy mode），并拥有各 Topic 的发言权限。启用后只会处理 `TARGET_GROUP_ID` 下配置的 Topic，不会监听其他群组或 Topic。

### Debian/Ubuntu 一键部署

将项目压缩包上传到服务器并解压，然后在项目目录执行：

```bash
bash deploy/install.sh
```

安装脚本会隐藏读取 Bot Token，创建 `ddbot` 系统用户、`/etc/ddbot.env` 安全配置、SQLite 持久化目录及开机自启的 systemd 服务。
