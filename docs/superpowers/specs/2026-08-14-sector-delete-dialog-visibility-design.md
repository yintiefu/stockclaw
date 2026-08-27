# 板块成分股删除弹窗关闭失效设计

- 日期：2026-08-14
- 范围：`frontend/src/pages/SectorDetail.tsx` 的来源成分股删除确认弹窗
- 状态：已确认

## 问题与根因

删除弹窗的原生 `<dialog>` 在取消或确认后会正确移除 `open` 属性，但元素 class 中常驻的 Tailwind `flex` 会覆盖浏览器默认的 `dialog:not([open]) { display: none }`。因此关闭后的 dialog 仍以全屏 flex 容器显示，并继续拦截点击，表现为取消和确认均无反应。

CDP 复现证据：点击取消后 `open=false`、`:open=false`，但计算样式仍为 `display:flex`，弹窗按钮仍有可见布局框。

## 设计

只把 dialog 的常驻 `flex` 改为 `open:flex`。打开时布局不变；关闭时不再生成覆盖浏览器默认隐藏规则的 `display:flex`。保留现有 React state、原生 `showModal/close`、Esc、遮罩点击和删除请求逻辑。

不改为自定义遮罩或条件渲染，因为这会扩大行为和可访问性变更范围；也不增加额外全局 CSS，因为问题只属于这一处 class。

## 验收

1. 自动化回归检查锁定 dialog 只能在 `open` 状态使用 flex，并先在旧实现上失败。
2. 前端测试与 TypeScript/Vite 构建通过。
3. CDP 实测取消后 dialog 不可见、不拦截点击。
4. CDP 拦截删除 API 后实测确认按钮发起一次正确的 POST 请求，dialog 同时关闭，且不修改真实用户数据。
