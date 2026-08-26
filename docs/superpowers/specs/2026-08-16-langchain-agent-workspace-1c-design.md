# LangChain Agent 工作台 1C 设计

**日期：** 2026-08-16

**状态：** 已完成交互式设计确认和书面复审，待实施计划

**上位设计：** `docs/superpowers/specs/2026-08-15-langchain-agent-workspace-design.md`

**实现基线：** 1A Agent 垂直闭环和 1B 服务端权威会话已经完成；1C 不重写其 AG-UI、Graph、thread/run 存储或 retry 合同。

## 1. 目标

1C 为现有 Agent 工作台增加两类用户可管理能力：

1. 用户目录中的完整 Skill，包括安全导入、校验、逐会话选择、渐进加载和受控资源读取。
2. stdio 与 Streamable HTTP MCP Tools，包括无密钥配置、连接测试、工具发现、命名空间、逐次审批和当前 thread session 临时授权。

1C 保持以下稳定边界：

- 管理操作使用 REST；活跃 run 和审批恢复继续使用 AG-UI。
- thread JSON 仍是所选 Skill 的权威来源，客户端不得在 `/run` 请求中临时注入 Skill 或 MCP 配置。
- 内置投研工具继续自动执行；只有 MCP 工具默认需要审批。
- 每次新产品 run 使用不可变、无密钥的 capability snapshot；纯 resume 复用原 snapshot 和 MemorySaver。
- 现有 `chat.SYSTEM_PROMPT` 的客观中立红线保持最高优先级。Skill 指令和 MCP 元数据均是用户安装的外部输入，不能替换或削弱系统规则。

## 2. 范围与切片

1C 是一个统一验收的里程碑，内部按三个始终可提交、可回归的切片推进：

1. Skill 发现、导入与渐进加载。
2. MCP 配置、连接与工具注册。
3. 审批、thread session allowance 与前端交互。

切片 2 的“工具注册”只到管理面连接、发现、alias 和持久化 catalog 为止。此时 `CapabilityResolver` 和 `AgentFactory` 仍不向 Graph 暴露任何 MCP alias，测试必须断言 run 只能看到内置工具和 Skill 工具。切片 3 的可提交完成态必须同时接入 MCP binding、参数 guard 与 HITL middleware；开发过程可以红绿小步推进，但不能把可执行而无审批的中间状态作为提交点。

每个切片完成时，全仓非 live 测试和前端 build 必须保持绿色。只有三个切片和 1C 浏览器验收全部完成后，才能标记 1C 完成。

## 3. 明确不做

- 不实现 Artifact、运行预算、上下文裁剪或最终 Inspector；这些属于 1D。
- 不提前实现最终三栏工作台。
- 不支持 MCP OAuth、Resources、Prompts、SSE transport、WebSocket transport 或服务器市场。
- 不执行 Skill `scripts/`，不提供 shell、Python 或其他代码执行工具。
- 不提供永久 MCP 工具信任；allowance 只存在当前后端进程内。
- 不把 MCP header、stdio env 或模型密钥写入 JSON、Graph state、checkpoint、SSE 或日志。
- 不在 1C 中迁移现有 `/api/chat`、AI 复盘、辩论、反思或 CLI 订阅入口。
- 不支持多 worker 或多个后端实例共享同一 Agent 数据目录。

## 4. 已确认的总体架构

1C 将系统分成管理面和运行面：

```text
React /agent
  -> CapabilityBar
  -> CapabilityManagerDialog
       -> Skills tab
       -> MCP servers tab
  -> AgentThread
       -> ApprovalPanel
       -> SteerAwayComposer

FastAPI agent.router
  -> Skill REST
       -> SkillRegistry
       -> SkillImporter
  -> MCP REST
       -> McpConfigStore
       -> McpRegistry
  -> existing /run
       -> RunCoordinator admission preview
       -> CapabilityResolver
            -> SkillRuntime snapshot
            -> McpRegistry tool bindings
            -> ApprovalPolicy
       -> immutable CapabilityLease
       -> existing AgentFactory / RuntimeHandle
       -> existing AgentProtocolBridge / RunJournal

Local user data
  -> agent/skills/<directory>/...
  -> agent/mcp.json
  -> agent/mcp-work/<server-id>/
```

### 4.1 组件职责

`SkillRegistry`：

- 扫描 Skill 根目录并产生一个不可变 generation。
- 解析 frontmatter、验证文件树并构建受控 manifest。
- 以规范化后的 Skill name 解析条目，不接受调用方拼接真实路径。
- 保留无效条目及其错误供 UI 显示，但不允许选择或加载。

`SkillImporter`：

- 分块接收 zip，执行体积、数量、文件类型和路径检查。
- 在 Skill 根目录同一文件系统内暂存、校验、原子落位。
- 恢复覆盖过程中遗留的自有 stage/backup 目录。

`McpConfigStore`：

- 原子读写一个带 revision 的 `mcp.json`。
- 只保存连接描述、环境变量引用、信任指纹、工具目录和脱敏 health。
- 文件损坏时保留原文件并整体失败关闭，不用空配置覆盖。

`McpRegistry`：

- 解析环境变量引用，按需建立和缓存 stdio/HTTP MCP session。
- 使用官方 `langchain-mcp-adapters` 发现并转换 Tools，不实现 MCP framing。
- 为每个 server 串行执行工具；不同 server 之间允许并行。
- 维护配置/catalog generation、session generation、活跃调用引用、异步关闭和 stale session 淘汰。
- 原始 secret 只存在 Registry 的连接对象内。

`CapabilityResolver`：

- 从 Coordinator preview 返回的权威 `selected_skills` 解析 Skill generation。
- 组合内置工具、两个 Skill 工具，以及从切片 3 起才进入运行面的全局启用 MCP 工具。
- 构建动态 MCP 审批策略。
- 返回 `CapabilityLease`，不修改 thread/run 文档。

`CapabilityLease`：

- 持有 Skill runtime snapshot、无密钥的 Registry MCP bindings、审批 policy factory 和配置/catalog generation 引用；不持有绑定具体 session 的 adapter Tool 或 request-scoped middleware 实例。
- 不持有模型密钥、MCP env/header 值或可序列化的连接配置。
- 准入失败时立即释放；成功后归 `ActiveRunHandle` 所有。
- 在 completed、failed、cancelled、steer-away、Graph 构建失败和 shutdown 路径中恰好释放一次。

`AllowanceRegistry`：

- 只在内存中保存 `(thread_id, server_id, original_tool_name)`。
- 为 `HumanInTheLoopMiddleware.when` 提供同步、无 I/O 查询。
- thread 删除、用户清除、相关 server 配置变更或后端退出时删除对应 allowance。

### 4.2 文件边界

实施时按以下职责拆分，避免继续扩大已经承担协议和持久化职责的 `router.py` 与 `runs.py`：

