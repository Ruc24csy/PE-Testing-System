# 软件与版本清单

用一键脚本 `deploy/install.sh` 安装时，服务器上装出来的就是下面这些版本。填备案、等保材料时照这张表写。

## 运行环境

| 类别 | 软件 | 版本 | 来源 |
|---|---|---|---|
| 操作系统 | 统信 UOS 服务器版（UnionTech OS Server 20，uelc20） | V20 | 通州云平台分配 |
| Web 服务器（中间件） | Nginx | 1.30.6（官方稳定版，重复执行脚本会升到最新稳定版） | 南京大学镜像（Nginx 官方 RHEL 8 包） |
| 开发语言 | Python | 3.12.16 | 南京大学镜像（python-build-standalone） |
| 数据库 | SQLite（单文件，随 Python 自带，无独立数据库服务） | 3.53.1 | 随 Python 一起 |
| 应用服务器 | Uvicorn | 0.54.0 | 清华 PyPI 镜像 |
| 安装工具 | uv | 0.13.7 | 清华 PyPI 镜像 |

Nginx 版本是 2026-11-11 在南京大学镜像上查到的最新稳定版；官方发了新的稳定版后以服务器上 `nginx -v` 为准。apt 系的 UOS 装的是系统源里的版本。

## 后端主要组件

| 组件 | 用途 | 版本 |
|---|---|---|
| FastAPI | Web 框架 | 0.162.4 |
| Starlette | Web 框架底层 | 1.10.0 |
| Pydantic | 数据校验 | 2.14.3 |
| SQLAlchemy | 数据库访问 | 2.1.8 |
| openpyxl | 读写 Excel | 3.1.5 |
| httpx | 对接统一身份认证 | 0.28.1 |
| itsdangerous | 登录会话签名 | 2.2.0 |
| python-multipart | 文件上传 | 0.0.33 |

全部 28 个 Python 包（含间接依赖）的确切版本见仓库根目录的 `requirements.lock`。

前端是原生 HTML/CSS/JavaScript，不使用前端框架，不引用外网 CDN。

## 升级版本

1. 改 `requirements.txt` 里的版本号。
2. 重新生成 `requirements.lock`：

   ```bash
   uv pip compile --universal --python-version 3.12 --no-header \
     --default-index https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple \
     requirements.txt -o requirements.lock
   ```

3. 跑测试，更新本表；升级 Python 或 uv 时同时改 `deploy/install.sh` 开头的 `PYTHON_VERSION`、`UV_VERSION`。
