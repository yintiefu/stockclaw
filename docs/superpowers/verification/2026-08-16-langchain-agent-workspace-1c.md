# 1C 端到端验证记录（2026-08-16）

**结论：PARTIAL**（自动化门禁、mootdx live 冒烟、浏览器审批/steer-away/503 闭环全部通过；
仅剩真实 provider 审批流一项未执行——本环境无可用 API key。）

## 提交范围

- 1C 起点：`354cb5d`（1c 设计文档）
- 1C 提交：`18b1675..5a4f27a`（共 18 个提交）
- 切片收口：Slice 1 `f8b9ec7`、Slice 2 `7cba4e4`、Slice 3 `5e29319`
- 验收期修复：`ea3d13d`（agui metadata 命名空间）、`2b296a4`、`5a4f27a`

## Step 1 干净依赖安装 ✅ PASS

```
VR_1C_VERIFY=$(mktemp -d) && python3 -m venv "$VR_1C_VERIFY/venv"
backend: pip install -r requirements.txt（清华镜像）→ pip check 通过
pytest tests/agent/test_dependency_compat.py → 5 passed
mootdx==0.11.7+vr1 · mcp==1.26.0 · langchain-mcp-adapters==0.3.2 · httpx==0.28.1
```

## Step 2 自动化门禁 ✅ PASS（时间 2026-08-16）

```
backend: pytest -m "not live" → 343 passed, 15 deselected
frontend: npm test → 16 pass / 0 fail；vitest → 9 files / 54 tests passed
          npm run build（tsc -b + vite）→ 通过
git diff --check → 干净
```

测试与日志确认：全部 Agent 测试使用临时 `AgentServices` 根（conftest +
`build_services(tmp_path)`），无真实 `~/.vibe-research/agent` 读写；输出中无密钥值。
1A/1B 重复/重试/取消/历史回归全部保持绿色（迁移点：`patchThread` 签名、resume
validate 元组、middleware_factory）。

## Step 3 浏览器验收（CDP 127.0.0.1:16002）✅ 大部分通过

环境：`VR_DATA_DIR=$(mktemp -d)`，后端 :8901、前端 :5898（Vite 代理指向 8901），
puppeteer-core 驱动本机 Chrome。截图存 `/tmp/vr-e2e-shots/`（14 张）。

| 项目 | 结果 |
|---|---|
| Skill 导入（zip multipart）→ 列表/详情/指令预览 | ✅ |
| Skill 选择 → 一次 PATCH 应用（能力条显示“已选 1 个 Skill”） | ✅ |
| MCP stdio 新增（不 spawn 进程） | ✅（add 后 process_count=0） |
| 信任流：完整命令 + 指纹展示 → 确认 | ✅（修复 `AgentApiError.preview` 透传后） |
| 测试连接（health ✓ tools=N） | ✅ |
| 刷新目录（echo 等 6 工具发现、默认 disabled） | ✅ |
| 工具启用（PATCH tool_enabled） | ✅ |
| 本地 stub 模型触发 MCP 工具调用 → HITL 中断持久化 | ✅（pending interrupt + resume_available=true） |
| 刷新页面恢复审批面板（ApprovalPanel 3 选项渲染） | ✅（修复 metadata 命名空间 `ag-ui`→`agui`，见下） |
| 桌面 1440×900 / 移动 390×844 截图，无重叠溢出 | ✅ |
| 后端重启对账（无活跃 run → 干净启动；活跃语义由 1B 测试覆盖） | ✅ |
| stdio/HTTP fixture 无孤儿进程 | ✅（验收后 `pgrep` 为 0） |

验收中发现并修复的缺陷：
1. `AgentApiError` 未透传 `preview` 字段 → 信任预览无法显示（已修）。
2. MCP 新增表单默认 `enabled:false` 且无启用入口 → 相关 server 永不参与准入（已改默认 true）。
3. 信任按钮流程改为「显示完整命令 → 用户确认」两步（与规范 9.2 一致）。
4. **metadata 命名空间**：锁定 runtime 的 `AG_UI_METADATA_NAMESPACE` 常量是
   `"agui"`，而 1B 注入的是 `"ag-ui"` —— 刷新后审批面板无法水合。已改为
   `custom.agui.interrupts` 并迁移测试。

## Step 4 真实 provider 审批流 ⛔ NOT RUN

本环境没有可用的 OpenAI 兼容 function-calling key（密钥只存用户浏览器
localStorage，后端/测试均无）。**未执行**，需用户提供 provider 后补验。

