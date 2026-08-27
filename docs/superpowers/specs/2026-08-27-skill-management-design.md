# 技能管理功能设计

**日期：** 2026-08-27

**状态：** 已完成第一轮对抗性评审修订，待用户复审

## 1. 背景

Vibe-Research 已经有两类 Agent Skills：

- 仓库内的 `backend/agent/builtin_skills/`，通过 `/builtin/` 只读命名空间提供；
- 本地 Agent 设置中 `skills.path` 指向的用户目录，通过 `/user/` 只读命名空间提供。

运行时边界已经存在，但前端只在设置页显示内置技能数量。用户无法查看技能内容，也无法从
浏览器导入、启停或删除用户技能。本功能补齐管理闭环，同时保留 FastAPI 与 LangGraph Server
的职责分离。

本设计遵守 `VISION.md`：技能管理只管理本地分析框架和工具说明，不增加选股、评分、目标价、
买卖信号或交易能力。用户技能不能覆盖固定的中立系统策略。

## 2. 目标

- 在侧栏 `α-mind` 下方增加一级入口“技能管理”。
- 在同一列表页明确分开用户技能和内置技能，用户技能在上。
- 内置技能始终启用且只读，可查看元数据与完整 `SKILL.md`。
- 用户技能可从本地文件夹或 ZIP 导入，可启用、停用、查看详情和删除。
- 新导入技能默认停用，用户审阅内容后再主动启用。
- 新 Agent 会话自动读取最新技能；已有会话通过显式 `/reload-skills` 命令刷新。
- 用户技能只作用于 `α-mind` Agent 工作台，不进入页面“问 AI”或固定工作流。
- 无论用户如何直接修改活动目录，内置技能始终优先，格式无效或同名冲突的用户目录不得进入
  Agent 提示或 `/user/` 只读文件视图。
- 导入文件只落在本地用户目录且不进入仓库；运行时沿用现有 Agent Skills 的读取、模型请求和
  本地 checkpoint 语义。

## 3. 明确不做

- 不提供网页内 `SKILL.md` 或附属文件编辑器。
- 不从 Git URL、技能市场或其他网络来源导入。
- 不提供插件或工具管理空标签。
- 不允许用户技能进入 `embedded_agent`、`debate`、`reflection`、`daily_review` 或
  `news_digest`。
- 不提供会话级技能勾选、技能评分、排名或推荐。
- 不在每次 Agent 运行时扫描目录、比较版本或后台轮询。
- 不从 FastAPI 主动改写 LangGraph 线程状态。
- 不热加载 Agent 设置中的 `skills.path`；修改静态设置后必须同时重启 FastAPI 与 LangGraph
  Server。
- 不自动执行导入包中的脚本。

## 4. 用户体验

### 4.1 导航与路由

- 侧栏在 `α-mind` 下方新增 `Sparkles` 图标的“技能管理”一级入口。
- 列表路由为 `/skills`。
- 内置详情路由为 `/skills/builtin/:name`。
- 用户详情路由为 `/skills/user/:name`。
- 详情页提供“返回技能列表”，浏览器前进、后退和刷新保持自然。

### 4.2 列表页

页面标题为“技能管理”，说明为“查看内置能力，管理你导入的技能。”右上角提供“导入技能”。

列表使用同页双分区：

1. 用户技能：显示总数、实际已加载数、名称、描述和开关。
2. 内置技能：显示总数、名称、描述和“内置”徽标，分区右侧标明“始终启用”。

卡片点击进入详情。用户技能开关是独立交互，点击开关不得同时触发路由跳转。技能按名称稳定
排序。移动端将两列卡片改为单列。

用户活动目录中名称满足 Agent Skills 规则、但内容无效或与内置技能冲突的目录仍显示在列表
中，并标记“已阻止”。它们虽然物理上位于活动根，却不会进入 Agent；界面提供“停用”和
“删除”，不显示已启用开关。停用根中的无效条目标记“无效技能”，可以删除但不能启用。
其他不符合技能目录命名规则的文件系统条目忽略；单个无效技能不能导致整个列表失败。

### 4.3 导入

