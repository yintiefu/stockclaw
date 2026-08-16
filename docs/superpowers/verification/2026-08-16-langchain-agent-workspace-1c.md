# 1C 端到端验证记录（2026-08-16）

**结论：PARTIAL**（自动化门禁与 mootdx live 冒烟全部通过；浏览器审批提交闭环与真实
provider 审批流两项未完成，见「未完成项」。）

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

## 浏览器审批提交闭环（approve/reject/steer-away）⚠️ PARTIAL

- 后端链路完整且由 pytest 覆盖：三合法决策组合、许可写于持久化之后、
  interrupt camelCase 元数据、`MCP_UNAVAILABLE` 503、allowance 清理。
- 浏览器内：审批面板可恢复渲染、SteerAwayComposer 正确替换普通输入框；
  但提交决定时锁定 runtime 以 start 形状重发请求（HTTP 400
  `INVALID_REQUEST_SHAPE`），未携带 `forwardedProps.command.resume` 条目。
  怀疑是 `useAgUiSubmitInterruptResponses` 对“从历史恢复（非活跃 run 注册）”
  的 interrupts 不构造 resume 载荷；属 @assistant-ui/react-ag-ui 集成缺口，
  需在活跃 run 内（不刷新页面）提交或进一步适配该 hook。

## Step 5 mootdx live 冒烟 ✅ PASS

```
pytest tests/test_live.py -m live -k 'mootdx_kline_route_live or mootdx_finance_route_live or mootdx_f10_live'
→ 3 passed in 2.93s（串行、真实 TDX 源）
```

## 密钥扫描

浏览器/后端日志与线程 JSON 中未发现任何模型密钥或 MCP secret 值；
`test_run_persistence` 的密钥不落盘断言保持绿色。

## 后续待办（不阻塞 1C PARTIAL 结论）

1. 适配 `useAgUiSubmitInterruptResponses` 的历史恢复 resume 路径（或活跃 run 内验证）。
2. 用户提供真实 provider 后补 Step 4 记录。
3. `SkillManager.test.tsx` 目前并入 `CapabilityManagerDialog.test.tsx`（计划单列）。
