"""Fetch upstream sources into data/raw/. Run once; never during simulation.

    python3.12 data/fetch.py --all
    python3.12 data/fetch.py --source riftcodex
    python3.12 data/fetch.py --enumerate-tabs

Everything lands under data/raw/<source-key>/ alongside a `_meta.json`
recording the URL, HTTP status, byte count, and a sha256 of the payload, so a
later re-fetch can be diffed against what the current cards.json was built
from.

stdlib only (urllib) -- no `requests` dependency.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sources import (  # noqa: E402
    ALL_SOURCES,
    SHEET_KNOWN_GIDS,
    Source,
    sheet_csv_url,
    sheet_html_url,
)

RAW = Path(__file__).resolve().parent / "raw"
UA = "riftbound-sim/0.1 (research; contact via repo owner)"
TIMEOUT = 30


class FetchError(RuntimeError):
    """Raised when a URL could not be retrieved."""


def _get(url: str) -> tuple[bytes, int]:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.read(), resp.status
    except urllib.error.HTTPError as exc:
        raise FetchError(f"HTTP {exc.code} for {url}") from exc
    except urllib.error.URLError as exc:
        # In a policy-restricted environment this is where an egress denial
        # surfaces (CONNECT tunnel refused). It is a blocked host, not a bug.
        raise FetchError(f"cannot reach {url}: {exc.reason}") from exc


def _write(dest: Path, url: str, payload: bytes, status: int) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(payload)
    meta = {
        "url": url,
        "status": status,
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    meta_path = dest.parent / "_meta.json"
    existing = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    existing[dest.name] = meta
    meta_path.write_text(json.dumps(existing, indent=2, sort_keys=True) + "\n")
    print(f"  wrote {dest.relative_to(RAW.parent)}  ({len(payload)} bytes)")


def enumerate_sheet_tabs() -> dict[str, str]:
    """Discover every tab (name -> gid) in the community spreadsheet.

    The htmlview export embeds the sheet menu, which carries both the gid and
    the human-readable tab name. Returns the mapping and caches it.
    """
    url = sheet_html_url()
    payload, status = _get(url)
    html = payload.decode("utf-8", errors="replace")
    # Anchors look like: href="#gid=530194913">All Card Info</a>
    pairs = re.findall(r'#gid=(\d+)["\'][^>]*>([^<]+)<', html)
    tabs: dict[str, str] = {}
    for gid, name in pairs:
        tabs.setdefault(name.strip(), gid)
    if not tabs:
        raise FetchError(
            "no tabs parsed from htmlview -- the export format changed; "
            "inspect data/raw/community_sheet/_htmlview.html by hand"
        )
    _write(RAW / "community_sheet" / "_htmlview.html", url, payload, status)
    (RAW / "community_sheet" / "_tabs.json").write_text(
        json.dumps(tabs, indent=2, sort_keys=True) + "\n"
    )
    print(f"  discovered {len(tabs)} tabs: {', '.join(sorted(tabs))}")
    return tabs


def fetch_sheet() -> None:
    try:
        tabs = enumerate_sheet_tabs()
    except FetchError as exc:
        print(f"  ! tab enumeration failed ({exc}); falling back to known gids")
        tabs = {name: gid for name, gid in SHEET_KNOWN_GIDS.items()}
    for name, gid in sorted(tabs.items()):
        slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or gid
        url = sheet_csv_url(gid)
        payload, status = _get(url)
        _write(RAW / "community_sheet" / f"{slug}.csv", url, payload, status)


def fetch_api(src: Source) -> None:
    if not src.endpoints:
        raise FetchError(
            f"{src.key}: no endpoints registered. The live API has never been "
            f"reached from this environment, so its routes are unknown. "
            f"Probe {src.base} and fill in Source.endpoints in data/sources.py "
            f"before running this."
        )
    for path, filename in src.endpoints.items():
        url = f"{src.base.rstrip('/')}/{path.lstrip('/')}"
        payload, status = _get(url)
        _write(RAW / src.key / filename, url, payload, status)


def fetch_rules(src: Source) -> None:
    payload, status = _get(src.base)
    if not payload.startswith(b"%PDF"):
        raise FetchError(
            f"{src.base} did not return a PDF (got {payload[:16]!r}) -- the "
            f"rules URL is stale; re-locate the current core rules document."
        )
    _write(RAW / src.key / "core_rules.pdf", src.base, payload, status)


def fetch(src: Source) -> bool:
    print(f"[{src.key}] {src.base}")
    try:
        if src.kind == "sheet":
            fetch_sheet()
        elif src.kind == "api":
            fetch_api(src)
        elif src.kind == "rules":
            fetch_rules(src)
        else:
            raise FetchError(f"unknown source kind {src.kind!r}")
    except FetchError as exc:
        print(f"  FAILED: {exc}")
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--all", action="store_true", help="fetch every source")
    ap.add_argument("--source", action="append", default=[], metavar="KEY")
    ap.add_argument("--enumerate-tabs", action="store_true")
    args = ap.parse_args(argv)

    if args.enumerate_tabs:
        try:
            enumerate_sheet_tabs()
        except FetchError as exc:
            print(f"FAILED: {exc}")
            return 1
        return 0

    selected = [s for s in ALL_SOURCES if args.all or s.key in args.source]
    if not selected:
        ap.error("nothing selected; pass --all or --source KEY")

    failed = [s.key for s in selected if not fetch(s)]
    if failed:
        print(f"\n{len(failed)}/{len(selected)} source(s) failed: {', '.join(failed)}")
        return 1
    print(f"\nall {len(selected)} source(s) cached under data/raw/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
