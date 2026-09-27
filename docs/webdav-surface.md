# 小米智能存储 WebDAV 能力面 · 实测结论

> 实测日期：**2026-09-27**（复测）；首测 2026-09-23/26
> 端点：`https://<NAS_IP>:5000`，SNI / `Host` = 设备 CN
> 后端：`nginx/1.24.0`
> 复现：`python -m minas_webdav probe`

## 1. 事实（当日实测可复现）

### 端点与认证

| 项 | 结果 |
|---|---|
| `OPTIONS` | 200；`DAV: 1,2`；`Allow: GET,HEAD,PUT,DELETE,MKCOL,COPY,MOVE,PROPFIND,OPTIONS,LOCK,UNLOCK` |
| Basic 认证 | `PROPFIND` → 207 ✓（账密来自 `get_pool_info` 或 credentials.env） |
| 无认证 | → **451**（设备上锁） |
| mTLS | 可选；证书 `%LOCALAPPDATA%\minasCert\`（cert/key/ca_chain） |
| Host/SNI | 必须为设备 CN（`nas.<uid>.<hash>.0`）；MKCOL 返回的 `Location` 头也用 CN 形式 |

### 读

| 操作 | 结果 |
|---|---|
| `PROPFIND Depth: 0` | 207 ✓ |
| `PROPFIND Depth: 1` | 207 ✓ |
| `PROPFIND Depth: infinity` | **403**（nginx 拒绝，递归必须逐层） |
| `GET` 文件 | 200 ✓（字节原样） |
| `GET Range: bytes=0-1` | **206** ✓ |
| `GET` 目录 | 200，返回 **HTML 索引**（不可当程序列表用，列目录走 PROPFIND） |

### 写

| 操作 | 结果 |
|---|---|
| `PUT` 新建 | 204 ✓ |
| `PUT` 覆盖 | 204 ✓ |
| `MKCOL` | 201 ✓（`Location` 头为 CN 完整 URL） |
| `MOVE`（`Destination` = 百分号编码纯路径） | **204** ✓ |
| `COPY`（同上） | **204** ✓ |
| `MOVE/COPY`（`Destination` = `https://<IP>:5000/...`） | **400** ✗ |
| `DELETE` 文件/目录 | 204 ✓；不存在 → 404；被锁 → **423** |
| `LOCK` | 200/201 ✓（返回 `lockdiscovery`，token 在 `<D:locktoken><D:href>`） |
| `UNLOCK` | 204 ✓（`Lock-Token: <opaquelocktoken:...>`，带尖括号） |

### 锁的坑（2026-09-27 发现）

- `LOCK Timeout: Second-60` 后，**即使文件已被 DELETE，同路径上的锁仍存活到超时**（423 持续约 60 秒）。
- 因此：连续两次 probe 若用同一路径，第二次会全程 423。`probe` 已改为**每次生成唯一路径**（pid+时间戳）。
- `DELETE` 整个目录返回 204 时会连同被锁子文件一起清掉（实测），但子文件单独 `DELETE` 仍是 423。

### 中文路径

- 请求行按段百分号编码（`cli.enc_path`）即可，`agent操作区` 等实测通过。
- **`Destination` 头必须先编码**：header 值对 `http.client` 是 latin-1，裸中文会抛
  `UnicodeEncodeError`（本项目已修复：`dest_header()` → `enc_path(norm(...))`）。

### 机密发现（LuCI，配合 mTLS）

- `POST /cgi-bin/luci/filemgr/get_pool_info`（`:443` 或本机隧道 `127.0.0.1:51087`）
  返回 `data.webDAV.username/password` 与 `data.internal_pool`（`df` 命令数据源）。

## 2. 推测（未验证，仅供参考）

| 观察 | 推测 |
|---|---|
| `Depth: infinity` 403 | nginx `dav_ext` 模块默认 `dav_methods` 限制或厂商主动关闭，防全盘遍历 |
| 451 状态码 | nginx 用 451 表示"需认证"是小米定制，非标准 401/403 |
| 锁删除后残留 | 锁表按路径 key 存储、与 inode 脱钩；也可能是实现简化 |
| 目录 GET 出 HTML 索引 | nginx `autoindex` 对 WebDAV collection 生效，非 WebDAV 特性 |
| PUT 204 而非 201 | 服务端对新建/覆盖统一回 204，不区分（RFC 允许） |

## 3. 与其它通道的关系

| | Samba（445） | WebDAV（5000，本项目） |
|---|---|---|
| 范围 | 仅官方发布的共享（现仅 Z: 备份） | `/pool0/data` 全池 |
| 认证 | Windows SMB 会话 | Basic（+可选 mTLS） |
| 用途 | 资源管理器 / 备份 | agent 自动化、远程、挂 N 盘 |

两者独立，不要混用路径语法（UNC ≠ WebDAV 路径）。
