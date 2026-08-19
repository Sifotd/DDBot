# DDBot

Telegram 频道内容发布与管理 Bot。管理员可在私聊中创建文字或单图内容、配置一个链接按钮、预览并二次确认，然后独立或同时发布到：

- `@aliceeaichannel`
- `@alicekoreanbet`
- `@alicesmartpick`

发布记录保存在 SQLite，包含每个频道独立的 `message_id`、状态和失败原因。后续可按单频道或全部目标频道同步修改正文、图片、按钮以及删除消息。

频道消息会自动转发到群组 Topic：英语频道对应 Topic `28604`，韩语频道对应 Topic `23669`，繁体中文频道使用配置的 Topic。快捷模板为每种语言提供三行固定按钮，繁中模板使用繁体文案。

发布时可选择仅发布一次，或按 30 分钟、1 小时、6 小时、24 小时定时推送。首次内容会立即转发到对应 Topic，之后的重复内容直接发送到 Topic。同一 Topic 发布新内容时，其旧定时任务自动停止；也可在“已发布内容”的详情中手动停止。任务保存在 SQLite，Bot 重启后会继续执行。

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
CHANNEL_ALICE_EAI=@aliceeaichannel
CHANNEL_ALICE_KOREAN=@alicekoreanbet
CHANNEL_ALICE_TRADITIONAL=@alicesmartpick
TOPIC_TRADITIONAL=28601
FLOW_TIMEOUT_MINUTES=30
TARGET_GROUP_ID=-1003869352469
TOPIC_EAI=28604
TOPIC_KOREAN=23669
```

将 Bot 加入两个频道，并授予发布、编辑和删除消息权限，然后启动：

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
- 会话状态存于内存，进程重启后未完成的草稿会丢失，已发布记录不会丢失。
- 定时任务持久化在 SQLite；停止任务只阻止后续推送，不会删除已经发送的消息。
- 自动转发需要 Bot 同时是源频道管理员，并有目标群组及对应 Topic 的发言权限。

### Debian/Ubuntu 一键部署

将项目压缩包上传到服务器并解压，然后在项目目录执行：

```bash
bash deploy/install.sh
```

安装脚本会隐藏读取 Bot Token，创建 `ddbot` 系统用户、`/etc/ddbot.env` 安全配置、SQLite 持久化目录及开机自启的 systemd 服务。
