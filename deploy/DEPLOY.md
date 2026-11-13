# 部署说明（通州云平台 · UOS V20）

服务器：体育部已分配的通州云平台虚拟服务器，SSH 端口 10001，300GB 数据盘需自行分区挂载。Web 只经备案域名、WAF 与防火墙对公网放通 80/443，管理端口只允许堡垒机访问。

## 一键安装（推荐）

从堡垒机登录服务器后，以 root 执行：

```bash
git clone <仓库地址> /root/pe-scores && cd /root/pe-scores
bash deploy/install.sh --domain <备案域名> --data-disk /dev/vdb --admin <工号>:<姓名>
```

脚本会挂载数据盘、用 uv 装 Python 3.12（UOS V20 自带的 Python 版本过低）、装依赖、写配置、起 systemd 服务和 Nginx、配每日备份，并打印应急管理员的初始口令。

下载走国内镜像：uv 和 Python 依赖用清华 PyPI 镜像，Python 本体和 Nginx 用南京大学镜像（清华没有这两个镜像），其余系统软件用 UOS 自带软件源。所有版本固定，清单见 [VERSIONS.md](VERSIONS.md)。可以重复执行；以后更新代码时 `git pull` 后再执行一次即可。服务器连不上代码仓库时，把代码压缩包传到服务器解压到 `/root/pe-scores`，再在里面执行同一条命令。

已经拿到正式证书的，执行前先放到 `/etc/nginx/certs/<备案域名>.crt` 和 `/etc/nginx/certs/<备案域名>.key`（证书文件要含中间证书），脚本就不再生成临时证书。

脚本同时支持用 dnf/yum 装软件的 UOS 服务器版（uelc20，与 CentOS 8 同类）和用 apt 的版本；SELinux 开着或 firewalld 在运行时，会自动放行 Nginx 转发和 80/443 端口。数据盘已挂载过的，去掉 `--data-disk`。以后更新时也去掉 `--admin`：带着它会把应急管理员的口令重置成新的随机口令。

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

UOS 自带源里的 Nginx 是 1.14.1，漏洞扫描会按版本号报出多个 CVE，所以改装 Nginx 官方的稳定版（1.30 及以上），从南京大学镜像下载：

```bash
sudo tee /etc/yum.repos.d/nginx.repo <<'REPO'
[nginx-stable]
name=nginx stable repo
baseurl=https://mirror.nju.edu.cn/nginx/rhel/8/$basearch/
gpgcheck=1
enabled=1
gpgkey=https://mirror.nju.edu.cn/nginx/keys/nginx_signing.key
module_hotfixes=true
REPO
sudo dnf remove -y 'nginx*'          # 已经装过系统源的旧版时才需要，先备份 /etc/nginx
sudo dnf install -y nginx
sudo rm -f /etc/nginx/conf.d/default.conf   # 官方包自带的欢迎页
sudo cp deploy/nginx.conf /etc/nginx/conf.d/pe-scores.conf   # 改成备案域名与证书路径
sudo nginx -t && sudo systemctl enable --now nginx && sudo systemctl reload nginx
nginx -v                             # 应为 1.30 及以上
```

apt 系的 UOS 没有可用的 Nginx 官方源，只能装系统源的版本；一键脚本会给出警告，并在版本低于 1.25 时关掉 HTTP/2。

配置里已经做了这几项加固（对应信息技术中心 2026-11-11 的应用漏洞简报）：

- 80 端口强制跳转 443，用 `http://` 访问 443 端口也跳到 https。
- Host 头不是备案域名的请求直接断开连接（防 Host 头攻击）。因此用服务器 IP 直接访问会打不开；确实要用 IP 访问时，在 `/etc/nginx/conf.d/pe-scores.conf` 里两处带域名的 `server_name` 后面加上 IP。
- 安全响应头：CSP、HSTS（含 includeSubDomains）、X-Frame-Options、X-Content-Type-Options、Referrer-Policy、X-XSS-Protection、X-Download-Options、X-Permitted-Cross-Domain-Policies、Cross-Origin-Opener-Policy、Cross-Origin-Resource-Policy、Permissions-Policy。
- 不显示 Nginx 版本号；只允许 TLS 1.2/1.3，只用 ECDHE 密钥交换（不支持 RSA 密钥交换）。

退出登录接口另带 `Clear-Site-Data: "cache"`，由应用加，只清浏览器缓存。不清 storage：录入页把没保存上的成绩暂存在浏览器本地，下次登录自动补存；不清 cookies：它作用于整个 ruc.edu.cn，会把同域其他系统也登出。这个头只在退出接口上，扫描器扫首页仍可能报「缺少 Clear-Site-Data」，按此说明即可。

证书使用学校统一证书或信息技术中心签发的证书。

### 已上线的服务器更新这份配置

Nginx 配置和应用代码都有改动，重新执行一键脚本即可（去掉 `--data-disk` 和 `--admin`）：

```bash
cd /root/pe-scores && git pull          # 连不上仓库时，把新代码包解压覆盖到这里
sudo bash deploy/install.sh --domain <备案域名>
```

只想单独更新 Nginx 配置时：

```bash
cd /root/pe-scores
sudo cp /etc/nginx/conf.d/pe-scores.conf /root/pe-scores.conf.bak.$(date +%Y%m%d)
sudo sed -e "s#pe.example.edu.cn#<备案域名>#g" deploy/nginx.conf | sudo tee /etc/nginx/conf.d/pe-scores.conf >/dev/null
sudo nginx -t && sudo systemctl reload nginx     # -t 报错就把备份拷回去
```

