# minas-webdav · Agent 接入技术方案（Skill vs MCP）

> 状态：**设计稿，待确认后实施**
> 日期：2026-09-27
> 依据：`F:\Projects\minas-webdav` 已交付并实测通过的 CLI / probe / proxy；MiMo skill 规范（mimo-skill-authoring）与 MCP 开发规范（mcp-builder）
> 结论先行：**推荐阶段 1 做 Skill（成本低、零新进程），阶段 2 按需补 MCP**

---

## 1. 背景与目标

`minas-webdav` 项目已提供完整的 WebDAV 控制面：

```text
python -m minas_webdav <cmd>   # 13 个文件命令（ls/df/put/get/mv/cp/rm/...）
python -m minas_webdav probe   # 协议能力探测
python -m minas_webdav proxy   # 本机 8899 反向代理（挂 N 盘）
```

但目前 agent 要用它，必须先**知道**这个项目存在、记得路径和命令。接入层要解决三件事：

1. **可发现**：用户提到"小米 NAS / WebDAV / 池子上的文件"时，agent 自动知道有这套工具
2. **可用得对**：中文路径、`--max-depth`、Destination 编码等坑由工具层兜底，不靠 agent 记忆
3. **可控**：删除确认、路径边界、凭据不落盘等约束有机制保障，不只靠口头约定

## 2. 现状资产

| 资产 | 状态 | 在本方案中的角色 |
|---|---|---|
| `minas_webdav/cli.py` | ✅ 实测全过（含中文 mv/cp） | Skill 与 MCP 的共同内核 |
| `minas_webdav/probe.py` | ✅ 读写全链路 + LOCK/UNLOCK | 安装验收 / 故障自检 |
| `minas_webdav/proxy.py` + `scripts/挂载*` | ✅ 代理 207 实测 | 不进 Skill/MCP，保持独立 |
| `minas_webdav/config.py` | ✅ 配置/凭据分离 | 两方案共用同一认证加载 |
| `docs/webdav-surface.md` | ✅ 事实/推测分档 | SKILL.md 故障排查的依据 |
| 旧 Samba skill（`minas-skill`） | ✅ 独立存在 | **不合并**，见 §5.4 边界 |

## 3. 两条路线对比

| 维度 | 方案 A · Skill（MiMo 原生） | 方案 B · MCP Server |
|---|---|---|
| 形态 | `SKILL.md` + 直接调现有 CLI | 常驻进程，暴露结构化 tools |
| 触发 | 对话命中触发词后**按需加载**（渐进披露） | 工具清单**常驻**系统上下文（每轮消耗 token） |
| 进程模型 | 无新进程；每命令一次 Python 启动（~0.3s + 网络） | 单常驻进程；配置/连接只初始化一次 |
| 参数校验 | 靠 SKILL.md 约定 + argparse 兜底 | JSON Schema 强校验，类型错误直接拒绝 |
| 结构化输出 | CLI `--json`（agent 自己选） | 工具级 `structuredContent` / `outputSchema` |
| 安全护栏 | 文档约束（删除前确认）+ 退出码 | `destructiveHint` 注解 + 强制 `confirm` 参数 |
| 跨客户端 | 仅 MiMo（及兼容 skill 的宿主） | 任何 MCP 客户端（Claude Code、Cursor…） |
| 实现成本 | **低**：写 SKILL.md + locales，约 1 小时 | 中：venv + `mcp` 依赖 + server + 注册 + 测试 |
| 维护成本 | 低（CLI 变了改文档） | 中（schema 与 CLI 同步演进） |
| 失败模式 | agent 忘记加载/不守约束 | 服务未启动 / 注册配置失效 |

### 3.1 关键判断

- **MiMo 内主用场景下，Skill 的短板（无 schema）由 CLI 的 argparse + `--json` 基本补齐**；MCP 的常驻工具清单反而是每轮固定 token 开销。
- **MCP 的独特价值**在跨客户端和强护栏：如果以后要在 Claude Code / Cursor 里操作 NAS，或者希望"递归删除必须带 `confirm=true`"由协议层强制，才值得做。
- 两者**共享同一内核**（`minas_webdav` 包），先做 Skill 不会浪费：MCP server 只是再包一层薄壳。

## 4. 推荐路线：分两阶段

```text
阶段 1（本次确认后即可做）  →  Skill minas-webdav（全局）
        │  验收：触发词命中 → 正确调用 CLI → 删除前会向用户确认
        │
阶段 2（可选，满足任一条件再做）
        ├─ 需要在 MiMo 以外的 MCP 客户端操作 NAS
        └─ 需要协议级强制护栏 / 结构化输出
        →  MCP server（stdio）+ 在 mimocode.jsonc 注册
```

## 5. 方案 A · Skill 详细设计

### 5.1 位置与标识

| 项 | 值 |
|---|---|
| ID（目录名 = frontmatter `name`） | `minas-webdav` |
| 位置 | **全局** `~/.config/mimocode/skills/minas-webdav/SKILL.md`（跨项目可用；NAS 是机器级资源） |
| 元数据 | `locales/zh-CN.json`、`locales/en-US.json`（各含 `displayName` + `brief`，不重复 name/description） |
| 不写在 frontmatter 里 | 密码、CN、IP（凭据仍走 `credentials.env`） |

