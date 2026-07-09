import { Link } from "react-router-dom";
import { AlertCircle } from "lucide-react";

export function CliBlocker() {
  return (
    <div className="flex h-[calc(100vh-3rem)] items-center justify-center p-6">
      <div className="glass max-w-md rounded-2xl p-6 text-center">
        <AlertCircle className="mx-auto mb-3 h-10 w-10 text-amber-500" />
        <h2 className="text-lg font-bold">需要 API 接入的模型</h2>
        <p className="mt-2 text-sm text-muted-foreground">
          Agent 工作台需要 Function-Calling 与流式多轮 Agent 路由，订阅接入（CLI 模式）不支持。
          请前往「接入 AI」页配置 API Key 或更换为 API 接入模型。
        </p>
        <Link
          to="/settings"
          className="mt-4 inline-block rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground"
        >
          前往「接入 AI」
        </Link>
      </div>
    </div>
  );
}
