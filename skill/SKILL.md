---
name: minas-webdav
description: 小米 NAS / 小米智能存储（MINAS）的 WebDAV 文件操作助手。用户提到小米NAS、智能存储、MINAS、NAS 上的文件、WebDAV、/pool0、挂 N 盘、往 NAS 传文件/下载/整理 NAS 目录时使用。只做文件层；不碰 SSH/Docker/固件；Z: 盘 Samba 共享请改用 minas skill。
---

# minas-webdav · 小米 NAS WebDAV 文件助手

通过设备 WebDAV（HTTPS :5000）操作 `/pool0/data` 整个存储池：列目录、统计、上传下载、移动复制、删除。不挂盘、不走 Samba。

## 何时使用 / 不用

**使用**：小米 NAS / 智能存储 / MINAS 文件操作、`/pool0` 路径、WebDAV、挂 N 盘、NAS 上的照片/文档/下载目录。

**不要用**：
- Samba 共享与 **Z: 盘**备份（→ skill `minas`）
- SSH / root / Docker / 固件 / 系统分区
- 挂载以外的设备设置类操作

## 前置条件

1. NAS 与本机同一局域网（默认 `192.168.1.100:5000`）
2. 官方客户端登录过（证书 `%LOCALAPPDATA%\minasCert\`）
3. 凭据在仓库外：`%LOCALAPPDATA%\minasCred\credentials.env`（**不写入对话/日志/项目**）

自检：`probe` 一键验证读写链路（见下）。

## 调用方式（必须遵守）

CLI 位于 `F:\Projects\minas-webdav`。**所有命令以该目录为工作目录**：

```powershell
Set-Location F:\Projects\minas-webdav
& $env:MIMO_PYTHON -m minas_webdav <命令> [参数]
```

（`$env:MIMO_PYTHON` 为空时用 `python`。）

## 命令表

| 命令 | 作用 | 示例 |
|---|---|---|
| `ls` | 列目录 | `ls /pool0/data --json` |
| `stat` | 单条元数据 | `stat /pool0/data/test.txt --json` |
| `tree` | 树形一览 | `tree /pool0/data --max-depth 1` |
| `find` | 通配查找 | `find /pool0/data --name "*.mp4" --max-depth 3` |
| `du` | 目录体积 | `du /pool0/data/我的照片 --max-depth 2` |
| `df` | 存储池容量 | `df --json` |
| `get` | 下载到本地 | `get /pool0/data/a.txt ./a.txt` |
| `put` | 本地上传 | `put ./a.txt /pool0/data/agent操作区/a.txt` |
| `cat` | 打印文本 | `cat /pool0/data/test.txt` |
| `mkdir` | 建目录（含父级） | `mkdir /pool0/data/work` |
| `mv` | 移动/改名 | `mv /pool0/data/a.txt /pool0/data/work/a.txt` |
| `cp` | 复制 | `cp src dst` |
| `rm` | 删除 | `rm /pool0/data/work -r` |
| `probe` | 协议能力探测（写测试，自动清理） | `probe` |
| `proxy` | 本机 8899 代理（挂 N 盘用） | `proxy`（常驻，需时单独起） |

路径约定：
- 完整路径 `/pool0/data/...`；简写 `/我的照片` 自动补成 `/pool0/data/我的照片`
- 中文路径 CLI 内部编码，**不要**自己 URL 编码后再传

退出码：`0` 成功 · `1` 错误 · `2` 不存在 · `3` 认证/网络。

## Agent 行为约束（硬规则）

1. **先探后动**：先 `ls` / `du --max-depth`，看清结构再写。
2. **禁止无深度递归**：`du` / `find` / `tree` 必须带 `--max-depth`（大目录如「百度网盘/我的资源」可达数百 GB）。
3. **删除必须征得用户同意**：任何 `rm -r`、批量删照片/备份目录，先问再执行。
4. **批量重处理拉到本地**：`get` → 本地处理 → `put` 回去，不在 WebDAV 上反复随机读。
5. **敏感信息不落盘**：密码、证书、设备 CN 不写入对话、日志、项目文件。
6. **路径只限 `/pool0/data`**：不越池、不碰系统目录。
7. **结构化输出优先 `--json`**，避免解析中文表格。

## 故障排查

| 现象 | 处理 |
|---|---|
| 451 | 凭据缺失 → 检查 `credentials.env`；保持官方客户端登录 |
| 423 Locked | 路径锁（文件删除后仍存活 ≤60s）；等约 1 分钟或换路径重试 |
| 403（PROPFIND） | `Depth: infinity` 被拒，必须 `Depth: 1` 逐层（CLI 已处理） |
| MOVE/COPY 400 | Destination 必须是编码纯路径；**用 CLI，勿手拼** |
| 证书/CN 报错 | 官方客户端重新登录以刷新 `%LOCALAPPDATA%\minasCert\` |
| 代理/挂 N 盘问题 | `proxy` 起在 8899；`scripts\挂载NAS为N盘.ps1`；详见 references/webdav-surface.md |

协议细节（事实/推测分档）见 `references/webdav-surface.md`。

## 与 minas（Samba）的边界

| | `minas` | `minas-webdav`（本 skill） |
|---|---|---|
| 通道 | SMB 445 | WebDAV 5000 |
| 范围 | 仅已发布共享（现仅 Z: 备份盘） | `/pool0/data` 全池 |
| 路径 | `//host/share`、UNC | `/pool0/data/...` |

Z: 盘备份读写 → `minas`；池内其它文件 → 本 skill。两套路径语法不可混用。

## 安全边界

- 仅操作用户自己的 NAS 数据
- 本 skill 与项目仓库不含任何 IP、账号、密码、证书
- `rm -r`、`.temp_upload_dir`、照片/备份目录的删除必须用户点头