### 5.2 目录结构

```text
~/.config/mimocode/skills/minas-webdav/
  SKILL.md                 # 触发、前置、命令表、约束、排查
  locales/
    zh-CN.json             # {"displayName": "小米NAS WebDAV助手", "brief": "..."}
    en-US.json
  references/
    webdav-surface.md      # 从项目 docs/ 拷贝（协议事实，渐进披露第二层）
```

### 5.3 SKILL.md 大纲（按此顺序写）

1. **frontmatter**
   - `name: minas-webdav`
   - `description`（触发引擎匹配用，中文，含触发词）：小米 NAS / 小米智能存储 / MINAS 的 WebDAV 文件操作；提到 NAS 文件、`/pool0`、WebDAV、挂 N 盘、传文件到 NAS、列 NAS 目录时使用。只做文件层。
2. **何时使用 / 何时不用**
   - 不用：Samba 共享与 Z: 盘（→ 另一个 skill `minas`）、刷机/SSH/Docker、挂载以外的系统操作
3. **前置条件**（凭据文件、证书、局域网连通；一键自检命令 `probe`）
4. **调用方式**（关键：包在项目里，必须带工作目录）
   ```powershell
   # 工作目录固定 F:\Projects\minas-webdav
   & $env:MIMO_PYTHON -m minas_webdav ls /pool0/data --json
   ```
   约定：所有命令以 `F:\Projects\minas-webdav` 为 cwd；或用 `PYTHONPATH` 方式（二选一，实施时定）
5. **命令表**（13 命令 + `probe` + `proxy` 入口，各配一行示例；结构化输出统一 `--json`）
6. **Agent 行为约束**（硬规则）
   - 先 `ls`/`du --max-depth` 后动手；`du`/`find`/`tree` 无 `--max-depth` 视为违规
   - `rm -r`、批量动照片/备份目录：**必须先征得用户同意**再执行
   - 批量重处理：`get` → 本地 → `put`，不在 WebDAV 上反复随机读
   - 凭据/证书/CN 不写入对话、日志、项目文件
   - 路径只允许 `/pool0/data` 下（不越池）
7. **故障排查表**（来自 webdav-surface 实测）
   - 451 → 凭据缺失；423 → 路径锁 ≤60s，等重试；403 infinity → 逐层递归
   - MOVE/COPY 400 → Destination 用编码纯路径；中文编码错 → 已由 CLI 修复，勿绕过 CLI 手拼
8. **退出码**：0/1/2/3 含义

### 5.4 与 Samba skill（`minas`）的边界

| | `minas`（已有） | `minas-webdav`（新） |
|---|---|---|
| 通道 | SMB 445 | WebDAV 5000 |
| 范围 | 仅已发布共享（Z: 备份盘） | `/pool0/data` 全池 |
| 路径语法 | `//host/share`、UNC | `/pool0/data/...` |
| 冲突处理 | SKILL.md 互相写明"对方负责什么"，Z: 盘优先走 `minas` | 同左 |

### 5.5 验收标准

- [ ] 新会话里说"列出小米 NAS 我的照片目录"→ 自动加载 skill → 正确命令 → 中文路径成功
- [ ] 说"删掉 NAS 上 xxx 整个目录"→ agent **先询问确认**再执行
- [ ] `probe` 作为安装自检命令可跑通
- [ ] 插件页显示 zh-CN `displayName`/`brief`
- [ ] frontmatter 合法（name 仅字母数字连字符）

## 6. 方案 B · MCP Server 详细设计（阶段 2）

### 6.1 形态与技术栈

| 项 | 值 |
|---|---|
| 框架 | Python SDK（FastMCP）——内核是 Python，复用 `minas_webdav` 包；不重写为 TS |
| 依赖 | 项目内独立 venv：`pip install mcp`（**不污染 `MIMO_PYTHON`**） |
| 传输 | **stdio**（本机单用户；无 HTTP 需求） |
| 入口 | `minas_webdav/mcp_server.py` → `python -m minas_webdav.mcp_server` |
| 认证/配置 | 复用 `minas_webdav/config.py`，启动时加载一次，无新增凭据面 |

### 6.2 工具清单（14 个，命名前缀 `nas_`）

| 工具 | 输入 | 输出 | 注解 |
|---|---|---|---|
| `nas_ls` | `path, limit=500` | 条目数组 + `truncated` | readOnly |
| `nas_stat` | `path` | 单条元数据 | readOnly |
| `nas_tree` | `path, max_depth=2` | 树文本/JSON | readOnly |
| `nas_find` | `path, name?, max_depth=3, files_only?` | 匹配数组 | readOnly |
| `nas_du` | `path, max_depth=2` | `{name,bytes,files}[]` | readOnly |
| `nas_df` | — | 池容量 JSON | readOnly |
| `nas_cat` | `path, max_bytes=65536` | 文本（超限截断+提示） | readOnly |
| `nas_get` | `remote, local` | 保存路径+字节数 | 非只读，幂等 |
| `nas_put` | `local, remote` | 上传结果 | 非只读，幂等（覆盖） |
| `nas_mkdir` | `path` | 结果 | 非只读，幂等 |
| `nas_mv` | `src, dst, force=false` | 结果 | 非只读，**非幂等** |
| `nas_cp` | `src, dst, force=false` | 结果 | 非只读，幂等（覆盖） |
| `nas_rm` | `path, recursive=false, **confirm**` | 结果 | **destructive** |
| `nas_probe` | — | 能力探测摘要 | readOnly（写测试用唯一路径） |

