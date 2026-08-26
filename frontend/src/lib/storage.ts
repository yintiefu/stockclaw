// 安全的 localStorage 读写。
//
// 浏览器在隐私模式、嵌入式 WebView、禁用 cookie、或配额写满时，访问 localStorage
// 会**直接抛异常**（不是返回 null）。如果这个异常发生在组件初始化里，整个页面会白屏——
// 侧栏折叠状态、主题这类外壳级设置尤其致命：一崩就是全站打不开。
//
// 这里统一兜底：存不下就算了，本次会话内功能照常，只是关掉页面后不被记住。

export function storageGet(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function storageSet(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* 存储不可用：本次会话仍可正常使用，只是不持久化 */
  }
}

export function storageRemove(key: string): void {
  try {
    localStorage.removeItem(key);
  } catch {
    /* 同上 */
  }
}

export interface WorkflowStreamCursor {
  runId: string;
  eventId: string;
  lastSeq: number;
}

const workflowStreamKey = (threadId: string) => `vr-workflow-stream:${threadId}`;

export function loadWorkflowStreamCursor(threadId: string): WorkflowStreamCursor | null {
  const raw = storageGet(workflowStreamKey(threadId));
  if (raw === null) return null;
  try {
    const value = JSON.parse(raw) as unknown;
    if (typeof value !== "object" || value === null || Array.isArray(value)) return null;
    const cursor = value as Record<string, unknown>;
    if (Object.keys(cursor).sort().join(",") !== "eventId,lastSeq,runId"
      || typeof cursor.runId !== "string" || cursor.runId.length === 0
      || typeof cursor.eventId !== "string" || cursor.eventId.length === 0
      || !Number.isInteger(cursor.lastSeq) || Number(cursor.lastSeq) < 0) {
      return null;
    }
    return cursor as unknown as WorkflowStreamCursor;
  } catch {
    return null;
  }
}

export function saveWorkflowStreamCursor(threadId: string, cursor: WorkflowStreamCursor): void {
  storageSet(workflowStreamKey(threadId), JSON.stringify({
    runId: cursor.runId,
    eventId: cursor.eventId,
    lastSeq: cursor.lastSeq,
  }));
}

export function clearWorkflowStreamCursor(threadId: string): void {
  storageRemove(workflowStreamKey(threadId));
}
