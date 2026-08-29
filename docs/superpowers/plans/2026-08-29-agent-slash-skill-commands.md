# Agent 工作台斜杠命令选取技能 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Agent 工作台的 Composer 支持 `/` 斜杠命令弹层:输入 `/` 弹出可用技能列表(名称 + 描述),继续输入过滤,↑↓ 导航、Enter 选中、Esc 关闭;选中后把 `/query` **原位替换**为一句模型可读的中文指令(含技能虚拟路径,如 `请使用技能「stock-analysis」（先 read_file /builtin/stock-analysis/SKILL.md）：`),前后文本原样保留、caret 落在指令后第一个空白之后,用户继续补问题后正常发送。

**Architecture:** 纯前端改动,零后端变更。使用已安装的 `@assistant-ui/react` 0.15.16 内置 TriggerPopover 体系:`ComposerPrimitive.Unstable_TriggerPopoverRoot` 包住 WorkspaceComposer 的 `ComposerPrimitive.Root`;命令数据源是现成的 FastAPI `GET /api/skills`(`api.skills()`),只取 `effective` 的技能。**选中行为 = Action + 自定义 DirectiveFormatter**:`serialize(item)` 返回中文指令文本,框架在 `selectItem` 内一次性计算 `before + directive + 分隔空格` 并单次 `setText`——单次写入、无读回,规避 store 快照竞态。**空候选/整消息级保留命令时不注册 behavior**(behavior 是弹层 `open` 的必要条件),弹层关闭、Enter 回退 Composer 自带发送。

**基线分支：** `main` → `feat/agent-slash-skills`。

**修订记录：**
- **R2(评审 R1 后,7 项源码核实成立)**:formatter 单次写入(弃 execute 读回写,C1 竞态);空候选不注册 behavior(Enter 吞键);注入文本带虚拟路径;原位替换语义;Item onMouseDown 拦截失焦;补键盘/发送覆盖;fixture 类型标注。
- **R3(评审 R2 后,6 项核实成立)**:空候选判定改 `scope.query` 直查 adapter(items 仅在 open 后生成,读 items 判空死锁);`reload-skills` 保留;onExecute 落 DOM caret(框架 setCursorPosition 不动 DOM);ArrowUp 补测;`tsconfig.test.json` + `typecheck:test`(主 tsconfig 排除测试);「路径自足」措辞软化。
- **R4(评审 R3 后,2 Critical + 5 Important + 2 Minor 全部采纳)**:
  - **C1(typecheck 必败)**:①`Unstable_TriggerAdapter.search` 是**可选**方法,`adapter.search(q).length` 报 TS2722 → 定义本地 `SkillSlashAdapter` 类型(search 必选);②`getByRole` 缺省泛型 `HTMLElement`,测试用 `selectionStart`/`setSelectionRange` 报 TS2339/TS2551 → `getByRole<HTMLTextAreaElement>("textbox", …)`。
  - **C2(caret 契约矛盾 + indexOf 回跳)**:实现固定落在 directive 末尾,而末尾场景测试期待分隔空格之后——矛盾成立。→ 统一规则:**caret 落在 directive 之后的第一个空白后**(与框架自身 `setCursorPosition(before+directive+1)` 一致;两种 separator 情形下该位置都有效)。位置计算改为**基于本次 trigger 的原始 caret**:`onExecute` 同步段读 `textarea.selectionStart`(框架 setText 尚未重渲染,DOM caret 仍是原值),`target = 原caret − (query.length + 1) + directive.length + 1`,`setTimeout(0)` 等重渲染后写入——不做全文 `indexOf(directive)`(同一技能二次插入会跳回第一条指令)。
  - **I1(reload-skills 精确语义)**:后端 `is_reload_command` 比较的是**整条消息**,query 级比较会错杀(`请 /reload-skills` 时同名技能被错误隐藏)。→ 保留条件收紧为「query === `reload-skills` **且** 当前 composer 全文恰为 `/reload-skills`」(textarea.value 直读)——与后端整消息语义对齐;fixture 加**真 `reload-skills` 技能**,三向测试:裸 `/reload-skills` 保留直发;`/reload-skill` 前缀照常弹层;`请 /reload-skills`(有前文)照常弹层。
  - **I2(条件卸载的吞 Enter 窗口)**:Action 经 passive useEffect 注销,「items 已空、behavior 仍在」窗口理论上存在;React 18+ 在处理下一个离散事件前 flush 挂起的 passive effects,实际窗口对真实输入关闭。→ 加单次调用回归测试 `user.type(input, "/zzz{Enter}")`(输入与 Enter 最小间隙);若失败升级为同步失效方案。
  - **I3(过滤假阳性)**:`/^debate/` 永不匹配(option 可访问名以 `/debate` 开头),断言恒真 → 改 `/^\/debate/`。
  - **I4(未跟踪文件)**:`AgentSlashSkills.test.tsx` 是本轮 Task 1 写的 R1 草稿(未提交),Task 1 明确为**重写替换**,非覆盖用户内容。
  - **I5(浏览器验证动真实模型)**:Task 4 改为 **UI-only**(弹层/过滤/键盘/caret/焦点/不吞键),不向真实 LangGraph 发送;模型侧端到端归确定性 e2e fixture 世界,列为后续任务(需单独授权才碰真实模型/线程)。
  - **Minor-1(传递依赖直导)**:`@assistant-ui/core` 不在 package.json dependencies → 全部类型改从 `@assistant-ui/react` 导入(已核实重导出 `Unstable_DirectiveFormatter`/`Unstable_TriggerItem`;`Unstable_TriggerAdapter` 不在,由本地 `SkillSlashAdapter` 取代,零 core 导入)。
  - **Minor-2(原生 dispatchEvent)**:测试中 `new Event("select")` → `fireEvent.select(input, { selectionStart, selectionEnd })`。
