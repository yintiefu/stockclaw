# 板块中心「上中下游产业链」详情页 — 设计文档 v6

- 日期：2026-08-13（v6：四轮评审，hook 竞态根因修复）
- 范围：板块中心详情页（`SectorDetail`）升级为「上中下游 → 子板块 → 成分股」三级结构；成分股本地增删/隐藏恢复；可复用的产业链导入流程。
- 试点：**人形机器人**（完整骨架，全叶子可导入）、**AI 产业链**（复用 `ai-computing` key，**本轮仅骨架占位**，多数 plate_id 待人工核实回填）。
- 状态：**待数据验证**（具身大模型归属、AI 板块最终命名/plate_id 待导入时落定——已用稳定 `id` 解耦，改名/补 id 不影响数据）。
- 定位：**完整骨架（仓库）+ 可选富途成分股增强（本地导入）**。开箱即得骨架与「我的关联标的」能力；来源成分股需用户本地跑导入脚本（需富途 OpenD + uv）。

> **v5→v6**（四轮评审）：① **hook 竞态根因修复**——纯乐观并发状态机（token 单调 ack、失败丢 diff、绑 key），替代 v5 的「整份替换 + 无条件逆操作 undo」；机器为纯函数并有乱序/部分失败/幂等/切 key 自动测试；② **容量契约统一**（后端每 leaf 上限 50→200，脚本 limit/page-size 校验 1..200）；③ **--page-size 真贯通**（`_build_base` 下传 + main 校验 + 测试断言真实 count/page）；④ schema 测试移到骨架任务之后（Task 9.5）；⑤ 原子写测试改**跨进程读者 + 屏障**（≥20 轮有效竞争）；⑥ 跨进程测试 **Windows spawn 兼容**（模块级 worker + 超时兜底）；⑦ **损坏 JSON 不丢数据**（`CorruptStoreError`：备份+移除、读降级、写拒绝并自愈）；⑧ 脚本**异常统一捕获**（富途异常带 leaf/plate 上下文；poster DNS/拒连/超时清晰 stderr + 非零退出）。

> **v4→v5**（三轮 Important 项）：① 富途**分页**（循环 `count/page` 到收足 limit 或 `next_page is None`）；② hook 竞态（请求 epoch + 函数式更新）；③ 前端测**生产 `.ts`**（tsx），删 `.mjs` 副本；④ **跨进程/原子写**真测试（multiprocessing）；⑤ **main() 提交边界**测试（注入依赖，零 POST + ctx.close 一次）；⑥ `ai-computing` 本轮**仅骨架占位**；⑦ UI **a11y**（aria/键盘/对话框焦点/mutation toast）；⑨ schema/数量边界/形状测试补齐。

> **v3→v4**（用户拍板）：① 去掉「市值前 8」排名语义——按数据源原序截取，可选 `--limit`，UI 不写市值排序（对齐 ROADMAP「不做个股排名」）；② 全有或全无导入——任一叶子抓取失败则不 POST import；③ 文件锁（`fcntl`/`msvcrt`）覆盖跨进程/多 worker，线程锁仍串行本进程；④ 轻量校验（前缀 + 大小/长度上限）；⑤ per-sector `meta`；⑥ mutation 统一返回 `{meta, leaves}`；⑦ 对齐 `futu-api==10.9.6908` 真实契约（成功 4 元组，列仅 `security/name`）；⑧ 补齐恢复入口、「如何导入」、两组分区标题与失败回滚测试。

> v2→v3 摘要：稳定 id、`{schema_version, meta, sectors}`、单一 HTTP 写入通道、骨架+可选增强、前端验收。

---

## 1. 背景与目标

当前 `frontend/src/data/sectors.json` 每个板块只有扁平 `nodes: string[]`，详情页是一排标签，无上中下游、无成分股、不能增删。

目标：
1. 详情页支持**上中下游三段**，段下是**子板块（块状卡片）**；子板块有下级时块内以 **tag** 显示。
2. 成分股**点击才展开**，列表呈现（名称/代码），分「来源成分股」与「我的关联标的」；来源项可隐藏/恢复、个人项可增删。
3. **仓库只发骨架**（段/块/叶子名 + `id` + `plate_id`，**不含股票**）；成分股由用户本地导入生成，不进仓库。
4. 可复用导入流程，把任意产业的富途产业链数据导入成本地成分股。

## 2. 关键决策