更新后在服务器上自查：

```bash
curl -skI https://<备案域名>/ --resolve <备案域名>:443:127.0.0.1 | grep -iE 'content-security|strict-transport|permissions'
curl -sI http://127.0.0.1/ -H 'Host: evil.com'      # 应为 Empty reply（连接被断开）
curl -sI http://<备案域名>:443/ --resolve <备案域名>:443:127.0.0.1 | grep -i location   # 应跳到 https
```

以后要升级 Nginx，执行 `sudo dnf upgrade -y nginx`，或者重新执行一键脚本。

## 5. 备份

```bash
sudo cp deploy/pe-scores-backup.cron /etc/cron.d/pe-scores-backup
```

每天 02:30 用 SQLite 在线备份到 `/data/pe-scores/backups/`，保留 60 天。建议再定期同步一份到信息技术中心的备份存储。

## 6. 安全清单

- 首次登录修改服务器初始管理口令（≥8位，三类字符）。
- 防火墙只对公网放通 80、443；SSH 10001 只允许堡垒机来源。
- ICMP 时间戳（系统漏洞简报里唯一的低危项）用 iptables 屏蔽。不要为此去启动 firewalld，它的默认规则不放行 SSH 的 10001 端口。见下面「服务器侧加固」。
- 关掉用不到的 rpcbind（111 端口），见下面「服务器侧加固」。
- SSH 关掉弱算法（见下面「SSH 加固」）。
- 统一认证启用前 `PE_AUTH_MODE=local`；填好 `PE_SSO_CLIENT_SECRET` 后改为 `sso`。生产环境禁止使用 `dev`。
- 数字人大账号、appid 的 secret 等凭据不写进代码仓库和文档，只写在 `/etc/pe-scores.env`。
- `PE_HTTPS_ONLY=1`。

## 7. SSH 加固

漏洞扫描会报「SSH 协议支持的算法」和「SSH 版本信息可被获取」。改 SSH 配置会影响运维登录，一键脚本不动它，由管服务器的人手动改。改之前先另开一个 SSH 会话不要关，改坏了还能用它改回来。

1. 在 `/etc/ssh/sshd_config` 末尾加上：

   ```
   KexAlgorithms curve25519-sha256,curve25519-sha256@libssh.org,diffie-hellman-group16-sha512,diffie-hellman-group18-sha512,diffie-hellman-group-exchange-sha256
   Ciphers chacha20-poly1305@openssh.com,aes256-gcm@openssh.com,aes128-gcm@openssh.com,aes256-ctr,aes192-ctr,aes128-ctr
   MACs hmac-sha2-512-etm@openssh.com,hmac-sha2-256-etm@openssh.com,umac-128-etm@openssh.com
   ```

2. dnf 系的 UOS 默认用系统加密策略覆盖上面这几行。`/etc/sysconfig/sshd` 里有 `CRYPTO_POLICY=` 开头的注释行时，把它改成不带 `#` 的 `CRYPTO_POLICY=`（等号后面留空）。
3. 检查并重启：

   ```bash
   sudo sshd -t && sudo systemctl restart sshd
   sudo sshd -T | grep -E '^(kexalgorithms|ciphers|macs)'   # 应与第 1 步一致
   ```

4. 用新会话登录一次，确认能登上再关掉旧会话。

SSH 的版本号是协议握手的一部分，不重新编译 OpenSSH 去不掉。这一项只是信息泄露，SSH 端口只允许堡垒机访问就够了。

## 8. 服务器侧加固

对应信息技术中心 2026-11-11 的系统漏洞简报，和网站代码无关，由管服务器的人执行。

1. 关掉 rpcbind（111 端口）。本系统用不到；服务器挂了 NFS 的先别关：

   ```bash
   mount | grep -q nfs && echo "有 NFS 挂载，先别关" || sudo systemctl disable --now rpcbind rpcbind.socket
   ss -lntu | grep ':111 ' || echo "111 端口已关闭"
   ```

2. 屏蔽 ICMP 时间戳请求和应答，并保存成开机自动加载：

   ```bash
   sudo iptables -I INPUT -p icmp --icmp-type timestamp-request -j DROP
   sudo iptables -I OUTPUT -p icmp --icmp-type timestamp-reply -j DROP
   sudo iptables -S | grep timestamp
   # dnf 系：装 iptables-services 后保存（不要启动 firewalld）
   sudo dnf install -y iptables-services && sudo service iptables save && sudo systemctl enable iptables
   ```

   `service iptables save` 会把当前全部规则写进 `/etc/sysconfig/iptables`。保存前先 `sudo iptables -S` 看一眼，确认里面没有会挡住 10001 端口的规则。

## 9. 漏洞扫描里不修的项

和学校说明即可：

- Server 头显示 nginx：版本号已隐藏。官方版 Nginx 去不掉这个头，要额外编译模块，不值得。
- 检测到反向代理、SSL 协议与加密算法、证书详情、SSH 支持的算法：报告自己标注为探测类，无需修改（SSH 算法已按第 7 节收紧）。
- SSH 版本信息可被获取：OpenSSH 握手协议本身要报版本，去不掉。10001 端口只对堡垒机开放即可。
- 操作系统识别为 Linux 2.6.18：扫描器误判，不用处理。