- `agent/skills.py`：Skill 模型、扫描、导入、manifest、runtime tools。
- `agent/mcp.py`：MCP 模型、配置 store、连接 registry、tool alias 与 adapter 集成。
- `agent/capabilities.py`：resolver、lease、allowance 与审批 policy。
- `agent/tool_registry.py`：保留内置工具适配，并提供最终工具集合的窄组合入口。
- `agent/router.py`：REST/AG-UI transport 和错误映射，不实现文件扫描或 MCP protocol。
- `agent/runs.py`：准入 preview、最终重校验、handle/lease 生命周期和 allowance 原子更新。
- `agent/protocol.py`：标准 interrupt/resume 线协议，不直接访问 Registry。

具体文件数量可以在实施计划中合并，但上述职责边界不能合并回单个全能模块。

## 5. 依赖兼容方案

### 5.1 已验证冲突

截至 2026-08-16：

- `langchain-mcp-adapters==0.3.2` 要求 `mcp>=1.24.0,<2.0.0`。
- `mcp==1.26.0` 要求 `httpx>=0.27.1`。
- 最新 `mootdx==0.11.7` 要求 `httpx>=0.25.0,<0.26.0`。

因此上游 `mootdx` 与当前 MCP SDK 无法由同一个 pip resolver 正常安装。

1C 的硬约束是：

- 不回退现有 mootdx K 线、财务和 F10 能力。
- 继续支持一次 `pip install -r backend/requirements.txt` 完成后端安装。
- 不增加第二个 Python 环境、helper daemon 或自研 MCP framing。

### 5.2 仓库内 mootdx 兼容包

仓库加入 `backend/vendor/mootdx_compat/`，其 distribution name 保持 `mootdx`，版本标为 `0.11.7+vr1`：

- Python 源码逐文件保持上游 0.11.7 不变。
- 保留上游 MIT `LICENSE` 和来源说明。
- 只用本地 `pyproject.toml` 将 `httpx` 约束改为 `>=0.27.1,<1`。
- 提交上游 Python 文件摘要 manifest；测试断言除允许文件外没有本地业务修改。
- `requirements.txt` 通过本地路径安装该 distribution，不再同时声明 PyPI `mootdx`。

1C 同时直接锁定：

```text
langchain-mcp-adapters==0.3.2
mcp==1.26.0
httpx==0.28.1
```

锁定版本升级必须先运行 mootdx 与 MCP 两组合同测试。不能通过 `--no-deps`、legacy resolver 或文档中的手工安装步骤绕过冲突。

## 6. Skill 模型与发现

### 6.1 目录和身份

Skill 根目录是 `<VR_DATA_DIR>/agent/skills/`。Registry 只扫描其直接子目录；自有 import 临时目录和备份目录不参与正常扫描。

Skill 的稳定身份来自 `SKILL.md` YAML frontmatter 的 `name`，不是目录名。目录名可以不同，但所有 REST、Agent tool 和 thread `selected_skills` 都使用 registry name。

`name` 必须：

- 长度 1-64。
- 匹配 `[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?`。
- 在 Unicode NFC 和 casefold 后与其他 Skill 不冲突。

`description` 必须是长度 1-1024 的字符串。`SKILL.md` 必须是 UTF-8，文件不超过 256 KB，解析后完整指令不超过 60,000 字符。YAML 使用 safe loader；自定义 tag 一律拒绝。

同一次扫描中出现重复规范化 name 时，所有冲突条目都标为 invalid，Registry 不任选一个胜出。

### 6.2 文件树规则

Registry 使用不跟随 symlink 的目录遍历，并为每个普通文件记录：

- NFC 规范化后的 POSIX 相对路径。
- category：`reference`、`asset`、`script` 或 `other`。
- 字节数、mtime、SHA-256 和检测到的 MIME。

任一 symlink、设备文件、socket、FIFO、目录逃逸、NUL、绝对路径、`.`/`..` segment、反斜杠歧义或 NFC/casefold 路径碰撞会使整个 Skill 无效。

类别访问规则：

| 类别 | UI manifest | Agent 读取 | REST 内容读取 |
|---|---:|---:|---:|
| `SKILL.md` | 是 | 仅 `load_skill` | 详情 API 返回解析结果 |
| `references/` | 是 | UTF-8 文本 | UTF-8 文本 |
| `assets/` | 是 | 仅元数据和安全 URL | 安全图片、PDF、纯文本 |
| `scripts/` | 是 | 否 | 否，固定 403 |
| 其他文件 | 元数据 | 否 | 否 |

单个 reference 原文件不超过 1 MB。`read_skill_resource` 单次最多返回 60,000 字符，超限时返回截断标记和原始字符数。手工放入的 asset 超过 20 MB 时仍可显示元数据，但不能通过 REST 下载或预览。

安全 asset MIME 仅包括：

- `image/png`
- `image/jpeg`
- `image/webp`
- `image/gif`
- `application/pdf`
- `text/plain`
- `text/markdown`
- `application/json`

SVG、HTML、XML 和浏览器可执行类型不属于安全 asset。安全响应设置明确的 `Content-Disposition`、`X-Content-Type-Options: nosniff` 和限制性 CSP。

MIME 不能只信任扩展名或上传 Content-Type。图片和 PDF 校验固定文件签名，文本必须通过 UTF-8 解码，JSON 还必须完整解析；扩展名、签名和声明类型不一致时拒绝预览和下载。

文本、Markdown 和 JSON 在 UI 中只以转义后的纯文本/code view 展示，不执行 Markdown 内嵌 HTML。PDF/图片响应使用 `Content-Security-Policy: sandbox; default-src 'none'`；不允许 Skill asset 请求同源脚本、网络或表单能力。

资源 API 先从当前 `SkillRegistry` 解析 name，再从已验证 manifest 精确查找规范化相对路径。不得将 `{skill_name}` 或 `{relative_path}` 直接拼到文件系统。

### 6.3 Registry generation

每次完整扫描产生单调递增的进程内 generation 和稳定的 Skill content digest。digest 覆盖 frontmatter、`SKILL.md` 指令和受控 manifest，不包含真实绝对路径。

无效 Skill 仍返回：

- 目录显示名。
- 可解析的 name/description（若存在）。
- `valid=false`。
- 稳定错误码和受限错误说明。

错误说明不得包含用户目录绝对路径；UI 需要定位时只显示 Skill 相对目录名。

## 7. Skill 导入与恢复

### 7.1 zip 限制

导入接受 multipart `UploadFile`，以固定大小 chunk 写入 Skill 根目录内的 `.skill-upload-<uuid>.tmp` 文件。读取超过 20 MB 后立即终止并删除 upload 文件。通过 zip 中央目录初检后，再逐 entry 解压到独立的 `.skill-import-<uuid>.tmp` stage 目录；上传文件和解压目录不能共用路径。

zip 必须满足：

- 最多 500 个 entry。
- 压缩包最多 20 MB。
- 声明和实际解压总量都不超过 50 MB。
- 只含普通文件和目录。
- 拒绝 encrypted entry、symlink 和特殊 Unix file mode。
- 所有路径通过与手工扫描相同的 NFC/casefold 和逃逸检查。
- 只接受根目录直接含 `SKILL.md`，或唯一顶层目录内含 `SKILL.md`。