- **R5(评审 R4 后,1 Critical + 3 Important + 1 Minor 全部采纳,均经源码复核)**:
  - **C1(中间 caret 测试确定性失败)**:①dom-testing-library `createEvent` 只把 `init.target` 下的属性 `Object.assign` 到节点,`target` 外的键进 Event 构造器被忽略——`fireEvent.select(input, { selectionStart: 9 })` 根本不落 DOM selection,必须 `fireEvent.select(input, { target: { selectionStart: 9, selectionEnd: 9 } })`;②user-event `type()` 源码 `if (!skipClick) await this.click(element)`——先点击,jsdom 无布局,click 把 caret 置于末尾,中间 caret 被冲掉。→ 定位光标后**一律 `await user.keyboard(...)` 续写**(keyboard 直接发键到 activeElement,不点击);测试中 caret 断言与续写之间保留 `waitFor` 以 flush `setTimeout(0)` 的 caret 写入。
  - **I1(reload-skills 对齐后端 strip 语义)**:`skill_reload.py:29` 用 `content.strip() == "/reload-skills"`——前导空白裸命令在后端同样触发刷新,前端 `el.value === "/reload-skills"` 精确比较会错放。→ 改 `useAuiState((s) => s.composer.text)`(reactive,store 官方范例)取全文,判 `query === "reload-skills" && composerText.trim() === "/reload-skills"`。尾随空白无需显式处理:query 含空白即不触发 detection,弹层结构上不可能出现。补前导/尾随空白两测试。
  - **I2(UI-only 浏览器步骤仍会真实发送)**:裸命令/零匹配时弹层已关,Enter 直接落入 Composer 提交,无「进消息流前清空」窗口。→ Task 4 真实栈**绝不按 Enter 发送裸文本**:零匹配/裸命令只断言无弹层,随后 **Ctrl+A + Backspace 清空**(Backspace 不发送);Enter 仅在弹层开着且有高亮项时按(此时被 preventDefault,只插入不发送);Enter 回退路径由内存 runtime 测试覆盖。
  - **I3(重复 directive 缺正式回归)**:补三项测试:同技能连续插入两次(caret 不回跳第一条指令)、中间 caret 无空白 suffix(`前文 /stock后文` → `前文 指令 后文`)、中间 caret 用 **Enter** 选中 + `user.keyboard()` 续写(即 C1 修复后的中间 caret 测试本体)。
  - **Minor(Task 3 提交遗漏)**:Task 3 补 Step 7——`tsconfig.test.json` + `package.json` 随独立 commit 提交,不留未跟踪文件。

