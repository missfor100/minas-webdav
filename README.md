# minas-webdav

小米智能存储（Xiaomi Smart Storage / MINAS）**WebDAV 控制工具箱**——不挂盘、不依赖 Samba，直接通过设备的 HTTPS WebDAV 端点（`:5000`）控制整个 `/pool0/data` 存储池。

三条能力线：

| 入口 | 作用 |
|---|---|
| `python -m minas_webdav <cmd>` | 文件 CLI：列目录、统计、上传下载、移动复制、删除 |
| `python -m minas_webdav probe` | 协议能力探测：读写全链路 + 认证 + 锁 |
| `python -m minas_webdav proxy` | 本机反向代理（`127.0.0.1:8899`），供 Windows `net use` 挂 N 盘 |

## 前置条件

1. NAS 与本机同一局域网（默认 `192.168.1.100:5000`）
2. 官方客户端已登录过（证书在 `%LOCALAPPDATA%\minasCert\`）
3. 凭据文件在**仓库外**：`%LOCALAPPDATA%\minasCred\credentials.env`

```text
MINAS_USER=...
MINAS_PASS=...
```

4. 非机密配置：复制 `minas_webdav/minas_config.example.json` 为
   `minas_webdav/minas_config.json`（已 gitignore），填入真实 `host` / `cn`

## 快速开始

```bash
# 列目录（JSON 输出适合 agent）
python -m minas_webdav ls /pool0/data --json

# 存储池容量
python -m minas_webdav df

# 上传 / 下载（中文路径直接写）
python -m minas_webdav put ./local.txt "/pool0/data/agent操作区/a.txt"
python -m minas_webdav get "/pool0/data/agent操作区/a.txt" ./back.txt

# 整理
python -m minas_webdav mkdir /pool0/data/work
python -m minas_webdav mv /pool0/data/a.txt /pool0/data/work/a.txt
python -m minas_webdav cp /pool0/data/a.txt /pool0/data/a-copy.txt
python -m minas_webdav rm /pool0/data/work -r      # 递删前务必确认！

# 能力探测（写测试用唯一路径，自动清理）
python -m minas_webdav probe

# 本地代理（供挂 N 盘 / Explorer 使用）
python -m minas_webdav proxy
```

也可 `pip install -e .` 后直接用 `minas-webdav` / `minas-webdav-proxy` / `minas-webdav-probe` 命令。

### 挂 N 盘（可选）

```powershell
.\scripts\挂载NAS为N盘.ps1     # 自动拉起代理 + net use N:
.\scripts\删除N盘文件.ps1 <路径> # 删掉资源管理器删不动的文件
```

## 命令一览

| 命令 | 作用 |
|---|---|
| `ls` `stat` `tree` `find` | 浏览（`--max-depth` 防全盘递归） |
| `du` `df` | 目录体积 / 存储池容量 |
| `get` `put` `cat` | 传输 / 查看文本 |
| `mkdir` `mv` `cp` `rm` | 整理（`rm -r` 需用户确认） |

退出码：`0` 成功 · `1` 错误 · `2` 不存在 · `3` 认证/网络。

## 项目结构

```text
minas_webdav/
  config.py    # 配置与凭据加载（minas_config.json + credentials.env）
  cli.py       # 文件 CLI
  probe.py     # 能力探测
  proxy.py     # 本机反向代理（WebClient 兼容调优）
scripts/       # 挂 N 盘 / 清理脚本 + rclone.zip
docs/          # webdav-surface.md 协议实测结论
```

## 协议要点（详见 [docs/webdav-surface.md](docs/webdav-surface.md)）

- `Host`/SNI 必须用**设备 CN**（`nas.<uid>.<hash>`），不能裸用 IP
- `PROPFIND Depth: infinity` 被拒（403），递归必须逐层 `Depth: 1`
- `MOVE`/`COPY` 的 `Destination` 用**百分号编码的纯路径**；IP 形式的 URL 会 400
- 无认证访问返回 451；Basic 即可，mTLS 可选

## 安全约束

- 凭据与设备 CN 不进 Git（`.gitignore` 已挡 `minas_config.json` / `*.env`）
- 不写日志、不打印密码；不碰 SSH/Docker/固件
- 递归删除、批量动照片/备份目录前必须征得用户同意

## Agent 接入

本项目同时提供两种 agent 接入方式（设计见 [docs/agent-integration-design.md](docs/agent-integration-design.md)）：

| 方式 | 位置 | 说明 |
|---|---|---|
| **Skill** | 仓库 `skill/` → 拷到 `~/.config/mimocode/skills/minas-webdav/` | SKILL.md 触发 + 调本 CLI；全局可用 |
| **MCP** | `mcp_server.py` + `mimocode.jsonc` 注册 `minas-webdav` | 14 个 `nas_*` 工具（stdio）；`nas_rm` 递归必须 `confirm=true` |

安装 Skill（MiMoCode）：

```powershell
Copy-Item -Recurse skill "$HOME\.config\mimocode\skills\minas-webdav"
```

注册 MCP（`~/.config/mimocode/mimocode.jsonc` 的 `mcp` 段）：

```jsonc
"minas-webdav": {
  "type": "local",
  "command": ["<python>", "-m", "minas_webdav.mcp_server"],
  "environment": { "PYTHONPATH": "<本仓库路径>" },
  "enabled": true
}
```

MCP 依赖装在项目 venv（`pip install mcp`，2.x）。改 CLI 后请同步更新 SKILL.md 命令表与 MCP 工具签名。

## License

Apache-2.0，见 [LICENSE](LICENSE) 与 [NOTICE](NOTICE)。与 Xiaomi 无关联。

## 相关

- 挂 N 盘可选 [rclone](https://rclone.com)（官方下载；仓库不打包二进制）
- Samba 通道（仅 Z: 备份共享）是另一套：`minas-skill`（SMB 445）