“导入技能”打开模态框，使用分段控件选择“文件夹”或“ZIP 文件”。两种方式均保留
`references/`、`scripts/`、`assets/` 等附属文件。弹窗明确说明：导入本身只写本地；技能
启用后，其 metadata 会进入本地 checkpoint，Agent 读取的指令内容会发送给用户配置的模型。

导入成功后：

- 技能出现在用户技能分区；
- 状态为停用；
- 提示用户先打开详情审阅，再主动启用；
- 不自动切换当前 Agent 会话的技能缓存。

同名技能一律拒绝，不覆盖、不自动改名。同名范围包括内置技能、已启用用户技能和已停用
用户技能。

### 4.4 详情

详情页显示名称、描述、来源类型、启用状态、逻辑路径和完整 Markdown 指令。无效技能的
`instructions` 为 `null`，页面显示安全诊断而不尝试渲染正文。逻辑路径只使用
`/builtin/<name>/SKILL.md` 或 `/user/<name>/SKILL.md`，不返回主机绝对路径。

有效用户详情右上角提供启停开关和带工具提示的删除图标。无效/冲突详情不提供启用操作；若
它仍在活动根，只提供“停用”和删除。删除必须经过明确的二次确认，文案说明操作会永久删除
本地受管副本。内置详情不显示开关和删除操作。

`SKILL.md` 使用项目已有的 `ReactMarkdown + remark-gfm` 渲染，代码块沿用现有复制能力和
主题样式，不新增 Markdown 编辑器。

### 4.5 状态变化提示

导入、启用、停用或删除成功后显示提示：

> 技能状态已更新。新会话将自动使用最新配置；已有会话请执行 `/reload-skills`。

请求进行中禁用对应控件。失败时保留原界面状态并显示后端中文错误，不使用可能与磁盘状态
不一致的乐观更新。

当当前 `α-mind` 线程存在待审批 HITL interrupt 时，Composer 整体禁用，只允许审批面板用
`Command(resume=...)` 恢复。此时不能发送普通消息或 `/reload-skills`，提示用户先处理待审批
工具调用；刷新命令绝不能隐式批准、拒绝或覆盖 interrupt。

## 5. 存储模型

### 5.1 目录

- 内置根：`backend/agent/builtin_skills/`，只读。
- 用户活动根：Agent 设置中的 `skills.path`，存放用户请求启用的目录；是否实际加载还要经过
  严格 overlay 校验。
- 用户停用根：活动根的同级目录 `<skills.path.name>.disabled`。

例如活动根为 `~/.vibe-research/agent/skills` 时，停用根为
`~/.vibe-research/agent/skills.disabled`。两个目录位于同一文件系统，使启停可以使用
`os.replace` 原子移动。停用目录不挂载到 `/user/`，模型无法列出或读取其中内容。

用户直接放入活动根的合法技能视为已启用；直接放入停用根的合法技能视为已停用。管理 API
不维护第二份 JSON 索引，目录位置就是启停状态的唯一事实来源。

### 5.2 设置生命周期与首次启动

`skills.path` 是进程级静态配置。LangGraph 在 graph 构建时固化该路径；FastAPI 的技能管理器
也在进程内首次初始化时读取并固化同一份设置，后续请求不得热读出另一个根。修改
`settings.json` 后必须同时重启两个服务，设置页继续显示 `restart_required: true`。开发脚本
负责成对重启，文档不得暗示只重启一侧即可生效。

有效设置指向尚不存在的 `skills.path` 时，设置加载器以 `0700` 创建活动根，技能管理器同样
创建停用根。创建失败、路径不是目录或不可读写时，用户技能功能不可用。配置文件缺失或整体
无效时，`GET /api/skills` 和内置详情仍返回仓库内置技能，并在响应中标明用户技能不可用；
只有用户详情、导入、启停和删除返回 `503`，不能让首次配置问题拖垮内置列表。

### 5.3 并发与原子性

导入、启停和删除共享一个进程级锁，避免同名检查与移动之间的竞争。项目仍按单机、单
FastAPI 进程的自托管模型设计，不宣称支持多 worker 分布式锁。

