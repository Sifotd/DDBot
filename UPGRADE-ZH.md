# Google Cloud SSH 升级

1. 在 SSH 页面点击“上传文件”，上传 `DDBot-upgrade-20260911-v5-linux.zip`，等待上传完成。
2. 在 SSH 终端粘贴执行：

```bash
cd ~
python3 -m zipfile -e DDBot-upgrade-20260911-v5-linux.zip ddbot-upgrade-20260911-v5
cd ~/ddbot-upgrade-20260911-v5/DDBot
bash deploy/upgrade.sh
```

出现“升级完成”和 `active (running)` 表示服务已启动。脚本适用于此前使用本项目安装脚本部署的服务，会保留 `/etc/ddbot.env` 和数据库，并在 `/var/backups/ddbot/时间戳/` 备份配置、旧代码及默认数据目录 `/var/lib/ddbot`。如果自行配置了其他数据库路径，请另行备份该文件。升级期间 Bot 会短暂停止。

查看运行日志：

```bash
sudo journalctl -u ddbot -n 50 --no-pager
```

本次升级会将 `RELAY_TRADITIONAL_TO_TOPIC` 设为 `false`，关闭华语频道到 Topic `28601` 的转发；同时将 `TOPIC_LATEST_PUSH_ENABLED` 设为 `false`，关闭各 Topic 的每小时最新消息自动重推。其他现有配置保持不变。

本版本还会拦截自动推送中的 Telegram Bot 命令。以 `/` 开头的文字（例如 `/weekly_rank`）不会被最新消息重推或循环定时任务再次发送。

本版本同时修复转发失败无法重试、相册部分删除后卡住、图片替换遗失旧消息 ID、重复点击导致重复发布、慢速相册被拆分、频道相册编辑破坏分组以及 Telegram 限流等待不足。升级失败时脚本会自动恢复旧配置和代码并尝试重新启动旧服务。

确认启动日志的 topics 中有 `'russian': 348731`。俄语话题默认位于群 `-1003869352469`，已有配置中的显式值优先。

升级后在俄语 Topic 发送一条普通消息，再查看日志，应该出现 `Observed latest topic message: topic=348731`。Bot 需要能收到群内普通消息；只会记录运行时收到的消息，不会补读历史消息。

俄语群话题无需配置频道即可工作。如还需向俄语频道发布，可在 `/etc/ddbot.env` 中加入实际的 `CHANNEL_ALICE_RUSSIAN=@频道用户名` 后执行 `sudo systemctl restart ddbot`。
