#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR=/opt/ddbot
DATA_DIR=/var/lib/ddbot
ENV_FILE=/etc/ddbot.env
SERVICE_FILE=/etc/systemd/system/ddbot.service
APP_USER=ddbot

if [[ ${EUID} -eq 0 ]]; then
  echo "请使用普通 SSH 用户运行此脚本；脚本会在需要时调用 sudo。" >&2
  exit 1
fi

if [[ ! -f pyproject.toml || ! -d ddbot ]]; then
  echo "请在解压后的 DDBot 目录中运行：bash deploy/install.sh" >&2
  exit 1
fi

read -r -s -p "请输入 Bot Token（输入不会显示）: " BOT_TOKEN
echo
if [[ ! ${BOT_TOKEN} =~ ^[0-9]+:[A-Za-z0-9_-]+$ ]]; then
  echo "Bot Token 格式不正确，部署已停止。" >&2
  exit 1
fi

echo "[1/6] 安装 Python 运行环境..."
sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-venv

echo "[2/6] 创建服务用户和数据目录..."
if ! id "${APP_USER}" >/dev/null 2>&1; then
  sudo useradd --system --home-dir "${APP_DIR}" --shell /usr/sbin/nologin "${APP_USER}"
fi
sudo install -d -o "${APP_USER}" -g "${APP_USER}" -m 0750 "${APP_DIR}" "${DATA_DIR}"

echo "[3/6] 安装应用..."
sudo cp -R ddbot pyproject.toml README.md "${APP_DIR}/"
sudo chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}"
sudo -u "${APP_USER}" python3 -m venv "${APP_DIR}/.venv"
sudo -u "${APP_USER}" "${APP_DIR}/.venv/bin/pip" install --disable-pip-version-check "${APP_DIR}"

echo "[4/6] 写入安全配置..."
sudo install -o root -g root -m 0600 /dev/null "${ENV_FILE}"
printf '%s\n' \
  "BOT_TOKEN=${BOT_TOKEN}" \
  "ADMIN_USER_IDS=7164480509,6404111657,8156318561" \
  "DATABASE_PATH=${DATA_DIR}/ddbot.sqlite3" \
  "CHANNEL_ALICE_EAI=@aliceeaichannel" \
  "CHANNEL_ALICE_KOREAN=@alicekoreanbet" \
  "FLOW_TIMEOUT_MINUTES=30" \
  "TARGET_GROUP_ID=-1003869352469" \
  "TOPIC_EAI=28604" \
  "TOPIC_KOREAN=23669" | sudo tee "${ENV_FILE}" >/dev/null
unset BOT_TOKEN

echo "[5/6] 配置 systemd 常驻服务..."
sudo tee "${SERVICE_FILE}" >/dev/null <<'EOF'
[Unit]
Description=Telegram Channel Publishing Bot
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=ddbot
Group=ddbot
WorkingDirectory=/opt/ddbot
EnvironmentFile=/etc/ddbot.env
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/ddbot/.venv/bin/python -m ddbot.main
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/ddbot

[Install]
WantedBy=multi-user.target
EOF

echo "[6/6] 启动并检查服务..."
sudo systemctl daemon-reload
sudo systemctl enable --now ddbot
sleep 3
if sudo systemctl is-active --quiet ddbot; then
  echo "部署成功：ddbot 服务正在运行。"
  sudo systemctl status ddbot --no-pager --lines=8
else
  echo "服务启动失败，最近日志如下：" >&2
  sudo journalctl -u ddbot --no-pager -n 50 >&2
  exit 1
fi

echo
echo "下一步：将 Bot 加入两个频道并授予发布、编辑、删除消息权限。"
echo "查看日志：sudo journalctl -u ddbot -f"
echo "重启服务：sudo systemctl restart ddbot"