导入先在停用根同级创建临时目录，完成解码、解压和全部校验后，再原子移动到最终停用目录。
任何校验失败都删除临时目录，不留下半套技能。启停请求若已处于目标状态则直接幂等成功；
需要移动时只有在目标不存在才执行 `os.replace`。活动根和停用根同时存在同名目录时返回
`409`，不猜测哪一份应被覆盖。

删除是永久删除受管目录。前端二次确认是必需门槛，不增加回收站或恢复功能。

## 6. 后端组件与 API

模块边界如下：

- `agent/skill_catalog.py`：项目自有的公开严格校验器与只读 overlay。它只使用公开依赖 API，
  不 import Deep Agents 的 `_parse_skill_metadata`、`_validate_skill_name` 或其他私有 helper。
- `skillmgr.py`：FastAPI 使用的磁盘管理层，负责导入、枚举、启停、删除、锁和原子操作。
- `agent/skill_reload.py`：`α-mind` 使用的可重载 Skills middleware 与命令文本提取。
- `agent/skill_backends.py`：把经过严格 overlay 的内置和用户 backend 分别挂到
  `/builtin/`、`/user/`；用户 overlay 额外剔除内置同名目录。

`skillmgr.py` 和 `agent/skill_reload.py` 都复用 `agent/skill_catalog.py` 的公开严格解析结果；
管理侧据此拒绝非法导入，运行时 overlay 据此隐藏直接丢入活动根的无效或冲突目录。共享的是
项目自有公开合同，不是 Deep Agents 的宽松 loader 或私有函数。`app.py` 只定义请求模型、
HTTP 路由及领域错误到状态码的映射。模块继续使用顶层兄弟导入，不引入 package-relative
import。现有 `agent/workflow_loader.py` 同步改用该公开严格解析器，移除它对 Deep Agents
`_parse_skill_metadata`、`_validate_skill_name` 的私有 import；固定工作流的严格启动校验语义
保持不变。

### 6.1 数据形状

列表响应顶层包含 `builtin`、`user`、`user_available` 和可选 `user_error`。技能摘要至少包含：

```text
name, description: string | null, source, enabled, valid, effective, error
```

详情在摘要基础上增加：

```text
path, instructions: string | null
```

`source` 只允许 `builtin` 或 `user`。`error` 只在无效技能时出现，内容为安全的中文诊断，
不得包含绝对路径、设置内容或密钥。`enabled` 表示用户目录物理位置，`effective` 表示该技能
实际进入 Agent；正常技能满足 `effective == enabled`，活动根中的无效或内置同名目录为
`enabled: true, effective: false`，在 UI 中显示“已阻止”。

### 6.2 路由

| 方法与路径 | 行为 |
|---|---|
| `GET /api/skills` | 返回 `builtin` 与 `user` 两组摘要，不在响应中返回完整正文 |
| `GET /api/skills/{source}/{name}` | 返回单个技能详情；`source` 仅允许 `builtin/user` |
| `POST /api/skills/import` | 导入文件夹文件列表或 ZIP base64；成功后默认停用 |
| `PATCH /api/skills/user/{name}` | 接收 `{ "enabled": boolean }` 并按需原子移动目录 |
| `DELETE /api/skills/user/{name}` | 永久删除用户技能 |

导入请求使用判别字段 `kind`：

- `kind: "folder"`：携带 `{ path, content_b64 }[]`；浏览器用相对路径保留目录结构。
- `kind: "zip"`：携带文件名和单个 `content_b64`。

沿用项目已有 base64 JSON 上传模式，不增加 `python-multipart` 依赖。所有路由自动受现有
`VR_API_KEY` 中间件保护。FastAPI 可以读取 Agent 设置以定位技能目录，但不得返回或记录
设置中的模型密钥、MCP header 或 env。

启停 PATCH 是幂等操作：目标状态已经满足时返回当前摘要和 `200`，不返回 `409`。只有活动根
和停用根同时存在同名目录、并发竞争导致目标出现或其他磁盘状态冲突时返回 `409`。

前端 `lib/api.ts::request()` 的 method 联合必须加入 `PATCH`，并在 `api` 对象中集中定义上述
技能调用；页面不得直接 `fetch`。

