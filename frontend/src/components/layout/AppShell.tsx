import { Link, Outlet, useLocation } from "react-router-dom";
import { LineChart, Moon, Sun, Github } from "lucide-react";
import { useDarkMode } from "@/hooks/useDarkMode";
import { cn } from "@/lib/utils";

const REPO_URL = "https://github.com/simonlin1212/Vibe-Research";

function TabLink({ to, active, children }: { to: string; active: boolean; children: React.ReactNode }) {
  return (
    <Link
      to={to}
      className={cn(
        "rounded-lg px-4 py-1.5 text-sm font-medium transition-colors",
        active
          ? "bg-primary/15 text-primary shadow-glow"
          : "text-muted-foreground hover:bg-muted/50 hover:text-foreground",
      )}
    >
      {children}
    </Link>
  );
}

export function AppShell() {
  const { pathname } = useLocation();
  const { dark, toggle } = useDarkMode();
  const isAgent = pathname.startsWith("/agent");

  return (
    <div className="flex h-screen flex-col">
      <header className="glass z-20 flex h-12 shrink-0 items-center gap-4 rounded-none border-b border-border/50 px-4">
        {/* 左：品牌 */}
        <Link to="/daily-review" className="flex items-center gap-2">
          <LineChart className="h-5 w-5 shrink-0 text-primary text-glow" />
          <span className="text-base font-extrabold tracking-tight">
            Vibe-<span className="text-primary">Research</span>
          </span>
        </Link>

        {/* 中：Tabs */}
        <nav className="flex gap-1">
          <TabLink to="/daily-review" active={!isAgent}>个人</TabLink>
          <TabLink to="/agent" active={isAgent}>股神</TabLink>
        </nav>

        {/* 右：工具 */}
        <div className="ml-auto flex items-center gap-1">
          <button
            onClick={toggle}
            className="rounded p-1.5 text-muted-foreground transition-colors hover:text-foreground"
            title={dark ? "切换亮色" : "切换暗色"}
          >
            {dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
          </button>
          <a
            href={REPO_URL}
            target="_blank"
            rel="noreferrer"
            className="rounded p-1.5 text-muted-foreground transition-colors hover:text-foreground"
            title="GitHub"
          >
            <Github className="h-4 w-4" />
          </a>
        </div>
      </header>

      {/* 主区：占满剩余高度，子 layout 自己决定 sidebar/fullscreen */}
      <div className="flex-1 overflow-hidden">
        <Outlet />
      </div>
    </div>
  );
}
