/** Task 8：导入对话框——文件夹/ZIP 分段、本地隐私说明与精确 payload（fake FileReader）。 */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";
import { SkillImportDialog } from "./SkillImportDialog";

vi.mock("@/lib/api", () => ({
  api: { importSkill: vi.fn() },
}));

const mocked = vi.mocked(api);

class FakeFileReader {
  onload: (() => void) | null = null;
  result: string | null = null;
  readAsDataURL(file: { name: string }): void {
    const encoded = btoa(`content-of-${file.name}`);
    this.result = `data:application/octet-stream;base64,${encoded}`;
    this.onload?.();
  }
}

function makeFile(name: string, relativePath: string): File {
  const file = new File(["x"], name, { type: "text/markdown" });
  Object.defineProperty(file, "webkitRelativePath", { value: relativePath });
  return file;
}

function renderDialog(onImported = vi.fn()) {
  return {
    onImported,
    ...render(
      <SkillImportDialog open onOpenChange={vi.fn()} onImported={onImported} />,
    ),
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal("FileReader", FakeFileReader);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("SkillImportDialog", () => {
  it("展示本地隐私边界说明", () => {
    renderDialog();
    expect(screen.getByText(/保留在本地/)).toBeInTheDocument();
    expect(screen.getByText(/发送到你所配置的模型/)).toBeInTheDocument();
    expect(screen.queryByText(/Git|marketplace|市场/i)).toBeNull();
  });

  it("文件夹模式用 webkitRelativePath 构造 payload", async () => {
    const user = userEvent.setup();
    const { onImported } = renderDialog(onImportedCapture());
    const input = screen.getByLabelText(/选择技能文件夹/) as HTMLInputElement;
    await user.upload(input, [
      makeFile("SKILL.md", "sample/SKILL.md"),
      makeFile("asset.bin", "sample/assets/asset.bin"),
    ]);
    await user.click(screen.getByRole("button", { name: /导入/ }));
    await waitFor(() => expect(mocked.importSkill).toHaveBeenCalled());
    const payload = mocked.importSkill.mock.calls[0][0];
    expect(payload.kind).toBe("folder");
    if (payload.kind === "folder") {
      expect(payload.files.map((file) => file.path)).toEqual([
        "sample/SKILL.md", "sample/assets/asset.bin",
      ]);
      expect(payload.files[0].content_b64).toMatch(/^data:application\/octet-stream;base64,/);
    }
    expect(onImported).toHaveBeenCalled();
  });

  it("ZIP 模式提交 filename 与 data URL", async () => {
    const user = userEvent.setup();
    renderDialog();
    await user.click(screen.getByRole("button", { name: /ZIP/ }));
    const input = screen.getByLabelText(/选择 ZIP 文件/) as HTMLInputElement;
    await user.upload(input, [makeFile("skill.zip", "skill.zip")]);
    await user.click(screen.getByRole("button", { name: /导入/ }));
    await waitFor(() => expect(mocked.importSkill).toHaveBeenCalled());
    const payload = mocked.importSkill.mock.calls[0][0];
    expect(payload.kind).toBe("zip");
    if (payload.kind === "zip") {
      expect(payload.filename).toBe("skill.zip");
      expect(payload.content_b64).toMatch(/^data:application\/octet-stream;base64,/);
    }
  });

  it("导入请求期间按钮禁用", async () => {
    const user = userEvent.setup();
    mocked.importSkill.mockReturnValue(new Promise(() => {}));
    renderDialog();
    const input = screen.getByLabelText(/选择技能文件夹/) as HTMLInputElement;
    await user.upload(input, [makeFile("SKILL.md", "sample/SKILL.md")]);
    const submit = screen.getByRole("button", { name: /导入/ });
    await user.click(submit);
    await waitFor(() => expect(submit).toBeDisabled());
  });

  it("失败保留已选文件并显示错误", async () => {
    const user = userEvent.setup();
    mocked.importSkill.mockRejectedValue(new Error("同名技能已存在"));
    const { onImported } = renderDialog(onImportedCapture());
    const input = screen.getByLabelText(/选择技能文件夹/) as HTMLInputElement;
    const files = [makeFile("SKILL.md", "sample/SKILL.md")];
    await user.upload(input, files);
    await user.click(screen.getByRole("button", { name: /导入/ }));
    await waitFor(() => expect(screen.getByText(/同名技能已存在/)).toBeInTheDocument());
    expect(onImported).not.toHaveBeenCalled();
    expect(input.files?.length ?? 0).toBeGreaterThan(0);
  });
});

function onImportedCapture() {
  return vi.fn();
}