**背景事实（执行者需要知道的，均已在本仓核实）：**
- **API 均在当前版本导出**:`ComposerPrimitive.Unstable_TriggerPopover{,Root,.Action,Items,Item}`、`unstable_useTriggerPopoverScopeContextOptional`、类型 `Unstable_DirectiveFormatter`/`Unstable_TriggerItem`(均来自 `@assistant-ui/react`;**不要**直接 import `@assistant-ui/core`——它只是传递依赖)。带 `Unstable_` 前缀,官方标注 may change;`package.json` 锁定精确版本 `0.15.16`,升级 assistant-ui 时需回归本功能。
- **选中写入路径(triggerSelectionResource.js 源码核实)**:Action + `removeOnExecute` 缺省(false)走 `insertDirective()`:**单次** `setText(before + serialize(item) + separator)`,`separator = after.startsWith(" ") ? after : " " + after`,并 `setCursorPosition(before + directive + 1)`(**仅检测状态,非 DOM**)。`before/after` 取自 select 时刻的一次 `getState().text` 快照(用户输入早已 flush,无竞态)。**不要**在 execute/onInserted 里再 `getState → setText`。
- **`parse` 不会被调用**:formatter.parse 只被 Lexical DirectivePlugin 使用;本 composer 是纯 textarea,平铺返回 `[{ kind: "text", text }]` 即可。
- **弹层渲染与键盘**:`Unstable_TriggerPopover` 打开时在 JSX 原位渲染 `<div role="listbox">`,关闭时直接渲染 children(ScopeProvider 两种分支都包裹,`scope` 恒可读)。键盘路由内置于 `ComposerPrimitive.Input`:先遍历 input plugin,plugin 返回 false 才走 Enter-发送;**Enter/Tab 零匹配也会 `preventDefault` 并返回 true**——靠「空候选不注册 behavior」让 `open=false` 兜底。Esc 走 `close()`(caret 移到 `/` 前,框架预期)。
- **触发与搜索(detectTrigger.js)**:`/` 须在文本开头或前一字符空白;query 从 `/` 到 caret、**不含空白**。搜索按 id/label/description 子串(不区分大小写),query 空返回全部;高亮索引初始 0,query 变化重置。**option 的可访问名以 `/` + label 开头**(断言正则记得转义斜杠)。
- **空候选判定必须用 `scope.query` 直查 adapter**:`scope.items` 仅在 open 后生成,而 open 依赖 behavior 注册——读 items 判空死锁(R3-C1)。`Unstable_TriggerAdapter.search` 可选——本地 `SkillSlashAdapter` 收紧为必选(R4-C1)。
- **名字唯一性**:`skillmgr.py:313` 把与内置同名的 user 技能标为 `effective=false`,前端只按 `effective` 过滤;builtin 恒 `effective=true`。
- **模型侧**:`SKILLS_SYSTEM_PROMPT` 列技能名+描述并指示 read_file;`FilteredSkillBackend` 每次 read 重新校验——指令带路径后通常可读;技能被并发停用/删除时 `read_file` 安全返回 `file_not_found`。
- **测试基建**:`@/test/agent-runtime` 的 `TestAgentRuntimeProvider`(真实 runtime + 回显模型 `回复：<文本>`);`vi.mock("@/lib/api")`。移动光标必须 `fireEvent.select(input, { target: { selectionStart, selectionEnd } })`——dom-testing-library 只把 `target` 下的键 assign 到节点;定位后续写一律 `user.keyboard()`(`user.type()` 会先 click 把 caret 冲到末尾)。trigger primitives 无 `scrollIntoView`,jsdom 安全。
- **测试类型检查**:`tsconfig.json` 排除 `src/**/*.test.{ts,tsx}` → `tsconfig.test.json`(include 仅本特性测试 + `src/test/setup.ts`,后者挂 jest-dom matcher 类型)+ `npm run typecheck:test`。全量纳入不做:既有测试类型错误 6 文件数十处,属独立任务。
- 代码/注释中文;Tailwind v4(`data-highlighted:` 变体可用);`npm run build` 跑 tsc strict。
- **范围边界**:只改工作台 Agent 页 Composer;嵌入式 Ask-AI 不动(后续可复用,届时数据源需换)。浏览器验证 UI-only,不碰真实模型/线程(R4-I5)。

---

## File Structure

| 文件 | 动作 | 职责 |
|---|---|---|
| `frontend/src/components/agent/AgentSlashSkills.tsx` | 新建 | `useSlashSkills()`(拉取+过滤+adapter)、`ComposerSlashPopover`(弹层 UI + query 判空 + 整消息级保留命令 + 焦点/caret)+ 纯函数 `buildSlashSkillItems` / `skillDirectiveText` / `skillDirectiveFormatter` |
| `frontend/src/components/agent/AgentThread.tsx` | 改 | `WorkspaceComposer` 外包 `Unstable_TriggerPopoverRoot`,挂 `ComposerSlashPopover` |
| `frontend/src/components/agent/AgentSlashSkills.test.tsx` | **重写** | 替换本轮 R1 草稿(未提交,非用户内容):纯函数单测 + 全链路集成测试 |
| `frontend/tsconfig.test.json` | 新建 | 测试类型检查(include 本特性测试 + setup.ts) |
| `frontend/package.json` | 改 | `typecheck:test` script |

---

## Task 1: 测试先行 — 重写 `AgentSlashSkills.test.tsx`

**Files:**
- Rewrite: `frontend/src/components/agent/AgentSlashSkills.test.tsx`(替换 R1 草稿)

- [ ] **Step 1: 重写测试(fixture 含真 `reload-skills` 冲突技能;textarea 泛型;fireEvent.select)**

