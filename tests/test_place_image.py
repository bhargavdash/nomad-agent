"""Unit tests for the place-image resolver — pure, no network.

Covers the photo-vs-non-photo filtering, index-ordered picking, and the
in-process memo. Network calls (_from_wikipedia / _from_pexels) are
monkeypatched so these run offline with no API keys.
"""

from __future__ import annotations

import app.tools.place_image as pi


def test_is_photo_accepts_normal_jpg() -> None:
    assert pi._is_photo("https://upload.wikimedia.org/x/Jaipur_City_Palace.jpg") is True


def test_is_photo_rejects_svg_maps_flags_and_none() -> None:
    assert pi._is_photo("https://upload.wikimedia.org/x/India_location_map.svg") is False
    assert pi._is_photo("https://upload.wikimedia.org/x/Flag_of_India.png") is False
    assert pi._is_photo("https://upload.wikimedia.org/x/Map_of_Goa.png") is False
    assert pi._is_photo("https://upload.wikimedia.org/x/map-goa.png") is False
    assert pi._is_photo(None) is False


def test_pick_photo_url_orders_by_index_and_skips_non_photo() -> None:
    pages = [
        {"index": 2, "thumbnail": {"source": "https://cdn/Beach_sunset.jpg"}},
        {"index": 1, "thumbnail": {"source": "https://cdn/Locator_map.svg"}},
    ]
    # index 1 is a locator map (rejected) -> falls through to the index-2 photo.
    assert pi._pick_photo_url(pages) == "https://cdn/Beach_sunset.jpg"


def test_pick_photo_url_none_when_all_non_photo() -> None:
    pages = [{"index": 1, "thumbnail": {"source": "https://cdn/coat_of_arms.svg"}}]
    assert pi._pick_photo_url(pages) is None


def test_clean_url_strips_query() -> None:
    assert pi._clean_url("https://cdn/p.jpg?w=1280&q=80") == "https://cdn/p.jpg"


async def test_resolve_memoizes(monkeypatch) -> None:
    calls = {"n": 0}

    async def fake_wiki(_client, _q, _ctx):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        return "https://cdn/jaipur.jpg"

    monkeypatch.setattr(pi, "_from_wikipedia", fake_wiki)
    pi._MEMO.clear()

    first = await pi.resolve_place_image_url("Jaipur", "Rajasthan, India")
    second = await pi.resolve_place_image_url("Jaipur", "Rajasthan, India")

    assert first == second == "https://cdn/jaipur.jpg"
    assert calls["n"] == 1  # second lookup served from the memo, not re-fetched


async def test_resolve_blank_query_returns_none() -> None:
    assert await pi.resolve_place_image_url("   ") is None