## 浏览器审批提交闭环（approve/steer-away/503）✅ PASS（2026-08-16 二轮）

根因定位并修复：`@ag-ui/client` 的 `prepareRunAgentInput` 把 resume 放在请求体
顶层 `resume` 字段，而后端合同是 `forwardedProps.command.resume`（纯 resume 还要求
messages 为空）。在 `AgentHttpAgent.requestInit` 做协议翻译后全链路打通：

1. **中断+元数据**：stub 模型触发 `mcp__fixture__echo` 工具调用 → HITL 中断 →
   审批面板 2 秒内出现，含 server 名/alias/脱敏参数/三选项（截图 e2e-01）。
2. **approve once**：提交 → resume → 工具真实执行（thread JSON：
   user→assistant(tool_call)→tool("你好")→assistant 完成）（截图 e2e-02）。
3. **steer-away**：待审批中经 SteerAwayComposer 发新问题 → 旧 run 持久化为
   `cancelled/STEERED_AWAY`、旧工具零执行、新 run 正常准入（截图 e2e-03）。
4. **503 fail-closed**：把 fixture executable 改坏后发消息 → 预流 503 横幅 +
   「管理 MCP」入口、无自动重试、user 消息与 revision 零写入（截图 e2e-04）。

二轮中发现并修复的新缺陷：
- **配置变更后缓存会话未失效**：transport 改动后准入仍复用旧配置的健康会话
  （503 用例未触发）。已在 `McpRegistry.patch_server` 后把该 server 会话世代转
  draining，并补回归测试。
- 顺带验证了 Critical-2 修复：取消 awaiting run 后 MCP PATCH 不再报
  `MCP_CONFIG_BUSY`（租约正确释放）。

## Step 5 mootdx live 冒烟 ✅ PASS

```
pytest tests/test_live.py -m live -k 'mootdx_kline_route_live or mootdx_finance_route_live or mootdx_f10_live'
→ 3 passed in 2.93s（串行、真实 TDX 源）
```

## 密钥扫描

浏览器/后端日志与线程 JSON 中未发现任何模型密钥或 MCP secret 值；
`test_run_persistence` 的密钥不落盘断言保持绿色。

## 后续待办（不阻塞 1C PARTIAL 结论）

1. 用户提供真实 provider 后补 Step 4 记录。
2. `SkillManager.test.tsx` 目前并入 `CapabilityManagerDialog.test.tsx`（计划单列）。

## Review 修复（2026-08-16，代码评审后）

两个并行 review（后端/前端）后修复并回归：

- **Critical** `protocol._capture` 生产路径从未填充 MCP 元数据 → router 在中断持久化前经
  `enrich_pending_interrupts`（alias→binding + 模型密钥/secret set 脱敏）富集；新增生产
  capture 路径回归测试（camelCase 元数据 + thread_session 许可推导 + 参数脱敏）。
- **Critical** `RunCoordinator.cancel_run` 泄漏能力租约 → 改走 `_release_handle`；新增
  断连/REST 取消路径的 lease 恰好一次释放测试。
- **Important** 会话 drain 契约：引用获取与 accepting 检查移入同一 `_state_lock` 临界区；
  supervisor 在 stop 后等 in-flight 归零再物理关闭；远端调用内取消标记 draining；
  连接预算回到 15s。
- **Important** run 准入不再调用 `refresh()` 重写目录：新增 `_ensure_session`/`_discover_tools`
  只读发现路径（复用 accepting 会话、零 revision 递增、失败不误杀其他线程共享会话）。
- **Important** `mcp.json` 损坏 fail-closed：`update` 不再从空文档恢复；`/run` 捕获
  `McpError` 返回结构化错误。
- **前端 Important** Skill 覆盖导入改走认证 `agentApi.importSkill`（含 409→digest 确认）；
  信任预览按 server 定位（防跨 server 指纹误确认）并支持取消。
- **Minor** `mcp.py`/`skills.py` 死代码清理、PDF 改 iframe 预览、切 Skill 清除文本预览、
  503 横幅随管理器关闭清除、重复 eslint-disable、设计文档 metadata 命名空间同步为
  `custom.agui.interrupts`。

门禁：后端 345 passed（not live）；前端 vitest 54 + node 16 + strict build 全绿。
仍未解决（与前文一致）：浏览器内 runtime resume 提交集成、真实 provider E2E。