设计要点：

- **`nas_rm` 护栏**：`recursive=true` 时必须显式传 `confirm=true`，且 description 写明"仅当用户已明确同意递归删除"；服务端校验缺失直接报错。这是 MCP 相对 Skill 的核心增益。
- **大目录**：`nas_ls` 带 `limit`（默认 500）+ `truncated` 标记，防单次响应撑爆上下文（WebDAV 无 offset 分页，截断即可，续查用 `nas_find`/更深层路径）。
- **输出**：一律 JSON `structuredContent`；错误返回可执行的建议（"凭据缺失 → 检查 %LOCALAPPDATA%\minasCred\credentials.env"），不用裸异常串。
- **`nas_get`/`nas_put`**：描述中注明"大文件建议仍走 CLI 或代理挂载"，MCP 传大 blob 不划算（默认单次上限如 32MB，超出报错指路）。

### 6.3 注册（`~/.config/mimocode/mimocode.jsonc` 的 `mcp` 段）

```jsonc
"minas-webdav": {
  "type": "local",
  "command": [
    "F:\\Projects\\minas-webdav\\.venv\\Scripts\\python.exe",
    "-m", "minas_webdav.mcp_server"
  ],
  "environment": { "PYTHONPATH": "F:\\Projects\\minas-webdav" },
  "enabled": true
}
```

> 改完需重启引擎 / 新会话生效。

### 6.4 实施与测试步骤

1. 建 venv：`F:\Projects\minas-webdav\.venv`，`pip install mcp`
2. 实现 `mcp_server.py`（Pydantic 模型 + `@mcp.tool` + 注解）
3. `python -m py_compile` → MCP Inspector（`npx @modelcontextprotocol/inspector`）手工过一遍只读工具
4. 真机验收：`nas_ls` 中文路径、`nas_rm` 缺 confirm 被拒、超限截断生效
5. 注册进 `mimocode.jsonc`，新会话验证工具列表出现
6. （可选）按 mcp-builder 规范做 10 条只读评测题

### 6.5 验收标准

- [ ] Inspector 中 14 工具 schema 可读、只读工具标注 `readOnlyHint`
- [ ] `nas_rm(recursive=true)` 无 `confirm` → 结构化错误
- [ ] MiMo 新会话可见工具并完成一次端到端"列目录→下载"
- [ ] 凭据不出现在任何 tool 响应/日志中

## 7. 安全设计（两方案通用）

| 风险 | 对策 |
|---|---|
| 凭据泄露 | 仍只在 `credentials.env`（仓库外）；Skill/MCP 响应中永不回显；MCP 启动失败信息不带 env 内容 |
| 误删 | Skill：SKILL.md 强制确认；MCP：`confirm` 参数协议级强制 + `destructiveHint` |
| 越权路径 | 内核 `ensure_data_path` 已锚定 `/pool0/data`；MCP 层再校验一次，拒绝 `..` 逃逸 |
| 超大递归拖死会话 | `max_depth`/`limit` 默认值保守；文档禁止无深度递归 |
| 旧通道混淆 | 两 skill 互相声明边界；Z: 盘走 `minas`，池文件走 `minas-webdav` |
| 证书即钥匙 | 不复制 `minasCert`，不进 Git（现状保持） |

## 8. 工作量与里程碑

| 阶段 | 内容 | 预估 |
|---|---|---|
| **P1 · Skill** | SKILL.md + locales + references 拷贝 + 真机验收 5 条 | ~1 小时 |
| **P2 · MCP**（可选） | venv + server 14 工具 + Inspector 测试 + 注册 | ~2–3 小时 |
| 回归 | CLI 改动时同步 SKILL.md / schema（写进项目 README 的维护清单） | 持续 |

## 9. 待你确认的点

1. **路线**：只做 P1 Skill？还是 P1 + P2 一起做？（推荐：先 P1，用一周再决定 P2）
2. **Skill 位置**：全局 `~/.config/mimocode/skills/minas-webdav`（推荐）还是项目级？
3. **调用方式**：cwd 固定项目目录（推荐，零安装）还是 `pip install -e` 进 venv 换 PATH 级调用？
4. **Skill ID**：`minas-webdav`（推荐，与 Samba 的 `minas` 区分）或其它名字？
5. **P2 若做**：是否接受项目内建 `.venv` 装 `mcp` 依赖？

---

**确认后我按所选路线实施：P1 直接建 skill 并验收；P2 追加 venv + server + 注册。**