解压不使用不受控的 `extractall`。Importer 逐 entry 打开、限量复制，并从规范化目标映射写入。

### 7.2 新导入与覆盖

新导入流程：

1. 将已校验的 zip 逐 entry 解压到 `.skill-import-<uuid>.tmp`。
2. 按两种合法布局确定 `payload_root`：stage 自身，或唯一顶层目录。
3. 完整校验 `payload_root` 中的 Skill。
4. 将目录 `fsync` 后，以 `os.replace(payload_root, <skills>/<registry-name>)` 落位。
5. 若存在空的 stage 外壳则删除，并 `fsync` Skill 根目录。
6. 删除对应 `.skill-upload-<uuid>.tmp`。
7. 刷新 Registry generation。

目标已存在时默认返回 `SKILL_CONFLICT`。显式覆盖必须提交：

```json
{
  "overwrite": true,
  "expected_digest": "用户确认时看到的现有 Skill digest"
}
```

覆盖在 Registry 写锁内执行：旧目录先改名为 `.skill-backup-<uuid>.tmp`，新目录再落位，根目录 `fsync` 后删除 backup。任何一步失败都保留可恢复状态，不用空目录替换旧 Skill。

启动恢复只处理上述固定名称：

- target 和 backup 都存在：target 已提交，删除 backup。
- target 不存在且只有合法 backup：恢复 backup。
- 未进入覆盖的孤立 upload/stage：删除。
- 形状不完整或归属不明确：保留并产生 recovery warning，不猜测删除。

REST 删除同样要求当前 digest。被活跃 `CapabilityLease` 引用的 Skill 返回 `SKILL_IN_USE`。若用户在文件系统手工删除已选 Skill，thread JSON 不被跨文件改写；UI 显示 missing，下一次 run 在写 user message 前返回 `SKILL_UNAVAILABLE`。

## 8. Skill 渐进加载

### 8.1 系统上下文

run snapshot 在内存中保存所选 Skill 的：

- name 和 description。
- 完整 `SKILL.md` 指令文本。
- Skill digest。
- 受控 resource manifest。

初始系统上下文只追加 name/description 目录，并明确它们是用户选择的外部能力。完整指令不直接放入 system prompt。

`chat.SYSTEM_PROMPT` 原始客观中立规则必须完整保留在 capability catalog 之前。Skill 内容不能作为新的 system message，也不能修改基础 prompt。

### 8.2 内置 Skill 工具

所有 run 只注册两个 Skill 工具：

`load_skill(name)`：

- 只接受当前 snapshot 中的 name。
- 返回 snapshot 已缓存的完整指令和资源索引。
- 不重新读取磁盘，不返回 script 内容。

`read_skill_resource(name, relative_path)`：

- 只接受当前 snapshot 中 manifest 的 `references/` 文本项。
- 读取当前文件后重新计算 SHA-256。
- digest 不匹配时返回结构化 `SKILL_CHANGED`，不返回新内容。
- UTF-8 解码失败时返回结构化错误。
- 最多返回 60,000 字符并标注截断。

Skill 工具是本地只读工具，自动执行，不进入 MCP 审批。工具结果继续计入现有 run tool summaries；1D 再统一纳入预算治理。

## 9. MCP 配置模型

### 9.1 文档结构

`<VR_DATA_DIR>/agent/mcp.json` 是单一配置文档：

```json
{
  "schema_version": 1,
  "revision": 0,
  "servers": []
}
```

每个 server 保存：

- `id`：稳定机器 ID。
- `display_name`：UI 显示名。
- `enabled`。
- `transport`：`stdio` 或 `streamable_http`。
- transport 配置。
- `trust_fingerprint` 和 `trusted_at`（仅 stdio）。
- 已发现工具目录及逐工具 enabled 状态。
- 脱敏 health 摘要。

`server_id` 长度 1-32，只匹配 `[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?`，因此不能包含 `__`。`display_name` 长度 1-80。

所有 POST、PATCH、DELETE、trust、test 和 refresh 都携带当前 MCP document revision。成功修改恰好递增一次 revision。revision 不匹配返回结构化 409，客户端重载，不自动重放。

### 9.2 stdio transport

stdio 配置保存：

```json
{
  "transport": "stdio",
  "executable": "npx",
  "args": ["-y", "@acme/finance-mcp"],
  "env": {
    "FINANCE_TOKEN": {"from_env": "VR_FINANCE_MCP_TOKEN"}
  }
}
```

不支持 shell command 字符串、raw env value、用户指定 cwd 或 shell 语法。后端固定使用：

- `shell=False`。
- 参数数组原样传递，不展开 glob、管道、重定向或变量。
- 工作目录 `<VR_DATA_DIR>/agent/mcp-work/<server-id>/`。
- MCP SDK `get_default_environment()` 返回的最小平台环境，加配置中显式解析成功的 env 引用。

首次 test、refresh、启用或 run 使用前，后端用 `shutil.which` 或绝对路径解析实际 executable。信任 fingerprint 是规范 JSON `{resolved_executable, args}` 的 SHA-256。

未确认时返回 `STDIO_TRUST_REQUIRED`，响应只包含：

- 配置中的 executable。
- 解析后的绝对 executable。
- 完整 args 数组。
- 当前 fingerprint。

新增配置只写 JSON，不启动进程。`POST /trust` 必须提交当前 revision 和页面显示的 fingerprint；后端重新解析并精确匹配后记录。executable 或 args 修改时指纹立即清空。PATH 变化导致 resolved executable 变化时也必须重新确认。

stdio MCP 是用户明确选择的受信任本地程序，不是沙箱。UI 必须显示这个事实，但 Skill `scripts/` 仍绝不执行；两种能力不能混淆。

删除 server 时关闭进程并删除空的 `mcp-work/<server-id>`。外部程序写入的非空工作目录作为用户数据保留，并在响应中返回 recovery warning，不递归删除。

### 9.3 Streamable HTTP transport

HTTP 配置保存：

```json
{
  "transport": "streamable_http",
  "url": "https://mcp.example.com/mcp",
  "headers": {
    "Authorization": {"from_env": "VR_FINANCE_MCP_AUTH"}
  }
}
```

URL 不允许 username/password、fragment 或 query。密钥必须通过 header 环境变量引用提供。禁止用户覆盖 `Host`、`Content-Length`、`Transfer-Encoding` 和 `Connection`。

连接前复用模型 Base URL 的地址校验规则：

- cloud metadata 与 link-local 永远拒绝。
- public mode 拒绝 loopback、私网和解析到这些地址的域名，并只允许 HTTPS。
- local mode 允许 loopback/private HTTP，方便本机 MCP，但仍拒绝 metadata/link-local。
- 自定义 `httpx.AsyncClient` 设置 `follow_redirects=False`；1C 不跟随 MCP HTTP redirect。

SSRF 公共逻辑从 `chat.py` 提取成无副作用共享 helper，模型 Base URL 的现有行为和错误语义必须保持回归测试。