## 7. 严格运行时视图与覆盖顺序

Deep Agents 0.7.7 的默认 loader 有两项不能直接作为本项目安全边界的行为：sources 后出现的
技能覆盖前者，且 `name` 与目录名不一致时只 warning、仍返回 metadata。因此运行时不能把
原始用户 FilesystemBackend 直接交给 `SkillsMiddleware`。

`agent/skill_catalog.py` 提供项目自有的严格解析器和动态只读 overlay：

- 严格解析器按钉住的 Agent Skills 合同校验 frontmatter、名称与目录名，不合规则不产生可
  加载 metadata；它不调用 Deep Agents 私有 helper。
- 内置和用户 source 都经 overlay 在 `ls`、`read` 和 `download_files` 时生成严格目录视图，
  只暴露格式合法的技能子树；用户视图再排除与内置名称冲突的目录。被阻止目录不能从
  `/user/` 直接读取，格式损坏的内置目录也不能被宽松加载。
- 新会话初次加载和 `/reload-skills` 才会触发 overlay 枚举；已有会话的普通消息沿用缓存，
  不产生每轮扫描。
- `SkillsMiddleware.sources` 调整为 `["/user/", "/builtin/"]`，利用 later-wins 让内置技能
  具有最终优先级；严格 overlay 同时剔除同名用户目录，形成双重防线。
- 直接丢入活动根的同名或无效技能只出现在管理 API 的“已阻止”诊断中，不进入技能提示，
  也不能通过 `read_file` 读取。

`SkillsMiddleware` 必须显式传入项目自定义 system prompt，保留其要求的
`{skills_locations}`、`{skills_load_warnings}`、`{skills_list}` 插槽，只指导模型使用已注册的
`ls` 和 `read_file`。提示必须明确附属脚本仅是只读参考材料，不得声称可以执行；禁止使用
Deep Agents 默认 `SKILLS_SYSTEM_PROMPT` 中的 “Executing Skill Scripts” 文案，因为本图没有
`execute` 工具。

## 8. 导入与安全校验

导入包必须有且仅有一个技能根。允许 `SKILL.md` 位于上传根，或位于唯一的一层外包装目录；
存在多个候选技能根时拒绝导入。macOS 归档噪声 `__MACOSX/` 与 `.DS_Store` 不参与技能根
识别且不落盘，但仍计入请求的文件数量与解压字节上限，不能借此绕过配额。

`POST /api/skills/import` 不使用 FastAPI 的自动 JSON body 解析。路由先通过
`Request.stream()` 有界读取：有 `Content-Length` 且超过 36 MiB 时立即返回 `413`；缺少或
伪造该 header 时，累计读取超过 36 MiB 同样立即中止。只有通过编码体上限后才调用 Pydantic
`model_validate_json`。36 MiB 覆盖 25 MiB 原始内容的 base64 膨胀和最多 256 条路径开销。

校验规则：

- 使用 `agent/skill_catalog.py` 的公开严格解析器；管理 API 与运行时 overlay 共享该合同。
- `name`、`description` 必填，目录名必须与 `name` 一致。
- `name`、`description`、`compatibility` 和可选 metadata/allowed-tools 必须满足钉住的 Agent
  Skills 长度与类型合同；超限一律拒绝，不采用 Deep Agents 的截断或 warn-and-load 行为。
- 与任何内置、活动或停用技能同名时返回冲突。
- 路径先把 `\\` 规范为 `/`，再拒绝 NUL、空路径、绝对路径、`.`、`..`、重复归一化路径和
  目标目录逃逸；检查必须在创建任何目标文件之前完成。
- 拒绝 ZIP 中的符号链接。文件夹上传协议只接受普通文件的字节与相对路径，不接受链接类型。
- 最多 256 个文件，解码或解压后总大小最多 25 MiB。
- 单个 `SKILL.md` 最大 10 MiB，与 Deep Agents 的运行时上限一致。
- base64 字段接受纯 base64 或合法的 `data:<mime>;base64,` 前缀；空前缀、缺逗号、非 base64
  data URI 或解码失败返回 `400`。
