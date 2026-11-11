# 部署说明（通州云平台 · UOS V20）

服务器：体育部已分配的通州云平台虚拟服务器，SSH 端口 10001，300GB 数据盘需自行分区挂载。Web 只经备案域名、WAF 与防火墙对公网放通 80/443，管理端口只允许堡垒机访问。

## 一键安装（推荐）

从堡垒机登录服务器后，以 root 执行：

```bash
git clone <仓库地址> /root/pe-scores && cd /root/pe-scores
bash deploy/install.sh --domain <备案域名> --data-disk /dev/vdb --admin <工号>:<姓名>
```

脚本会挂载数据盘、用 uv 装 Python 3.12（UOS V20 自带的 Python 版本过低）、装依赖、写配置、起 systemd 服务和 Nginx、配每日备份，并打印应急管理员的初始口令。

下载走国内镜像：uv 和 Python 依赖用清华 PyPI 镜像，Python 本体用南京大学镜像（清华没有这个镜像），系统软件用 UOS 自带软件源。所有版本固定，清单见 [VERSIONS.md](VERSIONS.md)。可以重复执行；以后更新代码时 `git pull` 后再执行一次即可。服务器连不上代码仓库时，把代码压缩包传到服务器解压到 `/root/pe-scores`，再在里面执行同一条命令。

已经拿到正式证书的，执行前先放到 `/etc/nginx/certs/<备案域名>.crt` 和 `/etc/nginx/certs/<备案域名>.key`（证书文件要含中间证书），脚本就不再生成临时证书。

脚本同时支持用 dnf/yum 装软件的 UOS 服务器版（uelc20，与 CentOS 8 同类）和用 apt 的版本；SELinux 开着或 firewalld 在运行时，会自动放行 Nginx 转发和 80/443 端口。数据盘已挂载过的，去掉 `--data-disk`。

下面是脚本做的每一步，需要手工排查时参考。

## 1. 挂载数据盘

```bash
lsblk                                   # 找到 300GB 的数据盘，例如 /dev/vdb
sudo parted /dev/vdb --script mklabel gpt mkpart primary ext4 0% 100%
sudo mkfs.ext4 /dev/vdb1
sudo mkdir -p /data
echo "UUID=$(sudo blkid -s UUID -o value /dev/vdb1) /data ext4 defaults 0 2" | sudo tee -a /etc/fstab
sudo mount -a && df -h /data
```

## 2. 安装应用

```bash
sudo useradd -r -m -d /opt/pe-scores -s /usr/sbin/nologin pe
sudo -u pe git clone <仓库地址> /opt/pe-scores/app
cd /opt/pe-scores/app
sudo -u pe python3 -m venv .venv
sudo -u pe .venv/bin/pip install -i https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple -r requirements.lock
sudo mkdir -p /data/pe-scores && sudo chown pe:pe /data/pe-scores
```

UOS 自带的 Python 版本过低，先用 uv 装 3.12（做法见 `install.sh`）。

## 3. 配置并启动

```bash
sudo cp deploy/pe-scores.env.example /etc/pe-scores.env   # 按实际填写回调地址、secret、管理员工号
sudo chown root:pe /etc/pe-scores.env && sudo chmod 640 /etc/pe-scores.env
sudo cp deploy/pe-scores.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now pe-scores
sudo -u pe env $(cat /etc/pe-scores.env | xargs) .venv/bin/python scripts/manage.py create-admin <工号> <姓名>
```

应用只监听 `127.0.0.1:8000`，由 Nginx 对外。

## 4. Nginx 与 HTTPS

```bash
sudo dnf install -y nginx     # apt 系的 UOS 用 apt install nginx
sudo cp deploy/nginx.conf /etc/nginx/conf.d/pe-scores.conf   # 改成备案域名与证书路径
sudo nginx -t && sudo systemctl reload nginx
```

80 端口强制跳转 443。证书使用学校统一证书或信息技术中心签发的证书。

## 5. 备份

```bash
sudo cp deploy/pe-scores-backup.cron /etc/cron.d/pe-scores-backup
```

每天 02:30 用 SQLite 在线备份到 `/data/pe-scores/backups/`，保留 60 天。建议再定期同步一份到信息技术中心的备份存储。

## 6. 安全清单

- 首次登录修改服务器初始管理口令（≥8位，三类字符）。
- 防火墙只对公网放通 80、443；SSH 10001 只允许堡垒机来源。
- 统一认证启用前 `PE_AUTH_MODE=local`；填好 `PE_SSO_CLIENT_SECRET` 后改为 `sso`。生产环境禁止使用 `dev`。
- 数字人大账号、appid 的 secret 等凭据不写进代码仓库和文档，只写在 `/etc/pe-scores.env`。
- `PE_HTTPS_ONLY=1`。