```tsx
/** 斜杠技能命令：纯函数契约 + Composer 全链路（触发/过滤/选中/键盘/caret/降级）。 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TestAgentRuntimeProvider } from "@/test/agent-runtime";

vi.mock("@/lib/api", () => ({ api: { skills: vi.fn() } }));

import { api, type SkillsResponse } from "@/lib/api";
import { buildSlashSkillItems, skillDirectiveText } from "./AgentSlashSkills";
import { AgentThread } from "./AgentThread";

afterEach(cleanup);

const skillsResponse = (): SkillsResponse => ({
  builtin: [
    { name: "debate", description: "多空辩论框架", source: "builtin", enabled: true, valid: true, effective: true, error: null },
    { name: "stock-analysis", description: "个股客观数据分析", source: "builtin", enabled: true, valid: true, effective: true, error: null },
  ],
  user: [
    { name: "my-checklist", description: "自定义清单", source: "user", enabled: true, valid: true, effective: true, error: null },
    { name: "reload-skills", description: "同名冲突技能", source: "user", enabled: true, valid: true, effective: true, error: null },
    { name: "disabled-one", description: "已停用", source: "user", enabled: false, valid: true, effective: false, error: null },
    { name: "debate", description: "与内置同名被遮蔽", source: "user", enabled: true, valid: true, effective: false, error: "与内置技能同名，已阻止加载" },
  ],
  user_available: true,
});

describe("buildSlashSkillItems / skillDirectiveText", () => {
  it("只保留 effective 技能，内置在前用户在后，携带虚拟路径", () => {
    const items = buildSlashSkillItems(skillsResponse());
    expect(items.map((i) => i.id)).toEqual(["debate", "stock-analysis", "my-checklist", "reload-skills"]);
    expect(items[0].label).toBe("debate");
    expect(items[0].description).toBe("多空辩论框架");
    expect(items[0].metadata?.path).toBe("/builtin/debate/SKILL.md");
    expect(items[2].metadata?.path).toBe("/user/my-checklist/SKILL.md");
  });

  it("空数据 / null 安全返回空数组", () => {
    expect(buildSlashSkillItems(null)).toEqual([]);
    expect(buildSlashSkillItems({ builtin: [], user: [], user_available: false })).toEqual([]);
  });

  it("指令文本：技能名 + 虚拟路径 + 显式 read 指令", () => {
    expect(skillDirectiveText("debate", "/builtin/debate/SKILL.md"))
      .toBe("请使用技能「debate」（先 read_file /builtin/debate/SKILL.md）：");
  });
});

async function renderAgent(data: SkillsResponse = skillsResponse()) {
  vi.mocked(api.skills).mockResolvedValue(data);
  render(
    <TestAgentRuntimeProvider>
      <AgentThread />
    </TestAgentRuntimeProvider>,
  );
  await waitFor(() => expect(api.skills).toHaveBeenCalled());
  return screen.getByRole<HTMLTextAreaElement>("textbox", { name: "Agent 消息" });
}

const directiveOf = (name: string, path: string) => skillDirectiveText(name, path);

describe("AgentThread 斜杠技能弹层", () => {
  beforeEach(() => vi.clearAllMocks());

  it("输入 / 弹出技能列表，继续输入按名称过滤", async () => {
    const user = userEvent.setup();
    const input = await renderAgent();
    await user.type(input, "/");
    expect(screen.getByRole("listbox", { name: "技能命令" })).toBeVisible();
    expect(screen.getByRole("option", { name: /stock-analysis/ })).toBeInTheDocument();
    await user.type(input, "stock");
    expect(screen.queryByRole("option", { name: /^\/debate/ })).toBeNull();
    expect(screen.getByRole("option", { name: /stock-analysis/ })).toBeInTheDocument();
  });

  it("点击条目：/query 原位替换（前后文本保留），焦点留在输入框", async () => {
    const user = userEvent.setup();
    const input = await renderAgent();
    await user.type(input, "帮我 /stock");
    await user.click(screen.getByRole("option", { name: /stock-analysis/ }));
    expect(input).toHaveValue(
      `帮我 ${directiveOf("stock-analysis", "/builtin/stock-analysis/SKILL.md")} `,
    );
    expect(screen.queryByRole("listbox")).toBeNull();
    expect(document.activeElement).toBe(input);
  });

  it("Enter 选中当前高亮条目（默认第一项）", async () => {
    const user = userEvent.setup();
    const input = await renderAgent();
    await user.type(input, "/");
    await user.keyboard("{Enter}");
    expect(input).toHaveValue(`${directiveOf("debate", "/builtin/debate/SKILL.md")} `);
  });

  it("ArrowDown/ArrowUp 移动高亮后 Enter 选中第二项", async () => {
    const user = userEvent.setup();
    const input = await renderAgent();
    await user.type(input, "/");
    await user.keyboard("{ArrowDown}{ArrowDown}{ArrowUp}{Enter}");
    expect(input).toHaveValue(
      `${directiveOf("stock-analysis", "/builtin/stock-analysis/SKILL.md")} `,
    );
  });

  it("选中后 caret 落在指令后第一个空白之后，续写再发送：断言最终发送文本", async () => {
    const user = userEvent.setup();
    const input = await renderAgent();
    await user.type(input, "/");
    await user.keyboard("{Enter}");
    const directive = directiveOf("debate", "/builtin/debate/SKILL.md");
    await waitFor(() => {
      expect(input.selectionStart).toBe(directive.length + 1);
    });
    await user.type(input, "对比 600519 多空");
    await user.click(screen.getByRole("button", { name: "发送", hidden: true }));
    await waitFor(() => {
      expect(screen.getByText(`回复：${directive} 对比 600519 多空`)).toBeVisible();
    });
  });

  it("中间光标：前文 /query 后文，Enter 选中后替换/caret/续写落点全部正确", async () => {
    const user = userEvent.setup();
    const input = await renderAgent();
    await user.type(input, "前文 /stock 后文");
    // selection 必须包在 target 下；user.type 会先 click 冲掉中间 caret，续写用 keyboard
    fireEvent.select(input, { target: { selectionStart: 9, selectionEnd: 9 } });
    await waitFor(() => {
      expect(screen.getByRole("option", { name: /stock-analysis/ })).toBeInTheDocument();
    });
    await user.keyboard("{Enter}");
    const directive = directiveOf("stock-analysis", "/builtin/stock-analysis/SKILL.md");
    await waitFor(() => {
      expect(input).toHaveValue(`前文 ${directive} 后文`);
      expect(input.selectionStart).toBe("前文 ".length + directive.length + 1);
    });
    await user.keyboard("问题");
    await user.click(screen.getByRole("button", { name: "发送", hidden: true }));
    await waitFor(() => {
      expect(screen.getByText(`回复：前文 ${directive} 问题后文`)).toBeVisible();
    });
  });

  it("同技能连续插入两次：第二条原位追加，caret 不回跳第一条指令", async () => {
    const user = userEvent.setup();
    const input = await renderAgent();
    await user.type(input, "/debate{Enter}");
    const directive = directiveOf("debate", "/builtin/debate/SKILL.md");
    await waitFor(() => {
      expect(input).toHaveValue(`${directive} `);
      expect(input.selectionStart).toBe(directive.length + 1);
    });
    await user.type(input, "/debate{Enter}");
    await waitFor(() => {
      expect(input).toHaveValue(`${directive} ${directive} `);
      expect(input.selectionStart).toBe(directive.length + 1 + directive.length + 1);
    });
    await user.click(screen.getByRole("button", { name: "发送", hidden: true }));
    await waitFor(() => {
      // 发送时尾随空白被 trim，只断言到第二条指令结尾
      expect(screen.getByText(`回复：${directive} ${directive}`)).toBeVisible();
    });
  });

  it("中间光标无空白 suffix：后文紧跟 query，替换后指令与后文之间补一个空格", async () => {
    const user = userEvent.setup();
    const input = await renderAgent();
    await user.type(input, "前文 /stock后文");
    fireEvent.select(input, { target: { selectionStart: 9, selectionEnd: 9 } });
    await waitFor(() => {
      expect(screen.getByRole("option", { name: /stock-analysis/ })).toBeInTheDocument();
    });
    await user.keyboard("{Enter}");
    const directive = directiveOf("stock-analysis", "/builtin/stock-analysis/SKILL.md");
    await waitFor(() => {
      expect(input).toHaveValue(`前文 ${directive} 后文`);
      expect(input.selectionStart).toBe("前文 ".length + directive.length + 1);
    });
  });

  it("Esc 关闭弹层，随后 Enter 正常发送", async () => {
    const user = userEvent.setup();
    const input = await renderAgent();
    await user.type(input, "/stock");
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("listbox")).toBeNull();
    await user.keyboard("{Enter}");
    await waitFor(() => {
      expect(screen.getByText("回复：/stock")).toBeVisible();
    });
  });

  it("零匹配：弹层不出现，Enter 直接发送（含输入+回车最小间隙的单次调用回归）", async () => {
    const user = userEvent.setup();
    const input = await renderAgent();
    await user.type(input, "/zzz{Enter}");
    expect(screen.queryByRole("listbox")).toBeNull();
    await waitFor(() => {
      expect(screen.getByText("回复：/zzz")).toBeVisible();
    });
  });

  it("裸命令 /reload-skills 精确保留：composer 全文恰为命令时不弹层（存在同名技能）", async () => {
    const user = userEvent.setup();
    const input = await renderAgent();
    await user.type(input, "/reload-skills");
    expect(screen.queryByRole("listbox")).toBeNull();
    await user.keyboard("{Enter}");
    await waitFor(() => {
      expect(screen.getByText("回复：/reload-skills")).toBeVisible();
    });
  });

  it("前缀 /reload-skill 命中同名技能：弹层照常出现（保留仅限整消息精确）", async () => {
    const user = userEvent.setup();
    const input = await renderAgent();
    await user.type(input, "/reload-skill");
    expect(screen.getByRole("option", { name: /reload-skills/ })).toBeInTheDocument();
  });

  it("有前文时 /reload-skills 照常弹层（后端仅整消息精确匹配，前文场景应可选技能）", async () => {
    const user = userEvent.setup();
    const input = await renderAgent();
    await user.type(input, "请 /reload-skills");
    expect(screen.getByRole("option", { name: /reload-skills/ })).toBeInTheDocument();
  });

  it("前导空白裸命令：整消息 trim 后恰为命令，保留不弹层、Enter 直发原文", async () => {
    const user = userEvent.setup();
    const input = await renderAgent();
    await user.type(input, "  /reload-skills");
    await waitFor(() => {
      expect(screen.queryByRole("listbox")).toBeNull();
    });
    await user.keyboard("{Enter}");
    await waitFor(() => {
      // 回显保留前导空格（getByText 归一化只折叠不删除），用正则容忍空白
      expect(screen.getByText(/回复：\s+\/reload-skills/)).toBeVisible();
    });
  });

  it("尾随空白裸命令：query 含空白即不触发弹层，Enter 直发原文", async () => {
    const user = userEvent.setup();
    const input = await renderAgent();
    await user.type(input, "/reload-skills  ");
    expect(screen.queryByRole("listbox")).toBeNull();
    await user.keyboard("{Enter}");
    await waitFor(() => {
      expect(screen.getByText("回复：/reload-skills")).toBeVisible();
    });
  });

  it("技能接口失败：输入 / 不弹层、不崩溃", async () => {
    vi.mocked(api.skills).mockRejectedValue(new Error("网络错误"));
    const user = userEvent.setup();
    render(
      <TestAgentRuntimeProvider>
        <AgentThread />
      </TestAgentRuntimeProvider>,
    );
    const input = screen.getByRole<HTMLTextAreaElement>("textbox", { name: "Agent 消息" });
    await user.type(input, "/");
    expect(screen.queryByRole("listbox")).toBeNull();
    expect(input).toHaveValue("/");
  });

  it("无可用技能：输入 / 不弹层", async () => {
    const input = await renderAgent({ builtin: [], user: [], user_available: true });
    const user = userEvent.setup();
    await user.type(input, "/");
    expect(screen.queryByRole("listbox")).toBeNull();
  });
});
```

