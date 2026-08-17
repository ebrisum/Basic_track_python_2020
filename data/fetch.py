"""Fetch upstream sources into data/raw/. Run once; never during simulation.

    python3.12 data/fetch.py --all
    python3.12 data/fetch.py --source apitcg

Everything lands under data/raw/<source-key>/ alongside a `_meta.json`
recording the URL, byte count, and sha256 of each file, so a later re-fetch can
be diffed against what the current cards.json was built from.

stdlib only for HTTP (urllib); git sources shell out to `git`.

The four sources named in the project brief are blocked by this environment's
egress proxy; see `sources.BLOCKED_SOURCES` and BLOCKER-1 in
RULES_QUESTIONS.md. The sources here are reachable substitutes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sources import ALL_SOURCES, BLOCKED_SOURCES, Source  # noqa: E402

RAW = Path(__file__).resolve().parent / "raw"
UA = "riftbound-sim/0.1 (research; contact via repo owner)"
TIMEOUT = 60

# The Core Rules PDFs are ~25-45 MB each and only the current one is needed as
# text. Keeping the PDFs out of git and committing the extracted text instead
# keeps the repo usable while preserving the citable content.
RULES_PDF = "CR-v1.4.pdf"
RULES_TXT = "CR-v1.4.txt"


class FetchError(RuntimeError):
    """Raised when a source could not be retrieved."""


def _record(dest: Path, url: str, payload: bytes) -> None:
    meta_path = dest.parent / "_meta.json"
    existing = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    existing[dest.name] = {
        "url": url,
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    meta_path.write_text(json.dumps(existing, indent=2, sort_keys=True) + "\n")


def _write(dest: Path, url: str, payload: bytes) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(payload)
    _record(dest, url, payload)
    print(f"  {dest.relative_to(RAW.parent)}  ({len(payload):,} bytes)")


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        raise FetchError(f"HTTP {exc.code} for {url}") from exc
    except urllib.error.URLError as exc:
        # A policy denial surfaces here as a refused CONNECT tunnel. That is a
        # blocked host, not a bug -- report it, do not retry.
        raise FetchError(f"cannot reach {url}: {exc.reason}") from exc


def fetch_npm(src: Source) -> None:
    """Resolve the package's tarball and lift the files we need out of it."""
    meta = json.loads(_get(src.url))
    version = src.version or meta["dist-tags"]["latest"]
    if version not in meta["versions"]:
        raise FetchError(f"{src.key}: version {version} not published")
    tarball = meta["versions"][version]["dist"]["tarball"]
    payload = _get(tarball)

    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / "pkg.tgz"
        archive.write_bytes(payload)
        with tarfile.open(archive) as tar:
            for member, dest_name in src.paths.items():
                try:
                    extracted = tar.extractfile(member)
                except KeyError:
                    extracted = None
                if extracted is None:
                    raise FetchError(f"{src.key}: {member} not in tarball")
                _write(RAW / src.key / dest_name, f"{tarball}#{member}", extracted.read())


def _git_clone(url: str, dest: Path) -> None:
    result = subprocess.run(
        ["git", "clone", "--depth", "1", "--quiet", url, str(dest)],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result.returncode != 0:
        raise FetchError(f"git clone {url} failed: {result.stderr.strip()}")


def _extract_rules_text(pdf: Path, dest: Path, url: str) -> None:
    """Extract the current Core Rules PDF to text.

    The PDFs are 24-43 MB each and mostly images; the repo stores the text.
    pypdf is a dev-time-only dependency -- nothing in engine/ or cards/ imports
    it, and simulation never reads the PDF.
    """
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise FetchError(
            "pypdf is needed to extract the rules text: "
            "uv pip install --python .venv/bin/python pypdf"
        ) from exc

    reader = PdfReader(str(pdf))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    payload = text.encode()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(payload)
    _record(dest, url, payload)
    print(f"  {dest.relative_to(RAW.parent)}  ({len(reader.pages)} pages, {len(payload):,} bytes)")


def fetch_git(src: Source) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        clone = Path(tmp) / "repo"
        _git_clone(src.url, clone)
        out_dir = RAW / src.key
        out_dir.mkdir(parents=True, exist_ok=True)

        copied = 0
        for pattern in src.globs:
            for path in sorted(clone.glob(pattern)):
                if not path.is_file():
                    continue
                # Card JSON and the manifest are copied; the huge PDFs are not
                # committed, only the current one's extracted text.
                if path.suffix == ".pdf":
                    if path.name == RULES_PDF:
                        _extract_rules_text(path, out_dir / RULES_TXT, f"{src.url}#{path.name}")
                        copied += 1
                    continue
                if path.suffix == ".mdx":
                    continue  # judge rulings: consulted by hand, not ingested
                name = "_sets.json" if path.parent.name == "sets" else path.name
                _write(out_dir / name, f"{src.url}#{pattern}", path.read_bytes())
                copied += 1
        if not copied:
            raise FetchError(f"{src.key}: globs {src.globs} matched nothing")


def fetch(src: Source) -> bool:
    print(f"[{src.key}] {src.url}")
    try:
        if src.kind == "npm":
            fetch_npm(src)
        elif src.kind == "git":
            fetch_git(src)
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
    ap.add_argument(
        "--show-blocked",
        action="store_true",
        help="list the brief's original sources and why they are not used",
    )
    args = ap.parse_args(argv)

    if args.show_blocked:
        print("Blocked by this environment's egress proxy (403 at CONNECT):")
        for key, url in BLOCKED_SOURCES.items():
            print(f"  {key:<20} {url}")
        print("\nSee BLOCKER-1 in RULES_QUESTIONS.md.")
        return 0

    selected = [s for s in ALL_SOURCES if args.all or s.key in args.source]
    if not selected:
        ap.error("nothing selected; pass --all, --source KEY, or --show-blocked")

    failed = [s.key for s in selected if not fetch(s)]
    if failed:
        print(f"\n{len(failed)}/{len(selected)} source(s) failed: {', '.join(failed)}")
        return 1
    print(f"\nall {len(selected)} source(s) cached under data/raw/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