- **不能全自动分类上中下游**：上中下游是领域知识。富途链顶层是领域视角（人形机器人＝大脑/身体/整机；AI＝基础建设/算法/应用），覆盖不均（AI 应用层空）。导入为**半自动**：人维护骨架、脚本抓数据。
- **成分股不进仓库（用户决策③）**：仓库 `sectors.json` 只存骨架；成分股存本地 `~/.vibe-research/sector-stocks.json`。→ 仓库无个股/无排名，规避「不做个股排名」边界与富途再分发顾虑。
- **导入截取策略（v4 / 用户决策 1-A，取代 v3「市值前 8」）**：
  - 每叶子：筛 A/港/美（`SH./SZ./HK./US.`）→ **保持数据源返回顺序** → 叶内去重 → 可选 `--limit`（默认 8）截取。
  - **禁止**本地按市值/涨跌/评分排序；**禁止** UI/文案出现「市值前 N」「排名」。
  - 原因：① `ROADMAP.md` / `AGENTS.md` 明确禁止个股排名；② 固定 SDK `get_industrial_plate_stock` 成功 DataFrame **仅有** `security, name`，无 `market_value`，本地市值排序不可行。
  - **如实说明（v6）**：`get_industrial_plate_stock` 的 `sort_field` 默认按市值降序返回，故「数据源原序」客观上即为富途的市值降序；本项目**不额外排序、不标注排名**，仅按数据源返回原样截取并显式声明「非本项目排名」。若日后要求真正中性顺序，可在 `_real_ctx_factory` 传 `sort_field=PlateStockSortField.CODE`（本轮不做，属后续范围）。
  - 若未来需要「更全列表」，只调大 `--limit` 或取消 limit，仍不引入评价性排序。
- **本轮不做跳个股页（用户决策②）**：成分股列表仅展示。
- **单一写入通道 + 文件锁（v4）**：
  - 所有对本地成分股文件的写入（导入 base、隐藏/恢复、增/删我的关联）一律经后端 HTTP。
  - 导入脚本与任何外部进程**不直接读写该文件**，也不读 `VR_DATA_DIR`。
  - 后端：`threading.Lock`（同进程串行）+ **跨进程文件锁**（POSIX `fcntl.flock`；Windows `msvcrt.locking` 或等价）包住 load→mutate→atomic save。
  - 原子写仍用 `tmp + os.replace`。
- **导入失败策略（v4 / 全有或全无）**：脚本对任一叶子调用富途失败（`ret != RET_OK`、异常、超时）→ **整次导入中止**，不调用 `POST /import`，已有本地 base 不变；stderr 打印失败 leaf/plate 与原因。成功路径才组装完整 `base` 一次提交。
- **轻量输入校验（v4）**：见 §6；不做 leaf∈骨架校验（避免后端强耦合 skeleton 文件；错误 leaf 只是多一个空用 id，可被用户忽略）。

## 3. 数据格式

### 3.1 `frontend/src/data/sectors.json`（仓库骨架，进仓库，**不含股票**）

向后兼容：保留现有字段；新增可选 `chain_id`、`tiers`。其余 **17** 个板块不动（共 19，试点 2）。

```ts
interface SectorItem {
  id: string;                // 稳定标识，latin slug，sector 内唯一；改名不影响数据
  name: string;              // 展示名（可改）
  desc?: string;             // 块头一句话
  plate_id?: string;         // 富途板块 ID；导入脚本据此抓成分股（仅叶子、仅 futu 源）
  source?: "futu" | "manual";
  children?: SectorItem[];   // 非空数组=分组块（块内渲染为 tag）；缺省=叶子
}
interface SectorTier { id: string; name: string; items: SectorItem[]; }  // 约定 "上游 · …"
interface Sector {
  key: string; label: string; tagline: string;
  hot: boolean; verified: boolean;
  nodes: string[];           // 列表页/回退用；有 tiers 时详情页忽略
  chain_id?: number;         // 富途主链 ID
  tiers?: SectorTier[];      // 存在即走三段视图
}
```

约束（schema 校验在测试里强制）：
- `id`：tier 与每个 item 必填；`^[a-z0-9][a-z0-9_-]*$`；**sector 内全局唯一**（跨 tier 不重）。
- `children` 非空数组 → 分组块；缺省 → 叶子；**禁止 `children: []`**。
- 最多 **3 层**：tier → block → leaf；分组块最多一层。
- 叶子的成分股不在本文件——由本地文件按叶子 `id` 提供。