caret 断言规则(与实现对齐):`target = 原caret − (query.length + 1) + directive.length + 1`——末尾场景原caret = 1+query.length(query 在文本头),target = directive.length + 1;中间场景 target = `"前文 ".length + directive.length + 1`。若实测有出入,先重读 `triggerSelectionResource.js` 的 `insertDirective` 再修,不得盲改。

- [ ] **Step 2: 确认失败**

Run: `cd frontend && npx vitest run src/components/agent/AgentSlashSkills.test.tsx` → FAIL(模块不存在)

---

## Task 2: 实现 `AgentSlashSkills.tsx` + WorkspaceComposer 接线

**Files:**
- Create: `frontend/src/components/agent/AgentSlashSkills.tsx`
- Modify: `frontend/src/components/agent/AgentThread.tsx`

- [ ] **Step 1: 新建 `AgentSlashSkills.tsx`**

```tsx
/** Composer 斜杠技能命令：/ 唤出技能列表，选中后原位注入模型可读的指令文本。
 *
 * 数据源 FastAPI /api/skills（只取 effective——与内置同名的 user 技能后端已标
 * effective=false，名字在 agent 复合视图内唯一）。写入走 Action 行为 + 自定义
 * DirectiveFormatter：框架在 selectItem 内一次性 setText(before + 指令 + 分隔空格)，
 * 单次写入、无读回——store 的 getState() 返回上次刷出的快照，execute 回调里
 * 读文本会拿到替换前的旧值，禁止再走该路径。
 *
 * 空候选或整消息级保留命令时不挂 Action（behavior 是弹层 open 的必要条件），
 * Enter 回退 Composer 自带发送——裸命令与含「 /词」的普通消息不被吞键。 */
import { useEffect, useMemo, useState } from "react";
import {
  ComposerPrimitive,
  unstable_useTriggerPopoverScopeContextOptional,
  useAuiState,
  type Unstable_DirectiveFormatter,
  type Unstable_TriggerItem,
} from "@assistant-ui/react";

import { api, type SkillsResponse } from "@/lib/api";

/** 本地 adapter 契约：search 收紧为必选（Unstable_TriggerAdapter 里是可选方法，
 * 直调会 TS2722）；categories/categoryItems 以 never[] 满足结构兼容，无需
 * import 传递依赖 @assistant-ui/core。 */
export type SkillSlashAdapter = {
  categories(): readonly never[];
  categoryItems(categoryId: string): readonly Unstable_TriggerItem[];
  search(query: string): readonly Unstable_TriggerItem[];
};

/** effective 技能 → 弹层条目（内置在前用户在后，保持服务端各自排序）。 */
export function buildSlashSkillItems(
  data: SkillsResponse | null,
): Unstable_TriggerItem[] {
  if (!data) return [];
  const effective = (list: SkillsResponse["builtin"]) =>
    list.filter((s) => s.effective);
  return [...effective(data.builtin), ...effective(data.user)].map((s) => ({
    id: s.name,
    type: "skill",
    label: s.name,
    description: s.description ?? "",
    metadata: { path: `${s.source === "builtin" ? "/builtin" : "/user"}/${s.name}/SKILL.md` },
  }));
}

/** 选中技能后原位写入 Composer 的指令文本（带虚拟路径：read_file 通常不依赖
 * 线程 skills_metadata 缓存即可读到；技能被并发停用/删除时后端安全返回
 * file_not_found）。 */
export function skillDirectiveText(name: string, path: string): string {
  return `请使用技能「${name}」（先 read_file ${path}）：`;
}

const directiveOf = (item: Unstable_TriggerItem): string =>
  skillDirectiveText(item.id, String(item.metadata?.path ?? ""));

/** serialize 是 Action 选中路径的唯一写入来源；parse 只被 Lexical 芯片插件
 * 使用（本 composer 为纯 textarea），平铺返回即可。 */
export const skillDirectiveFormatter: Unstable_DirectiveFormatter = {
  serialize: directiveOf,
  parse: (text) => [{ kind: "text", text }],
};

/** 拉取技能并构建 adapter；无可用技能时返回 null（完全不挂弹层）。 */
export function useSlashSkills(): SkillSlashAdapter | null {
  const [data, setData] = useState<SkillsResponse | null>(null);

  useEffect(() => {
    let alive = true;
    api.skills()
      .then((d) => alive && setData(d))
      .catch(() => {}); // 降级：无弹层，正常打字
    return () => {
      alive = false;
    };
  }, []);

  const items = useMemo(() => buildSlashSkillItems(data), [data]);
  const adapter = useMemo<SkillSlashAdapter>(
    () => ({
      categories: () => [],
      categoryItems: () => [],
      search: (query) => {
        const lower = query.toLowerCase();
        if (!lower) return items;
        return items.filter(
          (i) =>
            i.id.toLowerCase().includes(lower) ||
            i.label.toLowerCase().includes(lower) ||
            (i.description ?? "").toLowerCase().includes(lower),
        );
      },
    }),
    [items],
  );
  return items.length === 0 ? null : adapter;
}

/** 后端保留命令（skill_reload.py 用 content.strip() 整消息匹配 /reload-skills）：
 * 仅当 composer 全文 trim 后恰为该命令时不弹层——与后端 is_reload_command 语义
 * 对齐（前导空白裸命令后端同样会触发刷新）；有前文/后文时同名技能照常可选
 * （那时发送的是普通消息，不会触发刷新）。尾随空白无需处理：query 含空白
 * 本就不触发 detection。 */
function isReservedCommand(query: string, composerText: string): boolean {
  return query === "reload-skills" && composerText.trim() === "/reload-skills";
}

/** 框架的 setCursorPosition 只更新检测状态、不动 DOM selection——不补这步，
 * caret 停在原 /query 长度处，续写会插进指令中间。onExecute 同步段先记下
 * 原 caret（此时框架 setText 尚未重渲染，DOM 仍是旧值旧光标），换算出指令后
 * 第一个空白之后的位置，等重渲染完成（setTimeout 0）再写入。不做全文
 * indexOf(directive)——同一技能二次插入会跳回第一条指令。 */
function placeCaretAfterDirective(item: Unstable_TriggerItem, query: string): void {
  const el = document.activeElement;
  if (!(el instanceof HTMLTextAreaElement)) return;
  const original = el.selectionStart;
  if (original < query.length + 1) return;
  const directive = directiveOf(item);
  const target = original - (query.length + 1) + directive.length + 1;
  window.setTimeout(() => {
    if (document.activeElement === el) {
      el.setSelectionRange(target, target);
    }
  }, 0);
}

/** 空候选/保留命令时不注册 behavior：弹层关闭、Enter 回退正常发送
 * （triggerKeyboardResource 对开着的弹层会 preventDefault 并吞 Enter）。
 * 判空必须用 scope.query 直查 adapter——scope.items 仅在 open 后生成，而
 * open 又依赖本 behavior 注册，读 items 判空是死锁。 */
function SkillPopoverBehavior({ adapter }: { adapter: SkillSlashAdapter }) {
  const scope = unstable_useTriggerPopoverScopeContextOptional();
  // reactive 全文（store 官方范例选择器）：保留命令按整消息 trim 判定，
  // 与后端 content.strip() 语义一致——不能读 DOM 快照，前导空白会漏判
  const composerText = useAuiState((s) => s.composer.text);
  if (!scope) return null;
  const query = scope.query.trim();
  if (isReservedCommand(query, composerText) || adapter.search(query).length === 0) {
    return null;
  }
  return (
    <ComposerPrimitive.Unstable_TriggerPopover.Action
      formatter={skillDirectiveFormatter}
      onExecute={(item) => placeCaretAfterDirective(item, query)}
    />
  );
}

/** 挂在 ComposerPrimitive.Root 内、输入框上方的技能弹层（glass 风格）。 */
export function ComposerSlashPopover({
  adapter,
}: {
  adapter: SkillSlashAdapter;
}) {
  return (
    <ComposerPrimitive.Unstable_TriggerPopover
      char="/"
      adapter={adapter}
      aria-label="技能命令"
      className="bg-card text-card-foreground border-border/70 absolute bottom-full left-0 right-0 z-50 mb-2 max-h-64 overflow-y-auto rounded-xl border shadow-lg"
    >
      <SkillPopoverBehavior adapter={adapter} />
      <ComposerPrimitive.Unstable_TriggerPopoverItems aria-label="技能列表">
        {(items) =>
          items.map((item, i) => (
            <ComposerPrimitive.Unstable_TriggerPopoverItem
              key={item.id}
              item={item}
              index={i}
              onMouseDown={(e) => e.preventDefault()} // 阻止焦点离开输入框（combobox 惯例）
              className="data-highlighted:bg-accent data-highlighted:text-accent-foreground flex w-full cursor-pointer flex-col items-start gap-0.5 rounded-lg px-3 py-2 text-left text-sm outline-none select-none"
            >
              <span className="text-primary font-medium">/{item.label}</span>
              {item.description ? (
                <span className="text-muted-foreground line-clamp-2 text-xs">
                  {item.description}
                </span>
              ) : null}
            </ComposerPrimitive.Unstable_TriggerPopoverItem>
          ))
        }
      </ComposerPrimitive.Unstable_TriggerPopoverItems>
    </ComposerPrimitive.Unstable_TriggerPopover>
  );
}
```

