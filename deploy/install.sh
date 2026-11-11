#!/usr/bin/env bash
# 体育成绩录入平台一键安装（UOS V20 服务器版，root 执行）。可重复执行。
# 支持用 dnf/yum 装软件的版本（uelc20，与 CentOS 8 同类）和用 apt 的版本（与 Debian 同类）。
#
#   sudo bash deploy/install.sh --domain tykcjlr.ruc.edu.cn [--data-disk /dev/vdb] [--admin 工号:姓名]
#
# 做的事：挂载数据盘（可选）→ 建运行用户 → 用 uv 装 Python 3.12 与依赖 → 写配置
#        → systemd 服务 → Nginx（80 跳 443）→ 每日备份 → 应急管理员（可选）。
#
# 下载全部走国内镜像：uv 和 Python 依赖用清华 PyPI 镜像，Python 本体用南京大学镜像
# （清华没有 Python 预编译包的镜像）。要换别的镜像，执行前设环境变量 PYPI_MIRROR / PYTHON_MIRROR。
# 系统软件（Nginx 等）用服务器自带的 UOS 软件源。
set -euo pipefail

PYPI_MIRROR="${PYPI_MIRROR:-https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple}"
PYTHON_MIRROR="${PYTHON_MIRROR:-https://mirror.nju.edu.cn/github-release/astral-sh/python-build-standalone}"
# 版本固定，和 deploy/VERSIONS.md 一致；升级时一起改
UV_VERSION=0.13.7
PYTHON_VERSION=3.12.16

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

step "安装系统依赖"
if command -v dnf >/dev/null 2>&1 || command -v yum >/dev/null 2>&1; then
  PM=$(command -v dnf || command -v yum)
  "$PM" install -y nginx curl ca-certificates rsync unzip tar openssl parted e2fsprogs sudo cronie
  systemctl enable --now crond
elif command -v apt-get >/dev/null 2>&1; then
  apt-get update -y
  apt-get install -y nginx curl ca-certificates rsync unzip openssl parted sudo cron
else
  echo "找不到 dnf、yum 或 apt-get，无法安装系统软件"; exit 1
fi

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

step "建运行用户 pe 和目录"
id pe >/dev/null 2>&1 || useradd -r -m -d /opt/pe-scores -s /usr/sbin/nologin pe
mkdir -p "$APP_DIR" "$DATA_DIR"
rsync -a --delete --exclude .venv --exclude data --exclude .git "$SRC_DIR/" "$APP_DIR/"
chown -R pe:pe /opt/pe-scores "$DATA_DIR"
# 后面以 pe 身份执行命令；当前目录若在 /root 下，pe 没权限读，uv 会报 Permission denied
cd /opt/pe-scores

step "安装 uv $UV_VERSION 与 Python $PYTHON_VERSION（系统自带 Python 版本过低）"
if [[ "$(/usr/local/bin/uv --version 2>/dev/null | awk '{print $2}')" != "$UV_VERSION" ]]; then
  ARCH="$(uname -m)"
  case "$ARCH" in x86_64|aarch64) ;; *) echo "不支持的 CPU 架构 $ARCH"; exit 1;; esac
  # 从 PyPI 镜像取 uv 的 wheel 包（zip 格式，里面就是 uv 程序），按镜像给出的 sha256 校验
  HREF=$(curl -fsSL --retry 3 "$PYPI_MIRROR/uv/" \
    | grep -oE "href=\"[^\"]*/uv-${UV_VERSION}-py3-none-manylinux_2_17_${ARCH}[^\"]*\.whl#sha256=[0-9a-f]{64}\"" | head -1)
  [[ -n "$HREF" ]] || { echo "镜像 $PYPI_MIRROR 上找不到 uv $UV_VERSION（$ARCH）"; exit 1; }
  HREF="${HREF#href=\"}"; HREF="${HREF%\"}"
  TMP=$(mktemp -d)
  curl -fsSL --retry 3 -o "$TMP/uv.whl" "$PYPI_MIRROR/uv/${HREF%%#*}"
  echo "${HREF##*sha256=}  $TMP/uv.whl" | sha256sum -c --quiet
  unzip -q -o "$TMP/uv.whl" "uv-${UV_VERSION}.data/scripts/uv" -d "$TMP"
  install -m 755 "$TMP/uv-${UV_VERSION}.data/scripts/uv" /usr/local/bin/uv
  rm -rf "$TMP"
fi
sudo -u pe env UV_PYTHON_INSTALL_DIR=/opt/pe-scores/python UV_PYTHON_INSTALL_MIRROR="$PYTHON_MIRROR" \
  /usr/local/bin/uv venv --python "$PYTHON_VERSION" --allow-existing "$APP_DIR/.venv"
if [[ "$("$APP_DIR/.venv/bin/python" -c 'import platform; print(platform.python_version())')" != "$PYTHON_VERSION" ]]; then
  # 旧环境的 Python 版本不对：重建
  rm -rf "$APP_DIR/.venv"
  sudo -u pe env UV_PYTHON_INSTALL_DIR=/opt/pe-scores/python UV_PYTHON_INSTALL_MIRROR="$PYTHON_MIRROR" \
    /usr/local/bin/uv venv --python "$PYTHON_VERSION" "$APP_DIR/.venv"
fi
# 按 requirements.lock 装成一模一样的版本，多余的包会被删掉
sudo -u pe /usr/local/bin/uv pip sync -p "$APP_DIR/.venv/bin/python" --default-index "$PYPI_MIRROR" "$APP_DIR/requirements.lock"

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
if command -v getenforce >/dev/null 2>&1 && [[ "$(getenforce)" == "Enforcing" ]]; then
  # SELinux 默认不许 Nginx 转发到本机 8000 端口
  setsebool -P httpd_can_network_connect 1
  restorecon -R /etc/nginx/certs /etc/nginx/conf.d 2>/dev/null || true
fi
if systemctl is-active -q firewalld 2>/dev/null; then
  # 只放通本机防火墙的 80/443；对公网是否放通仍由学校的 WAF 和边界防火墙决定
  firewall-cmd -q --permanent --add-service=http --add-service=https
  firewall-cmd -q --reload
fi
nginx -t
systemctl enable nginx
if systemctl is-active -q nginx; then systemctl reload nginx; else systemctl restart nginx; fi

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