- ZIP 禁止 `extractall`。逐 member 流式读取并按实际产出字节累计 25 MiB 上限，不信任
  `ZipInfo.file_size`；超过上限立即中止并清理临时目录，从而约束高压缩比 zip bomb。
- ZIP 加密条目、CRC/解压错误和不支持的压缩方法返回 `400`，不留下部分文件。
- 附属文件按原始字节保存，但导入流程不会执行其中脚本。

用户技能属于不受信任的提示内容。导入 API 不把文件发送到第三方，但技能启用后，Agent 会
按现有行为把名称、描述和逻辑路径保存在本地 checkpoint；当 Agent 读取完整技能时，内容会
进入对话状态并发送给用户配置的模型。界面必须在导入弹窗说明这一点。固定
`fixed_system_policy()`、只读 Skills 文件工具、现有工具权限和行为测试继续构成产品边界；
本设计不声称仅靠系统提示可以完全消除 prompt injection 风险。

## 9. `/reload-skills` 命令

当前 Deep Agents `SkillsMiddleware` 会把 `skills_metadata` 缓存在会话 checkpoint 中，后续
运行不再加载。为避免每轮扫描或版本比较，`agent/skill_reload.py` 提供
`ReloadableSkillsMiddleware`，复用公开 `SkillsMiddleware` 的正常加载入口和第 7 节严格
overlay，不复制或 import Deep Agents 的私有枚举函数：

- 新会话没有技能缓存时，按现有行为加载内置根与用户活动根。
- 已有会话继续使用缓存，不检查磁盘版本。
- 命令识别只查看最后一条 `HumanMessage`。字符串 content 直接取值；block content 仅在所有
  block 都是 `{ type: "text", text: string }` 时按顺序拼接。存在图片、文件或未知 block 时不
  识别为命令。提取文本去除首尾空白后必须精确等于 `/reload-skills`。
- 命中后删除传给正常 loader 的旧 `skills_metadata`/`skills_load_errors` 视图，强制重新枚举
  严格 `/user/` 与 `/builtin/`，再以新结果替换当前会话缓存。
- 命令追加确定性 AI 消息，例如“技能已重新加载：内置技能 5 个，已启用用户技能 2 个”，
  然后结束本次运行，不调用模型或工具。
- 非精确匹配文本按普通用户消息处理，不做隐式命令猜测。
- 重载只影响执行命令的当前会话，其他历史会话保持原缓存。

终止必须实现为 middleware 的 `before_agent` hook，并用
`@hook_config(can_jump_to=["end"])` 声明跳转；禁止在 `wrap_model_call` 中伪造 goto。初次加载
和显式重载复用同一个公开 loader 与严格 overlay。刷新 middleware 只加入 `agent` graph；
`embedded_agent` 和四个固定工作流不变。

HITL interrupt 的优先级高于刷新。当前依赖允许新普通输入旁路待审批 run，因此官方前端必须
在 `useLangChainInterrupts()` 非空时禁用 Workspace Composer，不能创建包含
`/reload-skills` 的新 run；用户只能先在 `ApprovalPanel` 批准或拒绝。刷新 middleware 不生成
`Command(resume=...)`，也不读取或修改审批决定。直接绕过前端调用原生 LangGraph API 不属于
本功能的安全承诺，继续遵循本机 loopback 的信任边界。

## 10. 错误处理

| 状态码 | 场景 |
|---|---|
| `400` | base64、ZIP、目录结构、路径或 `SKILL.md` 格式非法 |
| `404` | 技能不存在 |
| `409` | 与内置、活动或停用技能同名，或活动/停用根出现冲突状态 |
| `413` | 编码 JSON body、文件数量、单文件或解码/解压后总大小超限 |
| `503` | 用户技能接口所需的 Agent 设置缺失、无效或技能根不可用 |
| `500` | 未分类本地文件系统错误，响应不含绝对路径 |

列表页、详情页和导入弹窗都提供加载、空、成功和错误状态。`GET /api/skills` 在用户配置不可用
时仍以 `200` 返回内置技能和 `user_available: false`；用户分区显示配置提示而不是整页错误。
用户分区为空时仍显示导入入口；内置目录为空时显示明确空态。详情 `404` 提供返回列表操作。

