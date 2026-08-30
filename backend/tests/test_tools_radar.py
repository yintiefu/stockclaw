"""query_news_radar 工具契约测试：key/名称双路匹配与 per_track 钳制（离线）。"""
from __future__ import annotations

import newsradar
import tools


def _industries_fixture() -> list[dict]:
    return [
        {"key": "ai", "name": "AI / 大模型", "items": [
            {"title": f"AI news {i}", "time": "08-30 12:00", "source": "源A", "url": f"https://a/{i}"}
            for i in range(30)
        ]},
        {"key": "semi", "name": "半导体 / 芯片", "items": [
            {"title": "Semi news one", "time": "08-30 11:00", "source": "源B", "url": "https://b/1"},
        ]},
        {"key": "energy", "name": "能源 / 新能源", "items": [
            {"title": "Energy news", "time": "08-30 10:00", "source": "源C", "url": "https://c/1"},
        ]},
    ]


def _patch_radar(monkeypatch) -> None:
    monkeypatch.setattr(
        newsradar, "get_radar",
        lambda force=False: {"generated_at": "2026-08-30 08:00", "recent_days": 7,
                             "industries": _industries_fixture()},
    )


def test_radar_matches_track_by_key(monkeypatch):
    _patch_radar(monkeypatch)
    out = tools.exec_tool("query_news_radar", {"track": "ai", "per_track": 25})
    assert [it["title"] for it in out["items"]] == [f"AI news {i}" for i in range(25)]
    assert all(it["track"] == "AI / 大模型" for it in out["items"])


def test_radar_matches_track_by_name_substring(monkeypatch):
    _patch_radar(monkeypatch)
    out = tools.exec_tool("query_news_radar", {"track": "半导体"})
    assert [it["title"] for it in out["items"]] == ["Semi news one"]


def test_radar_clamps_per_track(monkeypatch):
    _patch_radar(monkeypatch)
    # 上限 25；显式更小值生效；缺省 5
    assert len(tools.exec_tool("query_news_radar", {"track": "ai", "per_track": 99})["items"]) == 25
    assert len(tools.exec_tool("query_news_radar", {"track": "ai", "per_track": 3})["items"]) == 3
    assert len(tools.exec_tool("query_news_radar", {"track": "ai"})["items"]) == 5


def test_radar_returns_all_tracks_when_no_filter(monkeypatch):
    _patch_radar(monkeypatch)
    out = tools.exec_tool("query_news_radar", {})
    assert out["total_cached"] == 32
    assert out["tracks"] == ["AI / 大模型", "半导体 / 芯片", "能源 / 新能源"]
