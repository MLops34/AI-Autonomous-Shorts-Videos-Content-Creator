"""Run ONLY the Manim animation pipeline for an existing outputs run.

Usage examples (from project root):

  # Run Manim for the latest outputs/<timestamp>_* folder
  python run_manim_only.py

  # Or specify a particular run folder explicitly
  python run_manim_only.py outputs\\2026-02-25_190728_explain_about_rag_tech
"""

from __future__ import annotations

import sys
from pathlib import Path

from src.video_manim import assemble_with_manim


def _find_latest_run(outputs_base: Path) -> Path | None:
    """Return the most recently modified subfolder under outputs/, or None."""
    if not outputs_base.exists():
        return None
    candidates = [p for p in outputs_base.iterdir() if p.is_dir()]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def main() -> None:
    root = Path(__file__).resolve().parent
    outputs_base = root / "outputs"

    if len(sys.argv) > 1:
        run_dir = Path(sys.argv[1]).resolve()
    else:
        latest = _find_latest_run(outputs_base)
        if latest is None:
            print("No runs found under 'outputs/' — generate a video once, then rerun this script.")
            return
        run_dir = latest

    print(f"[Manim-only] Using run directory: {run_dir}")
    result = assemble_with_manim(run_dir)
    if result:
        print(f"[Manim-only] Animated short created: {result}")
    else:
        print("[Manim-only] No video generated (see log output above).")


if __name__ == "__main__":
    main()