### 3.2 `~/.vibe-research/sector-stocks.json`（本地，不进仓）

```ts
interface SectorStocksFile {
  schema_version: 1;
  // 注意：v4 起 meta 按 sector 存，不再用文件级全局 meta（避免 humanoid / ai-computing 互相覆盖）
  sectors: Record<string, SectorStocksBucket>;  // [sectorKey]
}
interface SectorStocksBucket {
  meta: SectorImportMeta;                 // 该板块最近一次成功导入的元数据；从未导入可为 {}
  leaves: Record<string, LeafStocks>;     // [leafId]
}
interface SectorImportMeta {
  sdk?: string;                 // "futu-api==10.9.6908"
  opend_host?: string;          // 例 "192.168.1.30:11111"
  fetched_at?: string;          // ISO 时间
  mapping_version?: string;     // 骨架/映射版本标识（如 chain_id）
  import_note?: string;         // "数据源返回原序截取；非市值排名"
  totals?: Record<string, number>;  // {leafId: 成分股数}
}
interface LeafStocks {
  base:   SectorStock[];   // 来源成分股（import 端点写；数据源原序 + limit 截取）
  hidden: string[];        // 用户隐藏的 base 代码（tombstone）
  mine:   SectorStock[];   // 我的关联标的（用户添加）
}
interface SectorStock { code: string; name: string; ts?: number }  // code: SH./SZ./HK./US.
```

**API 对外形状（GET 与所有 mutation 统一）**：

```ts
interface SectorStocksData {
  meta: SectorImportMeta;
  leaves: Record<string, LeafStocks>;
}
```

运行时合并（前端，每叶子，按 `leafId`）：
- 来源成分股（展示）= `base − hidden`
- 我的关联标的（展示）= `mine`（带「我的」角标）
- 状态机：
  - `base` 仅 `import` 端点写（重新导入整体替换该请求中出现的叶子 base，**保留 hidden/mine**；未出现在 base_map 的叶子不动）。
  - 隐藏：code 入 `hidden`（幂等）。
  - 恢复：从 `hidden` 移除（幂等）。
  - 加我的关联：push `mine`（`(sector,leafId,code)` 唯一；重复无操作）。
  - 删我的关联：从 `mine` 移除。
  - `base` 与 `mine` 可含同 code（两组分别显示、分别标注）。
  - 所有写入经后端线程锁 + 文件锁串行。

## 4. 板块范围与上中下游映射（骨架，含 id）

### 4.1 人形机器人（`humanoid`，富途主链 9610089）
```text
上游 · 核心零部件 (id: upstream)
  减速器 (reducer) → 谐波减速器(harmonic,10104257) · RV减速器(rv,10104258) · 行星减速器(planetary,10104259)
  电机 (motor)     → 无框力矩电机(torque,10104254) · 空心杯伺服电机(hollow,10104255) · 伺服驱动器(servo,10104256)
  丝杠 (leadscrew) → 行星滚柱丝杠(planetary-screw,10104261) · 滚珠丝杠(ball-screw,10104260)
  传感器 (sensor)  → 力/扭矩传感器(force,10104267) · IMU惯导(imu,10104266) · 激光雷达(lidar,10104265) · 摄像头(camera,10104264)
  轴承(bearing,10104262) · 编码器(encoder,10104263)
  芯片 (chip)      → AI芯片(ai-chip,10104250) · 存储芯片(storage,10104251)
  结构件 (struct)  → 机身结构件(body,10104272) · 电池系统(battery,10104270) · 热管理(thermal,10104273)
  灵巧手 (dexterous-hand, manual, 无 plate；base 可空，由用户挂)
中游 · 整机集成 (id: midstream)
  整机厂商 (integrator) → 车企系(auto,10104274) · 消费电子系(consumer,10104275) · 专业厂商(pro,10104277) · 互联网/电商系(internet,10104276)
下游 · 应用场景 (id: downstream，manual，无 plate、无成分股)
  工业制造(industrial) · 商业服务(commercial) · 家庭陪伴(home) · 特种作业(special)
```
> 「六维力传感器」并入 force；「具身大模型」归属待导入时定（id 预留，改名不影响已存数据）。

