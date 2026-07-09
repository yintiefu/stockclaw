export function ContextDrawer({ onClose }: { onClose: () => void }) {
  return (
    <aside className="glass w-72 rounded-2xl p-3 text-sm text-muted-foreground">
      <div className="flex items-center justify-between">
        <span className="font-medium">上下文</span>
        <button onClick={onClose} className="text-xs">收起</button>
      </div>
      <p className="mt-2 text-xs">Task 12 完整实现（股票快卡 + 收藏决策卡）</p>
    </aside>
  );
}
