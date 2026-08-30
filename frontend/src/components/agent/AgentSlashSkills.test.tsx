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

  it("点击弹层与输入框之外的页面区域收起弹层，输入文本保留", async () => {
    const user = userEvent.setup();
    const input = await renderAgent();
    await user.type(input, "/");
    expect(screen.getByRole("listbox", { name: "技能命令" })).toBeVisible();
    // 线程视口在 composer root 之外，视为弹层外
    const viewport = document.querySelector('[data-slot="aui_thread-viewport"]');
    expect(viewport).not.toBeNull();
    fireEvent.pointerDown(viewport!);
    await waitFor(() => {
      expect(screen.queryByRole("listbox")).toBeNull();
    });
    expect(input).toHaveValue("/");
  });

  it("点击输入框本身不收起弹层", async () => {
    const user = userEvent.setup();
    const input = await renderAgent();
    await user.type(input, "/");
    expect(screen.getByRole("listbox", { name: "技能命令" })).toBeVisible();
    fireEvent.pointerDown(input);
    expect(screen.getByRole("listbox", { name: "技能命令" })).toBeVisible();
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
