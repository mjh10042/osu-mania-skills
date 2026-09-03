"""Build the exe and prove it carries none of the builder's own data.

The thing that gets handed to someone else is a binary built on a machine that has been
running the app for weeks, so the question is not "did I remember to clean up" - it is
"can this build contain my ids, my play history, or an index of my beatmap folders at
all". Two ways it could:

  * PyInstaller bundling the cache as data (the spec must keep `datas` empty)
  * the exe writing its cache next to itself, so the folder gets zipped along with it

Both are checked here, and then the built exe is launched against an empty cache to see
what a stranger would actually see on first run. Verifying the artifact beats trusting
the build config, because only the artifact gets shipped.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXE = ROOT / "dist" / "mania-skills.exe"
PRIVATE = ("settings.json", "plays.json", "songs.json", "msd.json")


def fail(msg: str) -> None:
    print(f"FAIL  {msg}")
    sys.exit(1)


def check_version() -> None:
    """The version resource and the number the app shows have to be the same number."""
    sys.path.insert(0, str(ROOT))
    from core.version import LABEL, VERSION

    res = (ROOT / "version.txt").read_text(encoding="utf-8")
    dotted = VERSION if VERSION.count(".") == 3 else f"{VERSION}.0"
    for needle in (f"'FileVersion', '{dotted}'", f"'ProductVersion', '{LABEL}'"):
        if needle not in res:
            fail(f"version.txt disagrees with core/version.py - expected {needle}")
    print(f"ok    version {LABEL} matches the version resource")


# Files the spec is allowed to bundle. The point of the check was never "bundle nothing",
# it was "bundle nothing of this machine's" - so shipped assets are named here one by one
# and anything else still stops the build.
ALLOWED_DATAS = {"assets/skull.ico"}


def check_spec() -> None:
    spec = (ROOT / "mania-skills.spec").read_text(encoding="utf-8")
    listed = set(re.findall(r"\(\s*'([^']+)'\s*,\s*'[^']*'\s*\)",
                            re.search(r"datas=\[(.*?)\]", spec, re.S).group(1)))
    unexpected = listed - ALLOWED_DATAS
    if unexpected:
        fail(f"the spec bundles files that are not on the allow-list: {sorted(unexpected)}")
    for name in listed:
        if not (ROOT / name).is_file():
            fail(f"the spec bundles {name}, which does not exist")
    print(f"ok    spec bundles only allow-listed assets ({sorted(listed) or 'none'})")


def build() -> None:
    print("building...")
    r = subprocess.run([sys.executable, "-m", "PyInstaller", str(ROOT / "mania-skills.spec"),
                        "--noconfirm"], cwd=ROOT, capture_output=True, text=True)
    if r.returncode != 0:
        fail(f"PyInstaller exited {r.returncode}\n{r.stdout[-2000:]}\n{r.stderr[-2000:]}")


# Anything shorter than this is not evidence. Measured against this artifact: random
# 3-character strings hit in 86% of tries and random 4-digit ones in 39%, purely because
# 17 MB of compiled code contains most short byte sequences by accident. Random 8-character
# strings hit 0 times out of 200, so at this length a hit means something.
MIN_EVIDENCE = 8


def _secrets() -> dict[str, str]:
    """Actual values from this machine, to search the artifact for.

    Looking for filenames would only prove the spec is clean; looking for the values
    proves the shipped bytes are.
    """
    found: dict[str, str] = {}
    cache = ROOT / "cache"
    try:
        saved = json.loads((cache / "settings.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        saved = {}
    for key in ("osu_id", "mame_id", "osu_root"):
        if saved.get(key):
            found[key] = str(saved[key])
    try:
        songs = json.loads((cache / "songs.json").read_text(encoding="utf-8"))
        sample = next(iter(songs.get("by_md5", songs).values()))
        found["a beatmap path"] = str(sample)
        found["the Songs folder"] = str(Path(sample).parent.parent)
    except (OSError, json.JSONDecodeError, StopIteration, AttributeError):
        pass
    # The names in scores.db are the one piece of this machine the older check missed,
    # and they are the answer to the app's own "whose scores?" question.
    try:
        sys.path.insert(0, str(ROOT))
        from core.local_scores import players_seen

        for i, name in enumerate(n for n in players_seen() if n):
            found[f"a scores.db name ({name[:12]})"] = name
    except Exception:
        pass
    try:
        plays = json.loads((cache / "plays.json").read_text(encoding="utf-8"))
        if plays:
            found["a played map"] = str(plays[0].get("title") or "")
    except (OSError, json.JSONDecodeError):
        pass
    return {k: v for k, v in found.items() if len(v) >= MIN_EVIDENCE}


def check_contents() -> None:
    """Search the shipped bytes - and everything packed inside them - for this machine.

    The raw file is not enough on its own: most of what PyInstaller bundles is stored
    compressed, so a value could sit inside the archive and never appear in a byte scan
    of the exe. Each entry is unpacked and searched too.
    """
    chunks = [EXE.read_bytes()]
    try:
        from PyInstaller.archive.readers import CArchiveReader

        archive = CArchiveReader(str(EXE))
        for name in archive.toc:
            try:
                chunks.append(archive.extract(name))
            except Exception:
                pass
    except Exception as exc:
        print(f"warn  could not unpack the archive to search it ({exc})")

    known = _secrets()
    if not known:
        print("warn  no local cache to test against - build machine is already clean")
    hits = []
    for key, value in known.items():
        needles = [value.encode(enc, "ignore") for enc in ("utf-8", "utf-16-le")]
        if any(n and n in c for c in chunks for n in needles):
            hits.append(key)
    if hits:
        fail(f"the builder's own data is inside the exe: {hits}")
    print(f"ok    exe and its {len(chunks) - 1} packed entries hold none of this "
          f"machine's data ({len(known)} values checked)")


def check_fresh_run() -> None:
    """Start the built exe with an empty cache and read back what it saved."""
    # ignore_cleanup_errors: a just-terminated GUI process can still hold the directory.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        env = {**os.environ, "MANIA_SKILLS_CACHE": tmp}
        before = {f.name for f in EXE.parent.iterdir()}
        p = subprocess.Popen([str(EXE)], env=env)
        try:
            p.wait(timeout=12)
        except subprocess.TimeoutExpired:
            p.terminate()
            p.wait(timeout=10)

        wrote = {f.name for f in EXE.parent.iterdir()} - before
        if wrote:
            fail(f"the exe wrote beside itself: {sorted(wrote)}")
        saved = Path(tmp) / "settings.json"
        data = json.loads(saved.read_text(encoding="utf-8")) if saved.exists() else {}
        for key in ("osu_id", "mame_id", "osu_root", "players"):
            if data.get(key):
                fail(f"a fresh run already knows {key}={data[key]!r}")
        stray = sorted(f.name for f in Path(tmp).rglob("*") if f.name in PRIVATE)
        print(f"ok    fresh run starts blank (wrote {stray or 'nothing'})")
        stale = sorted(before - {EXE.name})
        if stale:
            print(f"warn  leftovers in dist/ (not shipped, but delete them): {stale}")


def package() -> Path:
    out = ROOT / "release"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir()
    zip_path = out / "mania-skills.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(EXE, EXE.name)
    return zip_path


if __name__ == "__main__":
    check_version()
    check_spec()
    build()
    check_contents()
    check_fresh_run()
    z = package()
    print(f"\n{z}  ({z.stat().st_size / 1_048_576:.1f} MB, 1 file)")
