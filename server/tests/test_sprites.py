"""Deck sprite assets: the distinct-deck walk and the composite layout."""

from PIL import Image

from app import sprites


class FakeResponse:
    def __init__(self, json_data):
        self.status_code = 200
        self._json = json_data

    def json(self):
        return self._json

    def raise_for_status(self):
        pass


class FakeClient:
    """Answers the keyset walk out of a table sorted by deck_id."""

    def __init__(self, rows):
        self.rows = sorted(rows, key=lambda r: r["deck_id"])
        self.requests = []

    def request(self, method, url, **kwargs):
        after = kwargs["params"]["deck_id"].removeprefix("gt.")
        self.requests.append(after)
        later = [row for row in self.rows if row["deck_id"] > after]
        return FakeResponse(later[:1])


def _row(deck_id, name, icons):
    return {"deck_id": deck_id, "deck_name": name, "deck_icons": icons}


def test_decks_returns_each_id_once():
    # Two rows per deck, as a real standings table has hundreds of.
    client = FakeClient(
        [
            _row("charizard-pidgeot", "Charizard Pidgeot", ["charizard", "pidgeot"]),
            _row("charizard-pidgeot", "Charizard Pidgeot", ["charizard", "pidgeot"]),
            _row("alakazam-ex", "Alakazam", ["alakazam"]),
            _row("alakazam-ex", "Alakazam", ["alakazam"]),
        ]
    )
    found = sprites.decks(client, "http://db", {})

    assert [deck["deck_id"] for deck in found] == ["alakazam-ex", "charizard-pidgeot"]
    # One request per deck, plus the one that finds nothing left and stops.
    assert client.requests == ["", "alakazam-ex", "charizard-pidgeot"]


class FakeSpriteClient:
    """Serves sprites from whichever hosts are listed in `available`."""

    def __init__(self, available):
        self.available = available
        self.urls = []

    def request(self, method, url, **kwargs):
        self.urls.append(url)
        found = any(url.startswith(host) for host in self.available)
        response = FakeResponse(None)
        response.status_code = 200 if found else 404
        response.content = b"png" if found else b""
        return response


def test_fetch_sprite_falls_back_to_the_older_host(tmp_path):
    # The Substitute doll on the "Other" deck 404s on the gen9 host.
    client = FakeSpriteClient([sprites.FALLBACK_SPRITE_BASE])
    sprites.fetch_sprite(client, "substitute", tmp_path / "substitute.png")

    assert (tmp_path / "substitute.png").read_bytes() == b"png"
    assert client.urls == [
        f"{sprites.SPRITE_BASE}/substitute.png",
        f"{sprites.FALLBACK_SPRITE_BASE}/substitute.png",
    ]


def test_fetch_sprite_does_not_probe_the_fallback_when_the_first_host_has_it(tmp_path):
    client = FakeSpriteClient([sprites.SPRITE_BASE])
    sprites.fetch_sprite(client, "dragapult", tmp_path / "dragapult.png")

    assert client.urls == [f"{sprites.SPRITE_BASE}/dragapult.png"]


def _sprite(path, size):
    Image.new("RGBA", size, (255, 0, 0, 255)).save(path)
    return path


def test_composite_bottom_aligns_and_gaps(tmp_path):
    sources = [
        _sprite(tmp_path / "tall.png", (20, 40)),
        _sprite(tmp_path / "short.png", (10, 16)),
    ]
    out = tmp_path / "deck.png"
    sprites.composite(sources, out)

    image = Image.open(out)
    assert image.size == (20 + sprites.GAP + 10, 40)
    # The short sprite sits on the bottom edge, not the top.
    assert image.getpixel((25, 39))[3] == 255
    assert image.getpixel((25, 0))[3] == 0
    # And the gap between them stays transparent.
    assert image.getpixel((20, 39))[3] == 0


def test_composite_of_one_sprite_has_no_trailing_gap(tmp_path):
    out = tmp_path / "deck.png"
    sprites.composite([_sprite(tmp_path / "solo.png", (28, 36))], out)

    assert Image.open(out).size == (28, 36)


def test_merge_manifest_keeps_aged_out_decks_and_replaces_seen_ones():
    previous = [
        {"id": "old-deck", "icons": ["a"]},
        {"id": "kept-deck", "icons": ["b"]},
    ]
    current = [
        {"id": "kept-deck", "icons": ["b", "c"]},
        {"id": "new-deck", "icons": ["d"]},
    ]
    assert sprites.merge_manifest(previous, current) == [
        {"id": "kept-deck", "icons": ["b", "c"]},
        {"id": "new-deck", "icons": ["d"]},
        {"id": "old-deck", "icons": ["a"]},
    ]


def test_read_manifest_of_a_missing_index_is_empty(tmp_path):
    assert sprites.read_manifest(tmp_path / "index.json") == []