### 4.2 AI 产业链（复用 `ai-computing` key，富途 9610020 稀疏 → 混合）
```text
上游 · 算力基础设施 (id: ai-upstream)（富途基础建设层 + 现有 ai-computing 节点合并去重）
  芯片(ai-chip) · 网络互连(ai-network) · 散热(ai-cooling) · 基础设施(ai-infra) · 能源(ai-energy)
中游 · 算法与模型 (id: ai-mid) → 算法模型(ai-algo,10010163)
下游 · AI 应用 (id: ai-down，manual，无成分股) → Agent · 办公 · 教育 · 垂类
```
> **本轮范围（v5）**：`ai-computing` **仅落地骨架**。上游各叶子的 `plate_id` 需**人工对照富途客户端逐一核实**，不能臆造，故本轮多数上游叶子不带 `plate_id`；导入脚本只收带 `plate_id` 的叶子，因此本轮实际**仅 `ai-algo` 可导入**，其余上游叶子显示「未导入」并可手工加「我的关联」。plate_id 核实列为**后续独立任务**（见 §11）。骨架已用稳定 `id` 解耦，将来补 id 不影响已存本地数据。`label` 可微调为「AI 产业链」（待定）。

## 5. 前端 UI（定稿 + v4 修订）

- **整体：纵向堆叠三段**（方案 A）。每段一张 `GlassCard`，段头 `▎` 橙条 + 段名 + 小字注。
- **子板块 = 块状卡片网格**（桌面 2 列，响应式）。每块：`名称 + 一句话 + 「N 只」+ 下级 tag`。
  - 下级 tag 冷色（蓝）描边；激活高亮；无下级的块点击直接展开。
  - 「N 只」= 该块下各叶子 `(base−hidden)+mine` 合计；未导入且无 mine 显示「未导入」。
- **成分股点击展开、列表展示**（块内就地）：表头 `名称 | 代码`；代码等宽、带前缀。
  - **两组分区标题**：「来源成分股」与「我的关联标的」（不得合并成单一标题）。
  - 来源项行尾常驻「隐藏」（触屏可用）。
  - **恢复入口**：块头或展开区显示「已隐藏 N」+「恢复」列表/按钮（对 `hidden` 中仍存在于 base 或历史 tombstone 的 code 可逐条 restore）。
  - 我的关联项行尾「移除」。
  - 列表底部「＋ 添加我的关联」→ 展开输入行（名称 / 代码 / 保存 / 取消）；输入须有可见 label（`label`/`aria-label`）；移动端输入行避免固定四列挤爆（`flex-col sm:grid` 或换行）。
  - **不可点击跳转**（本轮）。
- **可访问性与交互（v5）**：
  - 展开控件为 `<button>`，`aria-expanded`/`aria-controls` 指向面板 `id`（`role="region"`）；Enter/Space 展开，Esc 收起。
  - 隐藏/恢复/移除按钮带 `aria-label`（含标的名称），触屏尺寸 ≥36px。
  - mutation 失败由**组件层**统一 `toast.error`（hook 保持纯逻辑，不在内部 toast）。
  - 添加表单为 `<form onSubmit>`，输入有可见 `<label>`，Enter 提交；本地校验代码前缀，非法直接 toast 不发请求。
  - 「如何导入」用原生 `<dialog>`（或等价 focus-trap）：打开焦点入框、Esc/遮罩关闭、关闭焦点回触发按钮；命令含当前 `key`。
- **未导入态**：本地该叶子无 base 时，提示「未导入来源成分股」+ 可发现的 **「如何导入」** 入口（弹简短说明：需富途 OpenD + `uv run … import-sector-chain.py` 命令示例 + 可先跑 `--diagnose`），并可先手工「添加我的关联」。
- **加载失败**：与「未导入」区分——`useSectorStocks` 保留 `error` 状态；失败时 toast/横幅「成分股加载失败」（`role="alert"`），**不**把空对象伪装成未导入。
- **hook 竞态（v6 纯状态机）**：乐观并发逻辑下沉为纯状态机（`OptimisticState` + `beginMutation/ackSuccess/ackFailure/setKey`）。GET 用请求 epoch；mutation 带 token 提交，成功 ack 按 token **单调推进**（乱序/过期响应忽略，不覆盖更新的已提交态）；失败**丢弃该 token 的 pending diff**（幂等操作 diff 为 none，不误删/误恢复）；每个 mutation 绑 sector key，切换板块后旧 key 的 ack 被忽略。机器为纯函数，hook 仅薄封装；乱序/部分失败/幂等/切 key 均有自动化测试。
- 文案合规：说明「来源成分为本地导入的客观列表（数据源原序截取，非排名）；不推荐个股」。
- 组件：`SectorDetail.tsx`（有 `tiers` 走新视图，否则回退扁平标签）；`useSectorStocks(key)`；`api.ts` 新增方法；`sectorStocks.ts` 类型 + `mergeLeaf`；复用 `Disclaimer`。