### 9.4 环境变量和密钥

`env` 与 `headers` 的值只接受 `{ "from_env": "NAME" }`。变量名使用平台可移植格式 `[A-Za-z_][A-Za-z0-9_]*`。

解析发生在连接前。缺失或空变量返回 `MCP_SECRET_MISSING`，不启动进程、不发送网络请求。REST response、health 和日志只显示环境变量名，不显示解析值。

连接使用的 secret set 由 `McpRegistry` 持有，用于：

- MCP Registry/binding 调用边界对 adapter Tool 的结果和异常脱敏；adapter `tool_interceptors` 即使使用也只承担请求侧处理，不作为响应脱敏边界。
- health 错误脱敏。
- tool catalog 描述脱敏。
- 关闭连接时清除内存引用。

`CapabilityLease`、LangChain Tool metadata、Graph、MemorySaver 和 RunDocument 不保存 secret set。

## 10. MCP 连接和工具目录

### 10.1 生命周期

MCP session 按 server 和单调递增的 session generation 缓存。Registry 使用 `AsyncExitStack` 管理官方 adapter 的 session context：

1. 解析并验证无密钥配置。
2. 解析 env/header 引用。
3. 建立 stdio 或 Streamable HTTP session。
4. initialize。
5. 通过 `load_mcp_tools(session=...)` 发现并转换工具。
6. 用 Registry-owned 调用包装器应用响应/异常脱敏、server 串行锁和 alias。
7. 缓存 session、tools、health 和 config revision。

`test` 完成 initialize 和基础能力检查。`refresh` 还会更新工具目录。连接失败的 health message 必须先按当前 secret set 完整脱敏，再截断到最多 500 字符；成功写入 checked time 和 tool count。health 更新属于配置文档修改，会递增 revision。

`CapabilityLease` 中的稳定 binding 只捕获 server/tool identity、配置/catalog generation，以及准入时从官方 adapter Tool 冻结的模型可见 name、description 和 args schema；这些字段不从持久化 catalog JSON 重新生成。binding 不闭包持有 `ClientSession` 或 resolved secret。因此只旋转 transport session、但配置和 catalog 未变时，不构成 capability snapshot 漂移。

每个 session generation 有 `accepting`、`stale/draining`、`closed` 三种状态。connection-affecting 配置改变、transport error、已经进入 `session.call_tool` 的调用被取消或后端 shutdown 时，当前 generation 进入 `stale/draining`，立即拒绝新的调用引用；同一配置/catalog generation 可按需建立 successor session。旧 generation 只在 in-flight 引用归零后物理关闭，关闭由 Registry 自有的受保护清理任务完成，不能依赖已取消请求的作用域。

每次 binding 调用先等待 server 串行锁，取得锁后才在同一个 Registry 状态锁内原子检查匹配 generation 仍为 `accepting` 并增加 in-flight 引用；stale 转换、引用获取、引用释放和引用归零触发关闭都由该状态锁串行化。已取得引用的调用属于旧 generation 的 drain 范围，可以继续进入 `session.call_tool`；等待 server 锁时取消或 `McpArgumentGuard` 失败都尚未取得引用，不得标记 session stale。引用一旦取得，就必须在正常完成、超时、transport error 和调用中取消的 `finally` 路径中恰好释放一次。

successor session 重新执行官方 adapter 转换，但必须包含 binding 冻结的 original tool，且 args schema 与冻结值一致；缺失或不兼容时返回有界、脱敏的错误 ToolMessage 并标记 unhealthy，不静默缩小或改写活跃 Graph 的工具集合。shutdown 停止创建 successor 并拒绝新的引用；此时新进入 binding 的调用收到 `MCP_UNAVAILABLE` 语义的有界、脱敏 ToolMessage，不抛出未捕获异常。

Capability lease 引用用于固定配置/catalog generation 和阻止配置热切换，不用于长期钉住具体 transport session。若有 active capability lease，相关配置修改、test 和 refresh 返回 `MCP_CONFIG_BUSY`，不热切换活跃 Graph 的工具集合；shutdown 则停止创建 successor，并等待所有 generation 的有界关闭任务完成。

运行中产生的 health-only 更新允许在 lease 存在时原子写入，因为它不改变 transport、secret reference、工具目录或 snapshot generation；它仍递增 MCP document revision，前端 mutation 如因此冲突就重载，不自动重放。

### 10.2 并发

同一个 MCP server 的所有 tool call 通过一个 `asyncio.Lock` 串行执行，防止 stdio session 交错和未知远端限流。不同 server 可以并行。

内置投研工具继续使用其现有 run 内串行锁，Eastmoney throttled 调用不得因 MCP 接入而并行化。

固定超时使用代码常量，不在 1C 增加策略 UI：

- connect/initialize：15 秒，从开始建立 transport 到 initialize 和首次官方 adapter 工具发现完成；超时后本次 session generation 不进入缓存。
- tool call：60 秒，从进入 binding 开始，覆盖等待 server 串行锁和 `session.call_tool`。等待锁时超时不取得 session 引用；取得锁后只使用剩余预算，保证单次 binding 调用端到端有界。
- HTTP SSE read：60 秒，按单次底层 read 计算，同时受外层 tool-call 剩余预算约束。

stdio 关闭必须使用锁定 `mcp==1.26.0` context manager 的有界进程树关闭合同：先关闭 stdin 并最多等待 2 秒，再 terminate 进程树并最多等待 2 秒，仍未退出则 kill。POSIX 和 Windows 都保留 SDK 的平台实现；不得以无界 `process.wait()` 或只 terminate 不 kill 的自定义路径替换。

超时和 transport error 转成有界、脱敏的错误 ToolMessage，使 Agent 可以继续；Registry 同时标记连接 unhealthy。`CallToolResult(isError=true)` 使用 adapter 的标准错误转换。

1C 只接受 MCP text content 和 JSON `structuredContent`。image、audio、embedded resource 或其他内容块返回 `MCP_CONTENT_UNSUPPORTED`；Artifact 支持留给 1D。完整结果先递归脱敏，再编码并截断到 6,000 字符，顺序不能颠倒，避免截断后的 secret 前缀逃过替换。

Stop 或断连取消 Graph 时，尚未完成的 MCP 结果不得进入 Graph、SSE 或 RunJournal。实际调用中的 server session generation 标记 stale；它停止接收新调用并异步 drain，匹配同一配置/catalog generation 的 successor 可以独立连接。

### 10.3 工具目录和 alias

工具 refresh 保存：

- 原 server/tool 名和显示描述。
- 输入 schema。
- 稳定 alias。
- enabled 状态。
- discovered time。

单个 server 最多接受 256 个工具；单个原工具名最多 256 字符、description 最多 8,000 字符、序列化 input schema 最多 64 KB，整个持久化 catalog 最多 2 MB。超过任一限制时 refresh 失败并保留旧 catalog，不部分提交。

首次发现的工具默认 disabled。再次 refresh 时，只有相同原名的工具继承 enabled 状态；消失的工具从当前目录移除。

