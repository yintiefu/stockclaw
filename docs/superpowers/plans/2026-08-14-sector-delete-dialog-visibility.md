# 板块成分股删除弹窗关闭失效 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让来源成分股删除确认弹窗的取消与确认操作都能关闭弹窗，同时保留原有删除行为。

**Architecture:** 保留 React state 驱动原生 `<dialog>` 的实现，只修正 Tailwind display utility 的状态范围。用源码级回归检查防止常驻 `flex` 再次覆盖原生关闭态，再以构建和 CDP 覆盖实际浏览器行为。

**Tech Stack:** React 19、TypeScript、Tailwind CSS、Node test runner、Chrome CDP/Puppeteer。

---

### Task 1: 锁定关闭态显示回归

**Files:**
- Create: `frontend/tests/sector-delete-dialog.test.mjs`
- Modify: `frontend/src/pages/SectorDetail.tsx`

- [ ] **Step 1: 写失败测试**

读取生产组件源码，定位删除 dialog，断言 class 使用 `open:flex` 且不存在无状态 `flex`。

- [ ] **Step 2: 验证测试在旧实现失败**

Run: `cd frontend && node --test tests/sector-delete-dialog.test.mjs`

Expected: FAIL，指出删除 dialog 含常驻 `flex`。

- [ ] **Step 3: 最小修复**

将删除 dialog class 中的 `flex` 替换为 `open:flex`，其余逻辑不变。

- [ ] **Step 4: 验证自动化检查**

Run: `cd frontend && npm test && npm run build`

Expected: 全部测试通过，TypeScript 与 Vite 构建成功。

- [ ] **Step 5: CDP 浏览器验收**

在 `http://127.0.0.1:5899/sectors/ai-computing` 验证：取消后 `open=false` 且 `display:none`；确认时拦截 `/api/sectors/stocks/delete` POST 请求，断言只发一次、请求体正确且 dialog 关闭，避免改动真实数据。