## 6. 后端 — 本地成分股读写（新模块 `backend/sectorstocks.py`）

仿 `portfolio.py`：路径 `VR_DATA_DIR or ~/.vibe-research` 下 `sector-stocks.json`，**import 时固化**（测试隔离随 `conftest.py`）。

并发模型：
- `threading.Lock`：同进程内串行。
- **文件锁**：跨进程/多 worker 互斥；持锁范围覆盖 `_load` → 修改 → `_save`。
- 原子写：`*.tmp` + `os.replace`。

路由（`app.py`）——**所有写接口与 GET 均返回** `{"data": SectorStocksData}` 即 `{meta, leaves}`：

| 方法 | 路径 | 体 | 说明 |
|---|---|---|---|
| GET | `/api/sectors/stocks?key=` | — | `{meta, leaves}` |
| POST | `/api/sectors/stocks/import` | `{key, base:{leafId:[stocks]}, meta}` | 替换 base_map 中各叶子 base、保留 hidden/mine、写入**该 key 的** meta |
| POST | `/api/sectors/stocks/mine` | `{key,leaf,code,name}` | 加我的关联（幂等） |
| DELETE | `/api/sectors/stocks/mine` | query: key,leaf,code | 移除我的关联 |
| POST | `/api/sectors/stocks/hide` | `{key,leaf,code,name?}` | 隐藏来源项（幂等） |
| DELETE | `/api/sectors/stocks/hide` | query: key,leaf,code | 恢复来源项 |

**轻量校验（v4，v6 调整容量）**：
- `key` / `leaf`：非空；长度 ≤ 64；匹配 `^[a-zA-Z0-9][a-zA-Z0-9_-]*$`，否则 400。
- `code`：strip+upper；须含 `.` 且前缀 ∈ `{SH.,SZ.,HK.,US.}`，否则 400；总长 ≤ 32。
- `name`：可选；长度 ≤ 64。
- import：`base` 最多 200 个 leaf；**每 leaf 最多 200 只股票**（v6：与脚本 `--limit`/`--page-size` 上限 1..200 对齐，避免跨页抓足后被后端 400）；每 stock 须有合法 `code`。
- 缺字段：Pydantic **422**。
- **不做** leaf ∈ 骨架校验（轻量档）。
- **损坏数据（v6）**：写接口遇 `sectorstocks.CorruptStoreError` → HTTP **500**「本地成分股数据损坏，已自动备份；请重试」（一次性，备份+移除后自愈）；读接口降级返回空，不 500。
- `VR_API_KEY` 中间件自动鉴权；导入脚本传 `--api-key`。

## 7. 导入机制（经后端单通道，可复用）

脚本 `scripts/import-sector-chain.py`（`uv run --with "futu-api==10.9.6908"`；**不再依赖 pyyaml**，直接读 JSON）：

- 读仓库 `frontend/src/data/sectors.json` 中 `--key` 的 `tiers`，收集 `(leafId, plate_id)`。
- 参数：`--key humanoid --backend http://127.0.0.1:8900 --api-key $VR_API_KEY --opend-host 192.168.1.30:11111 --limit 8`。
- **富途契约（固定 SDK 10.9.6908，必须遵守）**：
  - `OpenQuoteContext.get_industrial_plate_stock(plate_id=…, count=…, page=…)`
  - **成功**：`(ret, data, next_page, all_count)`，`ret == 0`（`RET_OK`），`data` 为 DataFrame，列 **`security, name`**（无 market_value）。
  - **失败**：通常 `(ret, err_msg, …)` 且 `ret != 0`；适配层须兼容 2~4 元组，**禁止**假设恒为 2 元组。
  - 适配层把 DataFrame 转成 `list[dict]`：`{"security": …, "name": …}`。