模型可见名是：

```text
mcp__<server-id>__<safe-tool-alias>
```

最终名称必须满足 OpenAI-compatible 常见限制 `[A-Za-z0-9_-]+` 且最多 64 字符。

- 原工具名安全、不含 `__`、拼接后不超过 64 字符时直接使用。
- 否则将原名转为安全 slug，按剩余长度截断，并追加 `-` 加 SHA-256 前 10 位。
- refresh 对相同 `(server_id, original_tool_name)` 必须生成相同 alias。
- alias 冲突或无法生成时，该工具不可启用并显示校验错误。

UI 始终显示原 server/tool 名；alias 只用于模型、Graph、journal 和协议校验。

## 11. Capability 准入

### 11.1 两次校验

start、retry 和 steer-away 不能在检查 duplicate 前连接 MCP，也不能在 capability 解析期间允许配置漂移，因此采用两阶段准入。

第一阶段是 Coordinator 无副作用 preview：

- 按 1B 顺序先检查 protocol run ID 和 message ID duplicate/conflict。
- 检查 thread busy、revision、消息 head 和请求形状。
- retry 继续检查目标 run 是当前 thread 最新的可重试终态。
- steer-away 继续完整校验 pending cancelled entries 和新 user message。
- 返回 preview thread revision、`selected_skills` 和必要的历史边界。

第二阶段：

1. `CapabilityResolver` 以 preview 数据获取 Skill/MCP generation lease。
2. Coordinator 重新进入 thread lock。
3. 完整重复 duplicate、busy、revision、head 和形状检查。
4. 验证 preview 事实没有变化。
5. 才写 RunDocument、user message 和 thread revision，并构建 Graph。

第二次检查失败、MCP 连接失败或 Graph 构建失败时，lease 必须释放。duplicate 语义始终优先于 capability 错误。

1C 刻意采用 fail-closed 准入：全局 enabled server/tool 是用户声明的本次 run 能力集合。“相关 server”精确定义为全局 enabled 且持久化 catalog 中至少有一个 enabled tool 的 server；工具全部 disabled 的 server 不参与 run 准入。新产品 run 必须连接所有相关 server 并取得与持久化 catalog 一致的官方 adapter Tool；任一相关 server 不可用就返回 `MCP_UNAVAILABLE`/503，不静默删掉该 server 的工具后继续。用户需要修复连接、禁用 server 或禁用其全部 tools。1C 不采用首次 tool call 才连接的方案，因为 `load_mcp_tools(session=...)` 产生的 Tool 与具体 session 绑定，改用持久化 schema 自建占位 Tool 会越过“官方 adapter 转换”和不可变 snapshot 边界。纯 resume 仍复用已有 lease，不重新执行该准入。

thread PATCH 使用现有 revision CAS。若用户在 preview 和最终准入之间改变 `selected_skills`，最终准入返回 409，不使用旧 snapshot。

### 11.2 snapshot 内容

`CapabilityLease` 暴露给 AgentFactory 的内容只有：

- 内置 LangChain tools。
- `load_skill` 和 `read_skill_resource`。
- 已启用 MCP tools 的无密钥 binding。
- selected Skill catalog prompt fragment。
- 无密钥的 MCP HITL policy factory。
- generation/lease close callback。

纯 resume 复用 active handle 内的同一 lease、tools、policy 和 MemorySaver。`AgentFactory` 用本次请求重新提供的 `RunSecrets` 构造新的 request-scoped middleware 实例和同构 Graph；resume 不重新读取 `mcp.json` 或 Skill 文件。

retry 和 steer-away 创建新产品 run，因此重新 preview 并获取当前 capability generation；它们不恢复旧 run 的 Skill/MCP 配置。

## 12. MCP 审批与 allowance

### 12.1 Policy middleware

使用锁定版 `HumanInTheLoopMiddleware(interrupt_on=...)`。`interrupt_on` 的 key 集合必须与 lease snapshot 中全部已启用 MCP alias 的集合严格相等，不能遗漏或增加；锁定 middleware 对没有条目的 tool 默认自动批准，遗漏任一 alias 都会形成未经审批即可执行的 MCP 工具。每项只允许 `approve` 和 `reject`，不开放 edit/respond。

每个工具的 `when` predicate 捕获无密钥的 thread/server/original-tool identity，并同步查询 `AllowanceRegistry`：

- allowance 存在：本次不 interrupt。
- allowance 不存在：产生标准 HITL interrupt。

内置工具和 Skill 工具不在 `interrupt_on` 中，默认自动执行。

`when` 只承担 allowance 查询，不能承担参数大小校验；锁定版本的 predicate 只能返回 `bool`。运行面另注册 `McpArgumentGuard` 作为 pre-HITL `awrap_model_call` middleware：它在模型响应返回、但尚未写入 Graph state 和进入 `HumanInTheLoopMiddleware.after_model` 前，检查所有 MCP tool call。guard 对参数副本递归脱敏，再以确定性的紧凑 UTF-8 JSON 编码计算字节数；`<= 65,536` 字节继续，`> 65,536` 字节抛出无敏感详情的 `MCP_ARGUMENTS_TOO_LARGE`，由现有 run error/终态路径处理。超限调用不得产生 interrupt、进入 tools node 或访问 MCP server，guard 也不得修改尺寸合格调用用于实际执行的原始参数。

`McpArgumentGuard` 是 Graph/request-scoped 实例：它只在当前 Graph 生命周期内使用本次请求的 model key，并通过 Registry 的同步脱敏入口使用当前 server secret set。它不能保存在 `CapabilityLease` 或跨 resume 复用；Graph 在 interrupt 或终态后沿用 1B 路径释放，resume 以新请求密钥重建 guard。secret-free HITL policy factory 和 allowance identity 才属于 lease snapshot。

### 12.2 interrupt metadata

`AgentProtocolBridge` 继续将 legacy `CUSTOM/on_interrupt` 转为标准 `RUN_FINISHED.outcome.type="interrupt"`。每个标准 interrupt 附加已脱敏 metadata：

```json
{
  "source": "mcp",
  "serverId": "finance",
  "serverName": "财报服务",
  "toolName": "get_cashflow",
  "toolAlias": "mcp__finance__get_cashflow",
  "arguments": {"symbol": "600519"}
}
```

metadata 与现有 bridge ID、tool call ID 一并持久化到 pending assistant message，页面刷新后可恢复。超出有界序列化限制的 arguments 在产生 interrupt 前以 `MCP_ARGUMENTS_TOO_LARGE` 失败，不能只显示部分参数让用户在信息不完整时审批。

arguments 使用 UTF-8 JSON 计算，单个 tool call 上限为 64 KB。参数先用 model key 和当前 MCP secret set 脱敏，再进入 metadata、SSE 和 thread JSON。

上述 interrupt metadata 是项目自定义的恢复协议字段，随 AG-UI interrupt 载荷持久化，不属于 AG-UI 或 assistant-ui 的上游标准字段。字段名固定使用示例中的 camelCase；即使经 REST thread history 持久化和返回，也不转换成 snake_case。