- [ ] **Step 2: 改 `AgentThread.tsx` 的 `WorkspaceComposer`**

外层包 `Unstable_TriggerPopoverRoot`,`ComposerPrimitive.Root` 内(shell 之后)挂弹层。输入禁用(运行中/待审批)时 textarea 不可输入,弹层自然无法触发:

```tsx
function WorkspaceComposer({ approvalPending }: { approvalPending: boolean }) {
  const isRunning = useAuiState((s) => s.thread.isRunning);
  const locked = isRunning || approvalPending;
  const slashAdapter = useSlashSkills();
  return (
    <ComposerPrimitive.Unstable_TriggerPopoverRoot>
      <ComposerPrimitive.Root className="aui-composer-root relative flex w-full flex-col">
        <div data-slot="aui_composer-shell" className={/* 原样不动 */}>
          {/* 原内容原样不动：approvalPending 提示 / Input / 按钮区 */}
        </div>
        {slashAdapter ? <ComposerSlashPopover adapter={slashAdapter} /> : null}
      </ComposerPrimitive.Root>
    </ComposerPrimitive.Unstable_TriggerPopoverRoot>
  );
}
```

- [ ] **Step 3: 确认 Task 1 测试通过**

Run: `cd frontend && npx vitest run src/components/agent/AgentSlashSkills.test.tsx src/components/agent/AgentThread.test.tsx` → PASS

