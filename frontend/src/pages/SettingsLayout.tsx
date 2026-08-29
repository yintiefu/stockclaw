/** 设置中心布局：左侧分区子导航（模型设置 / 技能管理），右侧渲染当前分区内容。 */
import { NavLink, Outlet } from "react-router-dom";
import { SlidersHorizontal, Sparkles } from "lucide-react";

const SECTIONS = [
  { to: "/settings/model", icon: SlidersHorizontal, label: "模型设置" },
  { to: "/settings/skills", icon: Sparkles, label: "技能管理" },
];

export function SettingsLayout() {
  return (
    <div className="flex flex-col gap-6 md:flex-row md:gap-8">
      <aside className="md:w-48 md:shrink-0">
        <div className="mb-3 text-lg font-bold text-foreground">设置</div>
        <nav aria-label="设置分区" className="flex flex-row gap-1 overflow-x-auto md:flex-col">
          {SECTIONS.map(({ to, icon: Icon, label }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) => {
                const base = "flex items-center gap-2 whitespace-nowrap rounded-lg px-3 py-2 text-sm transition-colors";
                const idle = "text-muted-foreground hover:bg-muted/60 hover:text-foreground";
                const active = "bg-primary/10 font-medium text-primary";
                return `${base} ${isActive ? active : idle}`;
              }}
            >
              <Icon className="h-4 w-4 shrink-0" aria-hidden="true" />
              {label}
            </NavLink>
          ))}
        </nav>
      </aside>
      <div className="min-w-0 flex-1">
        <Outlet />
      </div>
    </div>
  );
}
