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
