#!/usr/bin/env bash
# 体育成绩录入平台一键安装（UOS V20 / Debian 系，root 执行）。可重复执行。
#
#   sudo bash deploy/install.sh --domain tykcjlr.ruc.edu.cn [--data-disk /dev/vdb] [--admin 工号:姓名]
#
# 做的事：挂载数据盘（可选）→ 建运行用户 → 用 uv 装 Python 3.12 与依赖 → 写配置
#        → systemd 服务 → Nginx（80 跳 443）→ 每日备份 → 应急管理员（可选）。
set -euo pipefail

DOMAIN=""; DATA_DISK=""; ADMIN=""
APP_DIR=/opt/pe-scores/app; DATA_DIR=/data/pe-scores; ENV_FILE=/etc/pe-scores.env
while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain) DOMAIN="$2"; shift 2;;
    --data-disk) DATA_DISK="$2"; shift 2;;
    --admin) ADMIN="$2"; shift 2;;
    *) echo "未知参数 $1"; exit 1;;
  esac
done
[[ $EUID -eq 0 ]] || { echo "请用 root 执行"; exit 1; }
[[ -n "$DOMAIN" ]] || { echo "请用 --domain 指定备案域名"; exit 1; }
SRC_DIR="$(cd "$(dirname "$0")/.." && pwd)"

step() { echo; echo "==> $*"; }

if [[ -n "$DATA_DISK" ]]; then
  step "挂载数据盘 $DATA_DISK 到 /data"
  PART="${DATA_DISK}1"
  if ! blkid "$PART" >/dev/null 2>&1; then
    parted "$DATA_DISK" --script mklabel gpt mkpart primary ext4 0% 100%
    sleep 2; mkfs.ext4 -F "$PART"
  fi
  mkdir -p /data
  UUID=$(blkid -s UUID -o value "$PART")
  grep -q "$UUID" /etc/fstab || echo "UUID=$UUID /data ext4 defaults 0 2" >> /etc/fstab
  mountpoint -q /data || mount /data
fi

step "安装系统依赖"
apt-get update -y
apt-get install -y nginx curl ca-certificates rsync

step "建运行用户 pe 和目录"
id pe >/dev/null 2>&1 || useradd -r -m -d /opt/pe-scores -s /usr/sbin/nologin pe
mkdir -p "$APP_DIR" "$DATA_DIR"
rsync -a --delete --exclude .venv --exclude data --exclude .git "$SRC_DIR/" "$APP_DIR/"
chown -R pe:pe /opt/pe-scores "$DATA_DIR"

step "安装 uv 与 Python 3.12（系统自带 Python 版本过低）"
if [[ ! -x /usr/local/bin/uv ]]; then
  curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin INSTALLER_NO_MODIFY_PATH=1 sh
fi
sudo -u pe env UV_PYTHON_INSTALL_DIR=/opt/pe-scores/python /usr/local/bin/uv venv --python 3.12 --allow-existing "$APP_DIR/.venv"
sudo -u pe /usr/local/bin/uv pip install -p "$APP_DIR/.venv/bin/python" -r "$APP_DIR/requirements.txt"

step "写配置 $ENV_FILE"
if [[ ! -f "$ENV_FILE" ]]; then
  sed -e "s#pe.example.edu.cn#$DOMAIN#g" "$APP_DIR/deploy/pe-scores.env.example" > "$ENV_FILE"
  echo "PE_SECRET_KEY=$(head -c 48 /dev/urandom | base64 | tr -d '\n/+=')" >> "$ENV_FILE"
  echo "  已生成配置（临时口令登录 local 模式）；拿到 secret 后在 $ENV_FILE 里填 PE_SSO_CLIENT_SECRET、把 PE_AUTH_MODE 改成 sso，再 systemctl restart pe-scores"
fi
chown root:pe "$ENV_FILE"; chmod 640 "$ENV_FILE"

step "初始化数据库"
sudo -u pe bash -c "set -a; . $ENV_FILE; set +a; cd $APP_DIR && .venv/bin/python scripts/manage.py init"

step "systemd 服务"
cp "$APP_DIR/deploy/pe-scores.service" /etc/systemd/system/pe-scores.service
systemctl daemon-reload
systemctl enable pe-scores
systemctl restart pe-scores

step "Nginx"
sed -e "s#pe.example.edu.cn#$DOMAIN#g" "$APP_DIR/deploy/nginx.conf" > /etc/nginx/conf.d/pe-scores.conf
if [[ ! -f "/etc/nginx/certs/$DOMAIN.crt" ]]; then
  echo "  还没有证书 /etc/nginx/certs/$DOMAIN.crt 和 .key，先生成临时自签证书；拿到正式证书后覆盖这两个文件并 systemctl reload nginx"
  mkdir -p /etc/nginx/certs
  openssl req -x509 -nodes -newkey rsa:2048 -days 30 -subj "/CN=$DOMAIN" \
    -keyout "/etc/nginx/certs/$DOMAIN.key" -out "/etc/nginx/certs/$DOMAIN.crt"
  chmod 600 "/etc/nginx/certs/$DOMAIN.key"
fi
nginx -t && systemctl enable nginx && systemctl reload nginx || systemctl restart nginx

step "每日备份"
cp "$APP_DIR/deploy/pe-scores-backup.cron" /etc/cron.d/pe-scores-backup
chmod 644 /etc/cron.d/pe-scores-backup

if [[ -n "$ADMIN" ]]; then
  step "应急管理员"
  sudo -u pe bash -c "set -a; . $ENV_FILE; set +a; cd $APP_DIR && .venv/bin/python scripts/manage.py create-admin '${ADMIN%%:*}' '${ADMIN#*:}'"
fi

step "检查"
sleep 2
curl -fsS http://127.0.0.1:8000/api/auth/mode && echo && echo "安装完成：https://$DOMAIN"