行为断言若与框架细节有出入,先重读 `triggerSelectionResource.js` 的 `insertDirective` 再修断言,不得盲改;零匹配 `{Enter}` 单次调用若偶发吞键,升级为同步失效方案并记录。

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/agent/AgentSlashSkills.tsx \
        frontend/src/components/agent/AgentSlashSkills.test.tsx \
        frontend/src/components/agent/AgentThread.tsx
git commit -m "feat(agent): composer slash command picker for skills"
```

---

## Task 3: 测试类型检查 + 回归

- [ ] **Step 1:** 新建 `frontend/tsconfig.test.json`:

```json
{
  "extends": "./tsconfig.json",
  "include": ["src/components/agent/AgentSlashSkills.test.tsx", "src/test/setup.ts"],
  "exclude": []
}
```

`package.json` scripts 加 `"typecheck:test": "tsc -p tsconfig.test.json --noEmit"`。include 刻意收窄:全量纳入会暴露既有测试类型错误 6 文件数十处,属独立任务。

- [ ] **Step 2:** `cd frontend && npm run typecheck:test` → PASS
- [ ] **Step 3:** `npm run test:unit` → 全 PASS(既有测试不受影响)
- [ ] **Step 4:** `npm test`(node 契约测试)→ PASS
- [ ] **Step 5:** `npm run build`(tsc strict + vite)→ PASS
- [ ] **Step 6:** 后端零改动;如不放心跑 `cd backend && .venv/bin/pytest -m "not live" -q`
- [ ] **Step 7:** 提交测试类型检查配置(不留未跟踪文件):

```bash
git add frontend/tsconfig.test.json frontend/package.json
git commit -m "test(frontend): 窄范围测试 typecheck 配置（AgentSlashSkills）"
```

---

## Task 4: 浏览器人工验证(UI-only,不碰真实模型/线程)

- [ ] **Step 1:** 确认 dev 栈在跑(`curl 127.0.0.1:8900/api/health`、`curl 127.0.0.1:5899/src/index.css` 返回 CSS;stale 则自起备用端口,见 AGENTS.md)。
- [ ] **Step 2:** 探测 CDP `curl -m 3 127.0.0.1:16002/json/version`,按 AGENTS.md 优先 CDP、退 headless chromium;打开 `/agent`,**只做 UI 交互、绝不发送**:
  - **纪律(R5-I2):弹层关闭状态下对裸文本按 Enter = 真实发送(无拦截窗口)。Enter 只在弹层开着且有高亮项时按(被 preventDefault,只插入不发送);清空输入框一律 Ctrl+A + Backspace。Enter 回退路径由内存 runtime 测试覆盖,不在真实栈验证。**
  - 输入 `/`:弹层浮于输入框上方、glass 风格、技能名 + 描述;
  - `/stock` 过滤;**↑ 与 ↓ 高亮随动**;Enter 选中 → 指令原位替换(随后 Ctrl+A + Backspace 清空,不发送);
  - **鼠标点选后直接继续打字(不失焦、caret 在指令后)**,再清空;
  - 中间光标:`前文 /stock 后文`(caret 移到 /stock 后)选中 → `前文 指令 后文`,再清空;
  - `/zzz` 零匹配、`/reload-skills` 裸命令:断言**无弹层**(不按 Enter),Ctrl+A + Backspace 清空收尾。
- [ ] **Step 3:** 截图存 `/tmp`,结论写进收尾汇报。模型侧端到端(read_file 技能、回复质量)归确定性 e2e fixture 世界,列为后续任务;真实模型验证需单独授权。

---

## 收尾

- 全部 Task 完成后汇报:改动文件、测试结果、浏览器验证截图路径、遗留事项(unstable API 升级回归;Ask-AI 嵌入式未接入;测试类型检查 include 收窄待全量;技能列表 focus 重拉未做;模型侧端到端 e2e 待做)。
- 分支保留在本地 `feat/agent-slash-skills`,是否推远端/开 PR 由用户决定。
