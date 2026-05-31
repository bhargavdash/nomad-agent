"""Unit tests for image storage keying — pure, no network.

Covers the place-key content-addressing (the dedup mechanism) and the public
URL format. The resolve/download/upload paths are I/O and exercised live.
"""

from __future__ import annotations

import app.images as im


def test_place_key_is_stable_and_normalised() -> None:
    # Same place, different casing/whitespace -> same key (so trips dedupe).
    assert im._place_key("Jaipur", "Rajasthan, India") == im._place_key(
        "  jaipur ", "RAJASTHAN, India"
    )


def test_place_key_distinguishes_places_and_contexts() -> None:
    base = im._place_key("Jaipur", "Rajasthan, India")
    assert base != im._place_key("Jodhpur", "Rajasthan, India")
    assert base != im._place_key("Jaipur", "")  # hero (no context) is its own key
    assert len(base) == 20


def test_public_url_format(monkeypatch) -> None:
    monkeypatch.setattr(im.settings, "SUPABASE_URL", "https://proj.supabase.co/")
    monkeypatch.setattr(im.settings, "SUPABASE_IMAGE_BUCKET", "place-images")
    assert (
        im._public_url("places/abc123")
        == "https://proj.supabase.co/storage/v1/object/public/place-images/places/abc123"
    )