- **分页（v5）**：SDK 单页默认/上限影响取数。脚本按 `--page-size`（默认 50）取页，**循环**：过滤+去重+原序累计，收足 `--limit` 或 `next_page is None` 即止。`--limit > page_size` 时自动跨页补足。任一页 `ret != 0` 视为该叶子失败（触发全有或全无）。
- 流程：
  1. 对每叶子 `plate_id` 分页调适配后的 plate 函数；无 plate 的 manual 叶子跳过。
  2. 任一叶子（任一页）`ret != 0` 或抛错 → **立即失败退出**，不 POST（全有或全无）。
  3. 筛 A/港/美前缀；**保持返回顺序**；去重；`--limit` 截取。
  4. 组装 `base` 与 **该 sector 的** `meta`（含 `import_note: "数据源返回原序截取；非市值排名"`）。
  5. `POST /api/sectors/stocks/import`。脚本不读写本地文件、不读 `VR_DATA_DIR`。无论成败，富途 `OpenQuoteContext` 在 `finally` 中 `close()` **恰好一次**。
- `--diagnose`：检查 futu-api 可 import、OpenD TCP 可连通、后端 `/api/health` 可达；非 0 退出码表示未就绪（供 UI「如何导入」引用）。
- 加新产业 = 在 `sectors.json` 写 `tiers`（id+plate_id）+ 跑脚本。

## 8. 合规

- 仓库 `sectors.json` **不含任何股票/排名**。
- 导入与展示**不生成个股市值排名**；截取仅为长度控制，文案明确「非排名」。
- 更新 `sectors.json` 的 `_comment`：`tiers` 仅为骨架，标的由用户本地导入与维护。
- 复用 `Disclaimer`。
- 对齐 `ROADMAP.md`「明确不做：荐股、目标价、评级、个股排名」与 `AGENTS.md` 产品硬边界。

## 9. 兼容性

- 其余 **17** 个板块（无 `tiers`）详情页不变（扁平标签回退）。
- `sectors.json` 字段只增不改；`Sectors.tsx` 列表页不受影响。
- 本地无 `sector-stocks.json` → 后端按空 `{meta:{}, leaves:{}}`；前端骨架 +「未导入」+ 可手工加我的关联。
- 若读到旧版文件级全局 `meta`（v3 草案形态），加载时忽略或迁移为按 sector 空 meta，不崩溃。

## 10. 测试

- 后端 `backend/tests/test_sectorstocks.py`（`not live`）：
  - base/hidden/mine 状态机（幂等、唯一、恢复、重导入保留 hidden/mine）。
  - **原子写**：**跨进程**写者 + 跨进程读者，读者完成 ≥20 轮，只看到完整旧版或新版 JSON，不抛、不读半截。
  - **文件锁/并发**：多线程交错 import 与 hide，**最终同时保留 import 与 hide 的效果**（非互相覆盖）。
  - **跨进程**：`multiprocessing` + 屏障制造确定性交错，验证两进程各 N 条全部落盘、无 lost update（**Windows spawn 兼容**：模块级 worker + 超时/terminate 兜底）。
  - **损坏 JSON（v6）**：写损坏文件 → 读降级空、原文件备份移除、其后写入自愈；损坏时写入抛 `CorruptStoreError`、不覆盖。
  - 路由契约：422 缺字段 / 400 非法 code·过长 key·过长 name·超容量 / 200；GET 路径含 `data.leaves`；**所有 mutation 返回 `{meta, leaves}`**。
  - import 后 **per-sector meta**（导入 B 不覆盖 A 的 meta）。
  - 轻量校验边界：leaf 数上限(200)、每 leaf 成分股数上限(200)、name 过长。
  - schema 校验（禁 `children:[]`、限三层、`id` 唯一与格式、无禁词）：**读取真实 `sectors.json`** 对试点板块跑（置于骨架任务之后）。
  - 测试间隔离：fixture 独立 `VR_DATA_DIR`（`rebind_paths_for_tests` 重置损坏标志），禁止顺序依赖。
- 导入器 `test_import_chain.py`（mock 富途）：
  - 单元 `_pick_constituents`：4 元组、A/港/美过滤、原序 + limit、去重、**分页**（跨页收足、`next_page is None` 停止、第二页失败抛错）。
  - **main() 提交边界**：注入 `ctx_factory`/`poster`/`sectors_path`；成功路径 POST 恰好 1 次；抓取失败 / 分页失败 / HTTP 错误 / **富途异常** 均为**零 POST**；`ctx.close()` **恰好一次**。
  - **--page-size 下传**：断言 `plate_fn` 收到的 `count == --page-size`、分页 `page` 推进。
  - **范围校验**：`--limit`/`--page-size` ∉ 1..200 → 退出码 2。
  - mock 使用 `ret=0`，**禁止**用布尔 `True` 充当成功码。