## 11. 测试与验收

### 11.1 后端文件层

- 文件夹和 ZIP 正常导入，附属文件保持字节一致，结果默认停用。
- 活动/停用目录之间的原子切换、PATCH 同状态幂等 `200` 和永久删除。
- 内置、活动、停用三类同名冲突。
- 非法 frontmatter、目录名不一致、多个技能根、空包。
- `\\` 路径、绝对路径、`.`/`..`、ZIP 穿越、ZIP 符号链接、重复路径、加密/损坏 ZIP。
- 纯 base64、合法/非法 `data:` 前缀、`__MACOSX`/`.DS_Store` 忽略规则。
- 伪造或缺少 `Content-Length` 时仍按流式累计限制 36 MiB body；高压缩比 ZIP 按实际解压
  字节在 25 MiB 处中止，而非信任 archive metadata。
- 任一步失败后目标目录和临时目录均无残留。
- 单个手工损坏目录不会阻断其他技能枚举。

### 11.2 FastAPI

- 列表与详情数据形状、排序、`enabled/effective` 状态、无效详情 `instructions: null` 和虚拟路径。
- `400/404/409/413/503` 的中文错误合同。
- 配置缺失时内置列表/详情仍为 `200`；有效配置的缺失用户根自动以 `0700` 创建；配置路径在
  FastAPI 进程生命周期内固定，不随文件热变更。
- `VR_API_KEY` 继续保护全部技能 API。
- 测试只使用 `conftest.py` 在导入 `app` 前设置的临时 Agent 设置和技能目录。
- 响应、异常和日志均不包含真实路径或设置密钥。

### 11.3 LangGraph

- 新会话读取当时的活动用户技能。
- 已有会话在磁盘变化后、刷新前保持原技能缓存。
- 精确 `/reload-skills` 的字符串和纯文本 block 两种输入替换当前会话缓存并返回正确数量；
  含非文本 block、额外文本或相似命令不触发刷新。
- 刷新命令的模型调用次数和工具调用次数均为零。
- 非精确命令进入普通模型流程。
- `embedded_agent` 仍只有 `/builtin/`，固定工作流行为不变。
- 直接放入活动根的用户技能与内置同名时，内置 metadata 始终获胜，冲突用户路径不能被
  `read_file`；名称/目录不一致或其他严格校验失败的用户技能也不能进入提示或文件视图。
- 自定义 Skills system prompt 不包含执行脚本文案，只提及 `ls`/`read_file`；固定中立政策
  始终存在。
- 测试或静态检查证明生产代码不 import `deepagents.middleware.skills` 的下划线私有 helper。

### 11.4 前端与浏览器

- 双分区、计数、内置徽标、用户开关和空/加载/错误状态。
- 文件夹与 ZIP 导入，成功后显示为停用。
- 卡片导航与开关事件隔离，失败时保留原状态。
- `api.ts::request()` 支持 PATCH，技能页面所有请求均通过集中 API client。
- 用户删除确认、内置技能无删除入口。
- 详情 Markdown、虚拟路径和刷新命令提示。
- 待审批 HITL interrupt 存在时 Composer 禁用，不能发送 `/reload-skills` 或普通消息，审批
  `Command(resume=...)` 仍正常工作。
- 桌面两列与移动端单列，无文字溢出或控件重叠。
- 隔离浏览器验收完成“导入 -> 查看 -> 启用 -> 新会话生效 -> 旧会话执行
  `/reload-skills` 生效 -> 停用 -> 删除”，不触碰真实用户数据。

最终门槛为后端离线测试、前端 Node/Vitest 测试、`npm run build` 和新增 Playwright 场景
全部通过。

## 12. 成功标准

用户能够从技能管理页清楚区分内置与用户技能，安全地导入并审阅本地技能，通过启停控制新
会话可见性，并在指定旧会话中用 `/reload-skills` 显式刷新。停用、无效或与内置同名的用户
技能不能被 Agent 列出或读取，内置技能始终获胜；页面“问 AI”和固定工作流不获得用户技能，
任何流程都不弱化项目的客观中立边界。
