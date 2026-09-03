"""Thin wrapper around the bundled MinaCalc CLI (msd.exe)."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from .bundle import bundled

SKILLSETS = ["overall", "stream", "jumpstream", "handstream",
             "stamina", "jackspeed", "chordjack", "technical"]

# msd.exe only supports these keymodes; anything else has no MinaCalc rating.
SUPPORTED_KEYS = {4, 6, 7}


def msd_path() -> Path:
    return bundled("vendor", "msd.exe")


class MsdError(RuntimeError):
    pass


def compute(notes: list[dict], rate: float = 1.0) -> dict[str, float]:
    """Run MinaCalc over note rows at a given music rate. Returns the 8 skillset values.

    msd.exe ignores its CLI rate argument, so the rate is applied by compressing the
    note timeline instead (verified to reproduce mania-tracker's rated values exactly).
    """
    if not notes:
        raise MsdError("no notes")

    exe = msd_path()
    if not exe.exists():
        raise MsdError(f"msd.exe not found at {exe}")

    if abs(rate - 1.0) > 1e-9:
        notes = [{"notes": n["notes"], "time": n["time"] / rate} for n in notes]

    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    proc = subprocess.run(
        [str(exe)],
        input=json.dumps(notes),
        capture_output=True, text=True, timeout=120,
        creationflags=creationflags,
    )
    out = (proc.stdout or "").strip()
    if not out or out.startswith("Error"):
        raise MsdError((out or proc.stderr or "msd.exe produced no output").strip()[:300])
    try:
        data = json.loads(out)
    except json.JSONDecodeError as exc:
        raise MsdError(f"bad msd output: {out[:200]}") from exc
    return {k: float(data[k]) for k in SKILLSETS}
