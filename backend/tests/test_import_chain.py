"""导入器：mock 富途 4 元组；分页；原序+limit；失败不提交；main() 级断言零 POST。"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "import-sector-chain.py"


def _load():
    spec = importlib.util.spec_from_file_location("import_sector_chain", SCRIPT)
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ---- plate_fn mock：签名 (plate_id, count, page) -> (ret, rows, next_page, all_count) ----


def _ok_single_page(_pid, _count, _page):
    rows = [
        {"security": "JP.6594", "name": "日本电产"},      # 过滤
        {"security": "SH.688017", "name": "绿的谐波"},
        {"security": "HK.00700", "name": "腾讯"},
        {"security": "US.AAPL", "name": "苹果"},
        {"security": "SZ.002472", "name": "双环传动"},
        {"security": "SH.688017", "name": "绿的谐波"},   # 重复
    ]
    return 0, rows, None, len(rows)


def _paged_plate_factory(pages):
    """pages: list[list[dict]]；逐页返回，next_page 为下一页标记或 None。"""
    state = {"i": 0}

    def fn(_pid, _count, _page):
        i = state["i"]
        rows = pages[i]
        nxt = f"page-{i + 1}" if i + 1 < len(pages) else None
        state["i"] += 1
        return 0, rows, nxt, sum(len(p) for p in pages)

    return fn


def _fail_plate(_pid, _count, _page):
    return -1, "mock error", None, 0


def _fail_on_page2_factory():
    """第一页成功、第二页失败。"""
    state = {"i": 0}

    def fn(_pid, _count, _page):
        i = state["i"]
        state["i"] += 1
        if i == 0:
            return 0, [{"security": "SH.600000", "name": "X"}], "page-1", 1
        return -1, "page2 boom", None, 0

    return fn


# ---- 单元：_pick_constituents ----


def test_pick_source_order_filter_limit_dedupe():
    m = _load()
    picked = m._pick_constituents("10104257", plate_fn=_ok_single_page, limit=3, page_size=50)
    codes = [s["code"] for s in picked]
    assert "JP.6594" not in codes
    # 原序：过滤 JP 后 SH → HK → US，limit 3，去重
    assert codes == ["SH.688017", "HK.00700", "US.AAPL"]


def test_pick_paginates_until_limit_or_no_next():
    m = _load()
    # 第一页 2 只合法、第二页 2 只合法；limit=3 应跨页取足
    fn = _paged_plate_factory([
        [{"security": "SH.1", "name": "a"}, {"security": "SH.2", "name": "b"}],
        [{"security": "HK.3", "name": "c"}, {"security": "US.4", "name": "d"}],
    ])
    picked = m._pick_constituents("p", plate_fn=fn, limit=3, page_size=2)
    assert [s["code"] for s in picked] == ["SH.1", "SH.2", "HK.3"]


def test_pick_stops_when_next_page_none_before_limit():
    m = _load()
    fn = _paged_plate_factory([[{"security": "SH.1", "name": "a"}]])
    picked = m._pick_constituents("p", plate_fn=fn, limit=8, page_size=50)
    assert [s["code"] for s in picked] == ["SH.1"]


def test_pick_failure_on_page2_raises():
    m = _load()
    fn = _fail_on_page2_factory()
    try:
        m._pick_constituents("p", plate_fn=fn, limit=8, page_size=1)
        assert False, "should raise"
    except m.PlateFetchError:
        pass


def test_collect_leaves_from_skeleton():
    m = _load()
    skel = {
        "tiers": [
            {
                "id": "up",
                "items": [
                    {
                        "id": "reducer",
                        "children": [
                            {"id": "harmonic", "plate_id": "10104257"},
                            {"id": "manual-leaf"},
                        ],
                    },
                    {"id": "bearing", "plate_id": "10104262"},
                ],
            }
        ]
    }
    leaves = m._collect_leaves(skel)
    assert {l[0] for l in leaves} == {"harmonic", "bearing"}


# ---- main() 级：注入 ctx_factory 与 poster，断言提交边界 ----


class _FakeCtx:
    def __init__(self):
        self.closed = 0

    def close(self):
        self.closed += 1


def _make_fake_sector_json(tmp_path: Path) -> Path:
    data = {"sectors": [
        {"key": "t", "chain_id": 1, "tiers": [
            {"id": "u", "items": [
                {"id": "leaf-a", "plate_id": "111"},
                {"id": "leaf-b", "plate_id": "222"},
            ]}
        ]}
    ]}
    p = tmp_path / "sectors.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def _ctx_factory_returning(plate_fn, ctx_holder):
    def factory(_opend_host):
        def fn(plate_id, count, page):
            return plate_fn(plate_id, count, page)
        fn._ctx = ctx_holder
        return fn
    return factory


def test_main_success_posts_once_and_closes_ctx(tmp_path, monkeypatch):
    m = _load()
    sectors_path = _make_fake_sector_json(tmp_path)
    ctx = _FakeCtx()
    posts = []

    def plate_fn(_pid, _count, _page):
        return 0, [{"security": "SH.600000", "name": "X"}], None, 1

    def poster(url, body, headers):
        posts.append((url, json.loads(body), headers))
        return 200, b'{"data":{}}'

    rc = m.main(
        ["--key", "t", "--backend", "http://x", "--limit", "8"],
        ctx_factory=_ctx_factory_returning(plate_fn, ctx),
        poster=poster,
        sectors_path=sectors_path,
    )
    assert rc == 0
    assert len(posts) == 1
    assert posts[0][0].endswith("/api/sectors/stocks/import")
    assert set(posts[0][1]["base"].keys()) == {"leaf-a", "leaf-b"}
    assert posts[0][1]["meta"]["import_note"].endswith("非市值排名")
    assert ctx.closed == 1


def test_main_fetch_failure_zero_posts_and_closes_ctx(tmp_path):
    m = _load()
    sectors_path = _make_fake_sector_json(tmp_path)
    ctx = _FakeCtx()
    posts = []

    def plate_fn(_pid, _count, _page):
        return -1, "boom", None, 0

    def poster(url, body, headers):
        posts.append(1)
        return 200, b"{}"

    rc = m.main(
        ["--key", "t", "--limit", "8"],
        ctx_factory=_ctx_factory_returning(plate_fn, ctx),
        poster=poster,
        sectors_path=sectors_path,
    )
    assert rc == 1
    assert posts == []          # 全有或全无：抓取失败不提交
    assert ctx.closed == 1      # ctx 仍被关闭一次


def test_main_pagination_failure_zero_posts(tmp_path):
    m = _load()
    sectors_path = _make_fake_sector_json(tmp_path)
    ctx = _FakeCtx()
    posts = []
    state = {"i": 0}

    def plate_fn(_pid, _count, _page):
        i = state["i"]
        state["i"] += 1
        if i == 0:
            return 0, [{"security": "SH.1", "name": "a"}], "page-1", 1
        return -1, "page2 boom", None, 0

    def poster(*a, **k):
        posts.append(1)
        return 200, b"{}"

    rc = m.main(
        ["--key", "t", "--limit", "8"],
        ctx_factory=_ctx_factory_returning(plate_fn, ctx),
        poster=poster,
        sectors_path=sectors_path,
    )
    assert rc == 1
    assert posts == []
    assert ctx.closed == 1


def test_main_http_error_returns_nonzero(tmp_path):
    m = _load()
    sectors_path = _make_fake_sector_json(tmp_path)
    ctx = _FakeCtx()
    posts = []

    def plate_fn(_pid, _count, _page):
        return 0, [{"security": "SH.600000", "name": "X"}], None, 1

    def poster(url, body, headers):
        posts.append(1)
        return 400, b'{"detail":"bad"}'

    rc = m.main(
        ["--key", "t", "--limit", "8"],
        ctx_factory=_ctx_factory_returning(plate_fn, ctx),
        poster=poster,
        sectors_path=sectors_path,
    )
    assert rc == 1
    assert len(posts) == 1
    assert ctx.closed == 1


def test_main_passes_page_size_and_page_to_plate_fn(tmp_path):
    """--page-size 必须真实传到 plate_fn 的 count，且分页时 page 推进。"""
    m = _load()
    sectors_path = _make_fake_sector_json(tmp_path)
    ctx = _FakeCtx()
    seen = []  # (count, page)
    state = {"i": 0}

    def plate_fn(_pid, count, page):
        seen.append((count, page))
        i = state["i"]
        state["i"] += 1
        if i == 0:
            return 0, [{"security": "SH.1", "name": "a"}], "page-1", 1
        return 0, [{"security": "HK.2", "name": "b"}], None, 2

    rc = m.main(
        ["--key", "t", "--limit", "8", "--page-size", "3"],
        ctx_factory=_ctx_factory_returning(plate_fn, ctx),
        poster=lambda *a, **k: (200, b"{}"),
        sectors_path=sectors_path,
    )
    assert rc == 0
    assert seen[0][0] == 3  # count == --page-size
    assert seen[0][1] is None  # 首页 page=None
    assert seen[1][1] == "page-1"  # 第二页用上一页 next_page


def test_main_rejects_limit_out_of_range(tmp_path):
    m = _load()
    sectors_path = _make_fake_sector_json(tmp_path)
    ctx = _FakeCtx()
    for bad in (["--key", "t", "--limit", "0"], ["--key", "t", "--limit", "201"], ["--key", "t", "--page-size", "0"]):
        rc = m.main(
            bad,
            ctx_factory=_ctx_factory_returning(lambda *_a, **_k: (0, [], None, 0), ctx),
            poster=lambda *a, **k: (200, b"{}"),
            sectors_path=sectors_path,
        )
        assert rc == 2, f"应拒绝非法参数: {bad}"


def test_main_futu_exception_is_caught_zero_posts(tmp_path):
    """富途调用抛普通异常时：清晰 stderr、非零退出、零 POST、ctx 仍关一次。"""
    m = _load()
    sectors_path = _make_fake_sector_json(tmp_path)
    ctx = _FakeCtx()
    posts = []

    def plate_fn(_pid, _count, _page):
        raise RuntimeError("OpenD 连接断开")

    rc = m.main(
        ["--key", "t", "--limit", "8"],
        ctx_factory=_ctx_factory_returning(plate_fn, ctx),
        poster=lambda *a, **k: posts.append(1) or (200, b"{}"),
        sectors_path=sectors_path,
    )
    assert rc == 1
    assert posts == []
    assert ctx.closed == 1