`responseSchema` 使用 `oneOf` 和 `additionalProperties=false`，只接受：

```text
approve + once
approve + thread_session
reject  + once
```

### 12.3 resume

`ApprovalBridge` 必须为所有 pending interrupt 各提交恰好一个标准 `ResumeEntry`。允许多项使用不同决定。

纯 resume 继续保留 1B 密钥和模型配置合同：请求必须重新通过 `X-VR-Agent-Model-Key` 提供模型密钥，密钥只进入 request-scoped `RunSecrets`；`ModelRef` 必须与 active handle 完全一致，否则返回 `RUN_CONFIG_MISMATCH`/409。模型配置比较先于消费密钥构建模型，且这些校验和 Graph 重建都必须在任何 allowance 写入之前成功。

Coordinator 在 thread lock 内依次完成：

1. 校验 bridge ID 集合完整、无重复。
2. 校验 tool call ID、alias 和原 server/tool 映射。
3. 校验 payload 是三个合法组合之一。
4. 校验 `ModelRef`，再用本次请求的 `RunSecrets` 构建模型。
5. 生成按原始顺序排列的 LangChain decisions。
6. 使用现有 snapshot 重建 request-scoped Graph。
7. 将同一产品 run 持久化回 `running` 并追加 protocol run ID。
8. 为 `approve + thread_session` 写入内存 allowance。
9. 清空 pending 并恢复流。

Graph 重建或持久化失败时不能新增 allowance，旧 handle 保持 `awaiting_approval`。allowance 登记是无失败的内存操作，发生在持久化成功后、恢复流之前。

`reject` 由 HITL middleware 生成结构化错误 ToolMessage，pending MCP 工具不执行，同一产品 run 继续让模型回答。

### 12.4 steer-away

待审批时普通 assistant-ui Composer 不直接 append。`SteerAwayComposer` 调用 `useAgUiSteerAway`，提交全部 cancelled entries 和恰好一条新 user message。

后端沿用 1B 的原子顺序：

- 校验全部 pending 都被 cancelled。
- 对新消息做无副作用 preview 和 capability lease。
- 在 thread lock 内再次校验。
- 先把旧产品 run 持久化为 cancelled 并释放旧 lease。
- 不执行任何 pending MCP 工具。
- 再以新 lease 准入新产品 run。

新 run 写入失败时旧 run 保持 cancelled；返回持久化错误并要求前端权威重载。

### 12.5 allowance 生命周期

allowance 不写入 thread、run 或 MCP JSON。以下动作删除它：

- `DELETE /threads/{thread_id}/allowances`。
- 删除 thread。
- 修改、禁用或删除对应 MCP server/tool。
- 后端 shutdown/restart。

清除 allowance 不修改 thread revision，响应返回清除数量。后端重启后，旧 `awaiting_approval` run 已由 1B reconciliation 变为 `interrupted`，不会恢复旧 allowance。

## 13. 刷新、恢复与取消

`GET /threads/{thread_id}` 在持久化 ThreadDocument 之外增加非持久化的 `resume_available` 布尔值，由 Coordinator 根据当前 active handle 计算。该字段不写入 thread JSON，也不递增 revision。

页面从 REST history 恢复 pending message 时，只有 thread `last_run.status == "awaiting_approval"` 且 `resume_available=true`，才把 `metadata.custom["agui"].interrupts` 注入 assistant-ui。

- handle 和 MemorySaver 仍在：审批按钮可用，resume 走原产品 run。
- 后端已经重启：reconciliation 将 run 改为 `interrupted`；pending turn 只作为历史显示，不注入可操作 interrupt，页面提供 Retry。

前端提交审批、steer-away 或 Stop 后进入现有 converging 状态，禁用输入、线程切换和 capability 变更，直到权威 thread reload 完成。

取消期间同步/远端工具可能继续到自身超时，但迟到结果必须由现有 run generation/closed journal 边界丢弃。MCP 调用中的取消额外使连接 stale。

## 14. 前端设计

### 14.1 1C 临时布局

1C 不实现最终三栏。现有 Agent 页面新增：

- `CapabilityBar`。
- `CapabilityManagerDialog`。
- `ApprovalPanel`。
- `SteerAwayComposer`。

现有模型配置区、thread list 和 assistant-ui 对话区保持原位。Dialog 打开时不切路由、不销毁 runtime。

桌面端 `CapabilityBar` 位于 thread selector 与对话之间；移动端自动换行。Dialog 在桌面使用受限宽度 modal，在移动端使用全屏 sheet。

### 14.2 CapabilityBar

Bar 显示：

- 当前 thread 已选有效 Skill 数量。
- enabled/healthy MCP server 摘要。
- 使用 Settings 图标的“管理能力”按钮。
- “清除临时授权”命令。

运行或收敛期间，影响 capability snapshot 的管理入口禁用。Bar 不承担最终 Inspector 的 run、artifact 或 budget 信息。

### 14.3 Skills tab

Skills tab 左侧是可扫描列表，右侧是详情：

- valid/invalid/missing 状态。
- name、description、digest 和校验错误。
- references/assets/scripts 文件清单。
- 安全 reference/asset 预览。

每个 valid Skill 使用 checkbox 表示当前 thread 选择。选择先保存在 dialog 草稿；“应用到本会话”一次提交 `selected_skills + thread revision`。409 后丢弃草稿并重载权威 thread，不自动重放。

Import 和 Refresh 使用 Lucide 图标按钮及 tooltip。覆盖同名 Skill 前显示现有 digest 和导入包 digest，并由用户显式确认。

### 14.4 MCP tab

MCP tab 提供：

- server 列表与 enabled/health/tool count。
- 添加、编辑、删除和启停。
- stdio/Streamable HTTP segmented transport control。
- executable、args、env/header reference 表单。
- test、refresh tools 和逐工具 enabled checkbox。

stdio trust dialog 显示配置 executable、解析后的绝对路径和完整 args。确认按钮调用独立 trust API；关闭 dialog 不产生信任。

任何 secret 输入框都不出现，因为配置只引用后端环境变量。UI 只显示变量名和 missing 状态。

### 14.5 ApprovalPanel

待审批时，每个 interrupt 显示一行：

- 原 server 和 tool 显示名。
- 完整、已脱敏的参数。
- 允许一次、本会话允许、拒绝三个互斥选项。

所有行有明确选择后，“提交全部决定”才启用。提交过程中禁用决定、thread 切换和 steer-away，防止两个 resume 请求并发。

普通 Composer 在 pending interrupt 时替换为 `SteerAwayComposer`。它只发送一条新 user message，并由 hook 自动为全部 open interrupt 生成 cancelled entries。

## 15. REST API

### 15.1 Skill

```text
GET    /api/agent/skills
GET    /api/agent/skills/{skill_name}
POST   /api/agent/skills/import
POST   /api/agent/skills/refresh
DELETE /api/agent/skills/{skill_name}
GET    /api/agent/skills/{skill_name}/files/{relative_path:path}
```

