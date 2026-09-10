#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

ENV_FILE=/etc/ddbot.env

if ! sudo test -s "${ENV_FILE}" || ! sudo test -d /opt/ddbot/ddbot; then
  echo "未找到现有 DDBot 安装（/opt/ddbot 和 /etc/ddbot.env），未执行升级。" >&2
  exit 1
fi

BACKUP_DIR="/var/backups/ddbot/$(date +%Y%m%d-%H%M%S)"
sudo install -d -m 0700 "${BACKUP_DIR}"
sudo cp -a /etc/ddbot.env "${BACKUP_DIR}/ddbot.env"
sudo tar --exclude=.venv --exclude=__pycache__ -czf "${BACKUP_DIR}/app.tar.gz" -C /opt ddbot
if sudo test -f /etc/systemd/system/ddbot.service; then
  sudo cp -a /etc/systemd/system/ddbot.service "${BACKUP_DIR}/ddbot.service"
fi
echo "配置和旧代码已备份到 ${BACKUP_DIR}"

rollback_upgrade() {
  local exit_code=$?
  trap - ERR
  set +e
  echo "升级失败，正在恢复旧版本……" >&2
  sudo cp -a "${BACKUP_DIR}/ddbot.env" /etc/ddbot.env
  sudo tar -xzf "${BACKUP_DIR}/app.tar.gz" -C /opt
  if sudo test -f "${BACKUP_DIR}/ddbot.service"; then
    sudo cp -a "${BACKUP_DIR}/ddbot.service" /etc/systemd/system/ddbot.service
  fi
  sudo systemctl daemon-reload
  if sudo systemctl restart ddbot; then
    echo "旧版本已恢复并重新启动；备份位于 ${BACKUP_DIR}。" >&2
  else
    echo "自动恢复后服务仍未启动；请从 ${BACKUP_DIR} 手动恢复。" >&2
  fi
  exit "${exit_code}"
}

trap rollback_upgrade ERR
sudo systemctl stop ddbot
if sudo test -d /var/lib/ddbot; then
  sudo cp -a /var/lib/ddbot "${BACKUP_DIR}/data"
fi

set_env_value() {
  local key="$1"
  local value="$2"
  if sudo grep -q "^${key}=" "${ENV_FILE}"; then
    sudo sed -i "s/^${key}=.*/${key}=${value}/" "${ENV_FILE}"
  else
    printf '%s=%s\n' "${key}" "${value}" | sudo tee -a "${ENV_FILE}" >/dev/null
  fi
}

# This release disables channel-to-topic relay for Traditional Chinese and
# keeps the independent hourly latest-message re-push disabled.
set_env_value RELAY_TRADITIONAL_TO_TOPIC false
set_env_value TOPIC_LATEST_PUSH_ENABLED false

bash deploy/install.sh
trap - ERR
echo "升级完成：华语频道到 Topic 28601 的转发已关闭，最新消息自动重推已关闭；现有配置和数据库已保留。"
