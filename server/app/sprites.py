"""Build the deck sprite assets a frontend serves out of public/.

Every deck in silver carries `deck_icons`: the Pokemon whose sprites Limitless
draws beside that deck's name. Those icon names are exactly the filenames on
Limitless's sprite CDN - the same images https://limitlesstcg.com/decks renders
in its table - so the deck-to-sprite mapping is already in the database and
does not have to be scraped back off that page and re-matched by deck name.

A run writes two things into --out (default public/decks):

    <deck_id>.png        one file per deck, its icons composited side by side
    index.json           deck id -> display name, icons, and image path

The individual Pokemon sprites those composites are built from land in
--sprite-cache instead, outside the frontend's public/ directory and outside
git. They are ingredients, not assets: every one of their pixels also lives in
at least one composite, and half the decks have a single icon, so their
"composite" is that one sprite re-saved. Nothing serves them, so shipping them
would send the browser a second copy of every image it already has. The cache
exists only so a re-run refetches nothing - deleting it costs one redownload.

Files are keyed on `deck_id` rather than on the deck name, for two reasons.
Names are not unique - two unrelated decks are both called "Alakazam" - so
name-keyed files would silently overwrite each other. And `deck_id` is what
silver_pairings already carries in winner_deck_id / loser_deck_id, so a
frontend holding a pairing row also holds the key to its image. The display
name is in index.json for anything that wants to render it.

The deck list is read off silver_standings, the only silver table carrying the
icons. That is a superset of the decks in silver_pairings, which take their
decks from standings by join, so every deck named in a pairing has an image.

Rate limiting is not a concern here: the sprites come from Limitless's asset
CDN, not from the play.limitlesstcg.com API that RATE_LIMIT_INTERVAL paces.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx2 as httpx
from PIL import Image

from app import supabase

SPRITE_BASE = "https://r2.limitlesstcg.net/pokemon/gen9"

# Not every icon is a Pokemon. The catch-all "Other" deck is drawn with a
# Substitute doll, which lives on Limitless's older asset host and 404s on the
# gen9 one, so a miss there is retried here before it counts as a failure.
FALLBACK_SPRITE_BASE = "https://limitless3.nyc3.cdn.digitaloceanspaces.com/pokemon"

# The images are frontend assets, so they are written straight into the Vite
# app's public/ directory - relative to server/, which is where the script runs.
DEFAULT_OUT = "../client/public/decks"

# URL prefix baked into index.json. Defaults to what --out resolves to once Vite
# serves public/ at the site root.
DEFAULT_PUBLIC_BASE = "/decks"

# Where the raw per-Pokemon downloads are kept between runs. Relative to server/
# like --out is, and gitignored - see the module docstring for why it is not
# under public/.
DEFAULT_SPRITE_CACHE = ".sprite-cache"

# Pixels between two sprites on a composite. Sprites are ~20-45px wide, so this
# reads as a gap without pushing them apart.
GAP = 2

DECKS_TABLE = "silver_standings"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--out",
        default=DEFAULT_OUT,
        help=f"Directory to write into, relative to the repo root (default {DEFAULT_OUT}).",
    )
    parser.add_argument(
        "--public-base",
        default=DEFAULT_PUBLIC_BASE,
        help=f"URL prefix recorded in index.json (default {DEFAULT_PUBLIC_BASE}).",
    )
    parser.add_argument(
        "--sprite-cache",
        default=DEFAULT_SPRITE_CACHE,
        help=(
            "Directory holding the raw per-Pokemon downloads, relative to the repo "
            f"root (default {DEFAULT_SPRITE_CACHE}). Not served - see app/sprites.py."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download sprites already on disk. Composites are rebuilt either way.",
    )
    parser.add_argument("--dry-run", action="store_true")


def decks(client: httpx.Client, base: str, hdrs: dict[str, str]) -> list[dict[str, Any]]:
    """Every distinct (deck_id, deck_name, deck_icons) in silver_standings.

    PostgREST has no DISTINCT, and silver_standings is ~120k rows, so this walks
    the deck_id index one key at a time: each request asks for the first row
    above the last id seen. That is one small request per deck (~200 of them)
    instead of paging the whole table down the wire.
    """
    found: list[dict[str, Any]] = []
    last = ""
    while True:
        response = supabase._send(
            client,
            "GET",
            f"{base}/rest/v1/{DECKS_TABLE}",
            params={
                "select": "deck_id,deck_name,deck_icons",
                "deck_id": f"gt.{last}",
                "order": "deck_id.asc",
                "limit": "1",
            },
            headers=hdrs,
        )
        response.raise_for_status()
        page = response.json()
        if not page:
            return found
        found.append(page[0])
        last = page[0]["deck_id"]


def fetch_sprite(client: httpx.Client, icon: str, destination: Path) -> None:
    """Download one sprite, falling back to the older host. Raises if neither has it."""
    for sprite_base in (SPRITE_BASE, FALLBACK_SPRITE_BASE):
        response = supabase._send(client, "GET", f"{sprite_base}/{icon}.png")
        if response.status_code < 400:
            destination.write_bytes(response.content)
            return
    raise RuntimeError(f"HTTP {response.status_code} from both sprite hosts")


def composite(sources: list[Path], destination: Path) -> None:
    """Lay sprites out left to right on a transparent canvas.

    Sprites are trimmed to their artwork and vary in height, so they are
    bottom-aligned: aligning them any other way leaves a short Pokemon floating
    above a tall one's feet. Some arrive as palette PNGs and some as RGBA, hence
    the convert.
    """
    images = [Image.open(path).convert("RGBA") for path in sources]
    width = sum(image.width for image in images) + GAP * (len(images) - 1)
    height = max(image.height for image in images)

    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    offset = 0
    for image in images:
        canvas.paste(image, (offset, height - image.height), image)
        offset += image.width + GAP
    canvas.save(destination)


def read_manifest(path: Path) -> list[dict[str, Any]]:
    """The decks an existing index.json lists, or [] if there is none yet."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))["decks"]
    except (FileNotFoundError, KeyError, ValueError):
        return []