现有 thread PATCH 增加 `selected_skills`，仍要求当前 thread revision。

现有 thread detail GET 增加计算字段 `resume_available`；thread list 不需要该字段。

### 15.2 MCP

```text
GET    /api/agent/mcp
POST   /api/agent/mcp
PATCH  /api/agent/mcp/{server_id}
DELETE /api/agent/mcp/{server_id}
POST   /api/agent/mcp/{server_id}/trust
POST   /api/agent/mcp/{server_id}/test
POST   /api/agent/mcp/{server_id}/refresh
DELETE /api/agent/threads/{thread_id}/allowances
```

`trust` 是对上位设计 API 的必要补充，用于把“显示命令”和“实际启动进程”之间的用户确认变成可测试的服务端合同。

所有 API 继续受现有 `/api/*` 可选 `VR_API_KEY` middleware 和 CORS 策略保护。普通 REST response 使用 snake_case；AG-UI 标准字段以及嵌在 REST history 中的自定义 interrupt metadata 继续按锁定 runtime 的 camelCase 格式。

## 16. 错误处理

### 16.1 HTTP 状态

| 状态 | 范围 |
|---|---|
| 400 | Skill/archive/path、MCP schema、SSRF、缺失环境变量、非法审批 payload |
| 403 | scripts、不安全 asset、manifest 外资源、目录逃逸 |
| 404 | 经 Registry 解析后不存在的 Skill/server/resource |
| 409 | revision/digest、stdio trust、active lease、thread busy、duplicate/run conflict |
| 503 | run 准入前启用的 MCP server 无法连接 |

所有错误使用 `{code, detail}`；409 继续使用 1B 的结构化 conflict 处理和权威 reload。

关键稳定错误码：

```text
SKILL_INVALID
SKILL_CONFLICT
SKILL_IN_USE
SKILL_CHANGED
SKILL_UNAVAILABLE
SKILL_ARCHIVE_REJECTED
SKILL_RESOURCE_FORBIDDEN

MCP_CONFIG_CORRUPT
MCP_REVISION_CONFLICT
MCP_CONFIG_BUSY
MCP_SERVER_NOT_FOUND
STDIO_TRUST_REQUIRED
STDIO_FINGERPRINT_MISMATCH
MCP_SECRET_MISSING
MCP_SSRF_BLOCKED
MCP_UNAVAILABLE
MCP_ARGUMENTS_TOO_LARGE
MCP_CONTENT_UNSUPPORTED
```

### 16.2 失败边界

- Skill/MCP capability 错误在最终 run 持久化前返回；不追加 user message，不创建 failed run。
- `MCP_UNAVAILABLE`/503 detail 只包含脱敏且有界的 server 显示名和错误摘要，不包含 command、args、header、env、resolved secret 或 URL query。前端保留尚未准入的 user message，显示不自动重试的连接错误和“管理 MCP”入口；它不是 revision conflict，不执行 409 权威 reload。
- MCP 工具已开始后的远端业务错误进入 ToolMessage，让 Agent 可以继续。
- transport error 也进入有界 ToolMessage，并使 Registry health unhealthy；错误字符串先脱敏。
- `mcp.json` 损坏时保留文件、拒绝 mutation/run 使用并显示 recovery warning。
- 单个 Skill 无效只隔离该 Skill，不阻塞其他有效 Skill 或 MCP。
- 所有 Skill 文件扫描、zip 解压和 JSON 原子写通过 `asyncio.to_thread`；MCP I/O 和进程生命周期使用 async API。

## 17. 安全不变量

1. Skill 名称和资源路径只能经当前 Registry/manifest 解析，不能直接拼路径。
2. Skill `scripts/` 的文本永不进入 REST、Agent Tool、Graph、SSE 或日志，也不传给任何 executable。
3. 添加 stdio 配置本身不启动进程；有效 fingerprint 信任是启动前置条件。
4. stdio 使用 `shell=False` 和参数数组；1C 不提供 shell command 模式。
5. Streamable HTTP 不跟随 redirect，并复用 public/local mode SSRF 规则。
6. `mcp.json` 只存环境变量引用；任何 resolved secret 不落盘。
7. MCP tool 输出、catalog、health 和异常在离开 Registry 前脱敏。
8. API Key 和 MCP secret 不得出现在 SSE、Graph state、MemorySaver、thread/run JSON、mcp JSON 或捕获日志。
9. active capability snapshot 不因 Skill/MCP 配置变化而静默漂移。
10. 外部 Skill/MCP 内容不能替换 Vibe-Research 客观中立系统规则。
11. 所有测试使用临时 Agent 数据目录，不读取或修改真实 `~/.vibe-research/agent`。
12. 1C 继续只支持单 FastAPI worker。

## 18. 测试策略

### 18.1 依赖兼容

- 在全新临时 venv 中从 `backend/` 执行 `pip install -r requirements.txt`。
- 导入锁定 MCP adapter、MCP SDK、httpx 和本地 mootdx distribution。
- 校验 vendored mootdx Python 文件与提交的上游摘要 manifest 一致。
- 用离线 fake `TdxHq_API` 覆盖 `Quotes.factory`、`bars`、`finance`，并验证上游 `Quotes.F10C/F10` API surface 保持可用；不虚构当前仓库不存在的 F10 HTTP route。
- 新增并保留真实 `/api/kline`、`/api/finance`，以及直接 `Quotes.F10` smoke 为串行执行的 `live` 发布前检查；不新增 F10 HTTP route。

### 18.2 Skill

- valid/invalid frontmatter、UTF-8、大小边界和 YAML safe loader。
- duplicate name、NFC/casefold path/name collision。
- 手工目录、symlink、设备文件和逃逸拒绝。
- zip-slip、绝对路径、反斜杠、encrypted/special entry、条目数和压缩/解压体积。
- 新导入、digest 覆盖冲突和每个 crash recovery 状态。
- reference 截断/digest 变化、asset MIME/响应头、scripts 固定 403。
- thread selected Skill revision CAS、missing/invalid run preflight。
- 初始 prompt 只含 catalog，完整文本只由 `load_skill` 返回。
- active lease 阻止覆盖/删除。

### 18.3 MCP

测试目录提供 deterministic fake stdio MCP 和 Streamable HTTP MCP：

