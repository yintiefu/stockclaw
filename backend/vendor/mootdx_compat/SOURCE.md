# mootdx 0.11.7+vr1 vendored copy

- 包名 / 版本: `mootdx` / `0.11.7+vr1`
- 上游 wheel: `mootdx-0.11.7-py3-none-any.whl`
- 上游 wheel SHA-256: `eab475f1d08b1c71ea51212c8b1b1038c4739798f7d95ad1a6fb7bb26e348ef2`
- PyPI 项目: https://pypi.org/project/mootdx/0.11.7/
- 获取日期: 2026-08-16（经清华镜像 https://pypi.tuna.tsinghua.edu.cn/simple 下载）

`src/mootdx/` 下的 Python/JS 源文件与上游 wheel 逐字节一致（见 `upstream.sha256`）。
与上游 0.11.7 的**唯一**差异是打包元数据：`pyproject.toml` 将 httpx 约束从
`httpx>=0.25.0,<0.26.0` 放宽为 `httpx>=0.27.1,<1`，以兼容 MCP 栈锁定的
`httpx==0.28.1`。Python 源码零改动。