def merge_manifest(
    previous: list[dict[str, Any]], current: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """This run's decks, plus any earlier entry this run did not see.

    silver_standings only spans the retention window, so a deck that has aged
    out of it is no longer read - but its PNG is still in the directory (and
    still committed), so index.json keeps listing it rather than describing
    fewer files than are there. A deck seen this run replaces its old entry.
    """
    by_id = {deck["id"]: deck for deck in previous}
    by_id.update({deck["id"]: deck for deck in current})
    return [by_id[deck_id] for deck_id in sorted(by_id)]


def run(args: argparse.Namespace) -> int:
    try:
        base, secret_key = supabase.credentials()
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    hdrs = supabase.headers(secret_key)

    out = Path(args.out)
    sprite_dir = Path(args.sprite_cache)
    public_base = args.public_base.rstrip("/")

    with httpx.Client(timeout=60.0) as client:
        print(f"reading decks from {DECKS_TABLE} (one request per deck, this takes a minute)")
        found = decks(client, base, hdrs)

        icons = sorted({icon for deck in found for icon in deck["deck_icons"] or []})
        missing = [i for i in icons if args.force or not (sprite_dir / f"{i}.png").exists()]
        print(f"{len(found)} decks, {len(icons)} distinct sprites, {len(missing)} to download")

        if args.dry_run:
            print(f"dry run - would write {len(found)} composites and index.json under {out}")
            print(f"dry run - would cache {len(missing)} sprites under {sprite_dir}")
            return 0

        sprite_dir.mkdir(parents=True, exist_ok=True)
        out.mkdir(parents=True, exist_ok=True)

        failed_icons: set[str] = set()
        for i, icon in enumerate(missing, start=1):
            try:
                fetch_sprite(client, icon, sprite_dir / f"{icon}.png")
            except Exception as exc:
                failed_icons.add(icon)
                print(f"  [{i}/{len(missing)}] {icon} FAILED: {exc}")
                continue
            print(f"  [{i}/{len(missing)}] {icon}")

    manifest: list[dict[str, Any]] = []
    skipped: list[str] = []
    for deck in sorted(found, key=lambda d: d["deck_id"]):
        deck_id, deck_icons = deck["deck_id"], deck["deck_icons"] or []
        if not deck_icons or failed_icons.intersection(deck_icons):
            skipped.append(deck_id)
            continue

        sources = [sprite_dir / f"{icon}.png" for icon in deck_icons]
        composite(sources, out / f"{deck_id}.png")
        manifest.append(
            {
                "id": deck_id,
                "name": deck["deck_name"],
                "icons": deck_icons,
                "image": f"{public_base}/{deck_id}.png",
            }
        )

    index_path = out / "index.json"
    merged = merge_manifest(read_manifest(index_path), manifest)
    if merged == read_manifest(index_path):
        print(f"wrote {len(manifest)} deck images to {out}, index.json unchanged")
    else:
        index_path.write_text(
            json.dumps(
                {
                    "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "sprite_source": SPRITE_BASE,
                    "decks": merged,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"wrote {len(manifest)} deck images and index.json ({len(merged)} decks) to {out}")

    if skipped:
        print(f"no image for: {', '.join(skipped)}", file=sys.stderr)
    return 1 if skipped else 0