- 添加配置不启动 stdio。
- 首次 test/refresh/enable 要求 trust。
- executable/args/PATH resolution 改变使 fingerprint 失效。
- shell 字符按普通参数传递。
- env/header secret 引用解析但不落盘。
- local/public mode SSRF、metadata/link-local、HTTPS 和 redirect。
- 工具发现、默认 disabled、refresh enabled 继承、奇异名称 alias 和 64 字符上限。
- 工具数量/catalog/schema 上限，以及非文本 MCP content 的失败关闭。
- 切片 2 完成态的 run 只向模型暴露内置工具和 Skill 工具，Graph、journal 和 interrupt 中都不存在 MCP alias。
- 同 server 串行，不同 server 可并行。
- session cache、successor generation、stale eviction、取消和 lifespan shutdown；跨 thread fixture 断言一个等待锁的调用取消不会获取 session 引用或关闭另一个正在调用的 session，实际调用取消后的旧 generation 只在 in-flight 归零后关闭，其他 lease 可转到同配置/catalog 的 successor。
- stale 转换与引用获取的竞态、所有取得引用路径的 exactly-once 释放，以及 shutdown 拒绝新引用时的有界脱敏 ToolMessage。
- successor 缺失 pinned original tool 或 args schema 漂移时标记 unhealthy 并返回错误 ToolMessage，不静默修改活跃工具集合。
- connect、排队加远端调用、HTTP read 三种超时边界；排队超时断言没有取得 session 引用。
- 顽固 stdio 子进程覆盖 stdin graceful close、terminate 和 kill 升级，测试有总时限且断言没有遗留进程。
- `isError`、transport error 和 timeout 的 ToolMessage/health 语义。
- fake server 故意回显 secret，断言 ToolMessage、SSE、checkpoint、thread/run/mcp JSON 和日志都只有 `[redacted]`。
- lease 和 binding 的对象图不暴露 `ClientSession`、resolved secret 或可序列化连接配置属性；模型可见 Tool metadata 来自准入时的官方 adapter Tool。
- 一个相关 server（全局 enabled 且 catalog 至少有一个 enabled tool）离线时，新产品 run 在写 user message 前以 `MCP_UNAVAILABLE`/503 失败；禁用该 server 或禁用其全部 tools 后其他能力可正常准入，工具全部 disabled 的离线 server 不阻塞准入。

### 18.4 审批合同

- approve once。
- approve thread session，以及同 server/tool 后续调用不再 interrupt。
- reject 后工具不执行且 Agent 继续。
- 多 interrupt 混合决定。
- fake model 依次调用 lease snapshot 中每个已启用 MCP alias，断言每个都产生 interrupt，且 `interrupt_on` key 集合与启用 alias 集合严格相等；内置和 Skill 工具不产生 interrupt。
- 缺失、重复、未知 ID 和非法 scope 全部失败关闭。
- MCP arguments 脱敏后的紧凑 UTF-8 JSON 恰好 65,536 字节时产生审批，65,537 字节时返回 `MCP_ARGUMENTS_TOO_LARGE`；两条路径都断言 metadata 脱敏，超限路径断言零次 server call 和零 interrupt。
- resume 缺少模型 key 或 `ModelRef` 不一致时不写 allowance、不清 pending，并分别返回 400/`RUN_CONFIG_MISMATCH` 409。
- resume 使用本次请求密钥构造新的 `McpArgumentGuard` 和 request-scoped middleware 实例，不跨 resume 复用旧实例，也不把实例写入 lease。
- reload 后 handle 存在时可 resume。
- backend restart 后只显示 interrupted，不可 resume 旧审批。
- clear allowance、thread delete、server config change 和 shutdown 清理。
- steer-away 取消全部 pending、执行零个旧 MCP 工具并准入一个新 run。
- preview 与最终准入之间的 thread/MCP/Skill 竞态。
- 所有 lease 在每个终态和异常路径恰好释放一次。

### 18.5 前端

- REST type 与 snake_case 线格式；嵌套在 thread history 中的项目自定义 interrupt metadata 保持 camelCase 原样往返。
- Capability Dialog draft 只产生一次 thread PATCH。
- invalid/missing Skill 展示和禁用。
- stdio trust dialog 显示完整命令、resolved executable、args 和 fingerprint。
- MCP test/refresh/enable 的 revision conflict reload。
- `MCP_UNAVAILABLE`/503 显示脱敏、有界且不自动重试的错误，保留未准入消息并提供“管理 MCP”入口，不触发 409 reload。
- 多 interrupt 全部选择后才能提交。
- `approve+once`、`approve+thread_session`、`reject+once` payload。
- pending 时普通 Composer 不可发送，SteerAwayComposer 使用 `useAgUiSteerAway`。
- `awaiting_approval` history 恢复 metadata；`interrupted` 不恢复可操作 interrupt。
- `resume_available=false` 时不注入可操作 interrupt，提交竞态仍由后端拒绝并重载。
- 运行/收敛期间管理和 thread 切换禁用。
- 桌面 modal、移动端 full-screen sheet 和文本不溢出。

### 18.6 全仓与浏览器验收

自动化门：

```bash
cd backend && .venv/bin/pytest -m "not live"
cd frontend && npm test
cd frontend && npx vitest run
cd frontend && npm run build
git diff --check
```

浏览器通过本机 `127.0.0.1:16002` CDP 驱动，覆盖：

1. 导入、选择、刷新和查看一个测试 Skill。
2. fake model 调用 `load_skill` 和 `read_skill_resource`。
3. 添加 fake stdio server，确认 trust 前没有子进程，trust 后 test/refresh 成功。
4. approve once、thread session、clear allowance、reject 和 steer-away。
5. fake Streamable HTTP MCP 的发现和调用。
6. 页面刷新后审批恢复，以及模拟重启后的 interrupted 展示。
7. 桌面和移动 viewport 无重叠、溢出或不可达控件。
8. 至少一家真实 OpenAI-compatible function-calling provider 完成一次 Skill/MCP 审批流。

所有浏览器和后端测试服务使用临时 `AgentServices` 与临时数据目录。不得扫描、连接或修改真实用户 MCP/Skill 配置。

## 19. 1C 退出条件

只有以下条件全部成立，1C 才可进入 1D：

- 三个内部切片均独立提交且全仓自动化绿色。
- 干净 venv 的单命令 requirements 安装成功。
- mootdx 离线合同通过，发布前 live smoke 已记录。
- fake stdio 和 Streamable HTTP MCP 均通过完整管理和工具调用流程。
- approve、reject、thread session、reload、resume、restart 和 steer-away 全部通过。
- traversal、symlink、zip-slip、script 读取/执行和 SSRF 测试全部失败关闭。
- stdio 未确认时没有进程启动。
- resolved secret 和真实用户数据没有出现在隔离目录之外。
- 前端 build、桌面/移动浏览器验收和至少一家真实 provider 验收通过。
- 1A、1B 的历史、retry、cancel、duplicate、revision 和密钥隔离回归没有退化。

若自动化全部通过但 mootdx live 数据源因外部网络不可用而未完成，验证文档必须标为 `PARTIAL`，不能宣称 1C 完整通过。

## 20. 向 1D 的演进边界

1D 可以把 CapabilityManagerDialog 的管理内容迁移到最终 Inspector，但必须保留：

- Skill/MCP REST schema。
- Registry name、digest 和 revision 合同。
- capability preview/lease 边界。
- MCP alias 和 allowance identity。
- AG-UI interrupt/resume payload。
- `AgentRuntimeHandle` 对 Graph 内部实现的隔离。

1D 增加 Artifact、预算、上下文和完整 policy UI 时，不得让这些功能直接访问 MCP session、Skill 真实路径或 LangGraph checkpoint 内部结构。