- 前端：
  - `frontend/tests/sector-merge.test.mjs`：**经 tsx 加载生产 `sectorStocks.ts`** 测 `mergeLeaf` **与乐观并发状态机**（乱序成功/部分失败/幂等/切 key）。
  - 构建：`npm run build`（`strict` + `noUnusedLocals`）通过。
  - 浏览器验收：展开/折叠（含 `aria-expanded/controls`、键盘 Enter/Space/Esc）、隐藏/**恢复**、添加/移除（可见 label、Enter 提交）、**如何导入对话框焦点管理**、失败乐观回滚 + toast、**加载失败 vs 未导入**、两组分区、触屏常驻按钮、扁平板块回退、**并发点击无脏状态**、**limit>page-size 跨页取足**。

## 11. 不在本轮范围

- 其余 17 板块迁移到 `tiers`。
- **`ai-computing` 上游叶子 `plate_id` 的人工核实与回填**（本轮仅骨架，仅 `ai-algo` 可导入）。
- 成分股点击跳个股页。
- 运行时（页面内）导入富途链。
- 成分股实时行情列。
- 跨市场币种统一 / 任何评价性排序。
- leaf ∈ 骨架的严格服务端校验。

## 附录：评审追踪

| 评审项 | 处理 |
|---|---|
| **hook 竞态根因（v6 Critical）** | 纯乐观并发状态机：token 单调 ack / 失败丢 diff / 绑 key；乱序·幂等·切 key 自动测试 |
| **容量契约（v6）** | 后端每 leaf 50→200，与脚本 limit/page-size 1..200 对齐 |
| **--page-size 贯通（v6）** | `_build_base` 下传 + main 校验 + 测试断言真实 count/page |
| **schema 测试顺序（v6）** | 迁至 Task 9.5（依赖 Task4/9 骨架） |
| **原子写真竞争（v6）** | 跨进程读者 + 屏障，≥20 轮有效 |
| **跨进程 Windows（v6）** | 模块级 worker + spawn ctx + 超时/terminate |
| **损坏 JSON（v6）** | `CorruptStoreError`：备份+移除、读降级、写拒绝并自愈 |
| **脚本异常（v6）** | 富途异常带 leaf/plate 上下文；poster 网络错误清晰 stderr + 非零退出 |
| C1 市值前 8 越边界 | **v4 用户 1-A：废除排名语义**，原序+limit；对齐 ROADMAP |
| 富途 SDK 契约 | 成功 4 元组 + 仅 security/name；适配层兼容失败 2~4 元组 |
| **富途分页（v5）** | 循环 `count/page` 到收足 limit 或 `next_page is None`；`--limit>50` 可取全 |
| 抓取失败清空 base | **全有或全无**：失败不 POST |
| 跨进程 lost update | HTTP 单通道 + 线程锁 + **文件锁**；multiprocessing 测试 |
| 文件级 meta 覆盖 | **per-sector meta** |
| mutation 返回形状 | 统一 `{meta, leaves}` |
| **hook 竞态（v5）** | GET 请求 epoch；mutation 函数式更新 + 按操作 undo |
| **前端测副本（v5）** | tsx 加载生产 `.ts`，删 `.mjs` 副本 |
| **main() 提交边界（v5）** | 注入依赖；失败/分页/HTTP 错误零 POST；ctx.close 一次 |
| **ai-computing 二试点（v5）** | 本轮仅骨架占位，plate_id 核实列为后续任务 |
| **UI a11y（v5）** | aria/键盘/对话框焦点/mutation toast/表单 label |
| 输入校验 | 轻量：前缀 + 长度/数量上限 |
| UI 缺口 | 恢复入口、如何导入、两组分区、加载失败区分 |
| 测试/自评不符 | 测试清单与实现片段对齐；schema/边界/形状补齐；禁 `git add -A` |
| I3 schema | `{schema_version, sectors:{key:{meta,leaves}}}` |
| I4 稳定 id | 保留 |
| I5 默认体验 | 骨架 + 可选增强 + 如何导入 + diagnose |
| I6 前端验收 | 保留并扩充 |
