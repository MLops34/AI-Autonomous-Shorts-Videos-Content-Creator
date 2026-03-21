"""Manim-only pipeline runner.

This script is intentionally separate from the "normal" pipeline (MoviePy, etc).
It supports two modes:

1) Topic mode (recommended): generate assets + render with Manim into a separate base folder.
   - Generates:
       - script.json
       - mmd/section_XX.mmd
       - audio/section_XX.mp3
   - Renders:
       - videos_manim/<run_name>_short_manim.mp4

2) Existing-run mode: point to an already-generated run folder (e.g. under outputs/).
   The script copies needed assets into the Manim-only base folder and renders there,
   keeping Manim outputs separate from the normal pipeline outputs.

Examples (from project root):

  # Manim-only pipeline from a topic (writes to outputs_manim/)
  python run_manim_only.py "how database indexes work"

  # Use a specific OpenRouter model and TTS voice
  python run_manim_only.py "rag explained" --model "deepseek/deepseek-r1-0528:free" --voice "en-US-GuyNeural"

  # Render from an existing normal run (copies into outputs_manim/ first)
  python run_manim_only.py outputs\\2026-02-25_190728_explain_about_rag_tech
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import shutil
from datetime import datetime
from pathlib import Path

from src.ollama_client import DEFAULT_MODEL, generate_script, query_ollama
from src.mermaid_renderer import ensure_mermaid_has_diagram_type, render_all_mmd_to_png
from src.tts_edge import generate_section_audios
from src.video_manim import assemble_with_manim


def _find_latest_run(outputs_base: Path) -> Path | None:
    """Return the most recently modified subfolder under outputs_base/, or None."""
    if not outputs_base.exists():
        return None
    candidates = [p for p in outputs_base.iterdir() if p.is_dir()]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _safe_slug(text: str, max_len: int = 40) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]", "_", text.strip().lower())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return (cleaned or "manim_run")[:max_len]


def _new_run_dir(outputs_base: Path, name_hint: str) -> Path:
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    slug = _safe_slug(name_hint)
    run_name = f"{timestamp}_{slug}"
    out = outputs_base / run_name
    out.mkdir(parents=True, exist_ok=True)
    return out


def _copy_dir(src: Path, dst: Path) -> None:
    if not src.exists():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


def _copy_file(src: Path, dst: Path) -> None:
    if not src.exists():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


async def _build_assets_for_topic(run_dir: Path, topic: str, model: str, voice: str) -> None:
    root = Path(__file__).resolve().parent
    config_folder = root / "config"

    script_prompt_path = config_folder / "prompts" / "01_scripts.txt"
    mermaid_tmpl_path = config_folder / "prompts" / "02_mermaid.txt"
    if not script_prompt_path.exists():
        raise FileNotFoundError(f"Prompt not found: {script_prompt_path}")
    if not mermaid_tmpl_path.exists():
        raise FileNotFoundError(f"Prompt not found: {mermaid_tmpl_path}")

    script_prompt = script_prompt_path.read_text(encoding="utf-8")
    mermaid_tmpl = mermaid_tmpl_path.read_text(encoding="utf-8")

    script_data = generate_script(topic, script_prompt, model=model)
    if not script_data or "sections" not in script_data:
        raise RuntimeError("Failed to generate a valid script.json structure.")

    (run_dir / "script.json").write_text(json.dumps(script_data, indent=2, ensure_ascii=False), encoding="utf-8")

    mmd_folder = run_dir / "mmd"
    mmd_folder.mkdir(exist_ok=True)

    for i, section in enumerate(script_data["sections"], 1):
        heading = section.get("heading", "")
        text = section.get("text", section.get("narration", ""))
        prompt = (
            mermaid_tmpl.replace("{{heading}}", heading)
            .replace("{{text}}", text)
            .replace("{{NARRATION}}", text)
        )

        diagram_code = query_ollama(prompt, model=model).strip()

        # Robust cleanup of common LLM wrappers
        if "```mermaid" in diagram_code:
            diagram_code = diagram_code.split("```mermaid", 1)[1].split("```", 1)[0].strip()
        elif "```" in diagram_code:
            parts = diagram_code.split("```")
            if len(parts) >= 3:
                diagram_code = parts[1].strip()

        diagram_code = ensure_mermaid_has_diagram_type(diagram_code)
        (mmd_folder / f"section_{i:02d}.mmd").write_text(diagram_code, encoding="utf-8")

    images_folder = run_dir / "images"
    print("[Manim-only] Rendering Mermaid diagrams to PNG...")
    render_all_mmd_to_png(mmd_folder, images_folder)

    audio_folder = run_dir / "audio"
    await generate_section_audios(script_data["sections"], audio_folder, voice=voice)


def _prepare_from_existing_run(outputs_manim_base: Path, source_run_dir: Path) -> Path:
    run_dir = _new_run_dir(outputs_manim_base, source_run_dir.name)

    _copy_file(source_run_dir / "script.json", run_dir / "script.json")
    _copy_dir(source_run_dir / "mmd", run_dir / "mmd")
    _copy_dir(source_run_dir / "audio", run_dir / "audio")
    # Optional (used only as a fallback in src/video_manim.py)
    _copy_dir(source_run_dir / "images", run_dir / "images")

    if not (run_dir / "script.json").exists():
        raise FileNotFoundError(f"Missing script.json in source run: {source_run_dir}")
    if not (run_dir / "audio").exists():
        raise FileNotFoundError(f"Missing audio/ in source run: {source_run_dir}")

    return run_dir


async def main_async() -> int:
    parser = argparse.ArgumentParser(description="Run Manim-only pipeline (separate outputs).")
    parser.add_argument(
        "input",
        nargs="?",
        default=None,
        help="Either a topic string OR a path to an existing run directory.",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=DEFAULT_MODEL,
        help="OpenRouter model id (used for script + Mermaid generation).",
    )
    parser.add_argument(
        "--voice",
        type=str,
        default="en-US-GuyNeural",
        help="edge-tts voice name (used to generate audio clips).",
    )
    parser.add_argument(
        "--outputs-base",
        type=str,
        default="outputs_manim",
        help="Base folder where Manim-only runs are written.",
    )
    parser.add_argument(
        "--latest",
        action="store_true",
        help="Re-render the latest run under outputs-base (no generation).",
    )

    args = parser.parse_args()

    root = Path(__file__).resolve().parent
    outputs_manim_base = (root / args.outputs_base).resolve()
    outputs_manim_base.mkdir(parents=True, exist_ok=True)

    run_dir: Path

    if args.latest:
        latest = _find_latest_run(outputs_manim_base)
        if latest is None:
            print(f"No runs found under '{outputs_manim_base}'. Provide a topic first.")
            return 2
        run_dir = latest
    else:
        if not args.input or not str(args.input).strip():
            print("Provide a topic (recommended) or a path to an existing run folder.")
            print('Example: python run_manim_only.py "how RAG works"')
            return 2

        maybe_path = Path(args.input)
        if maybe_path.exists() and maybe_path.is_dir():
            print(f"[Manim-only] Copying assets from existing run: {maybe_path.resolve()}")
            run_dir = _prepare_from_existing_run(outputs_manim_base, maybe_path.resolve())
        else:
            topic = str(args.input).strip()
            run_dir = _new_run_dir(outputs_manim_base, topic)
            print(f"[Manim-only] Generating assets for topic: {topic}")
            print(f"[Manim-only] Run directory: {run_dir}")
            await _build_assets_for_topic(run_dir, topic=topic, model=args.model, voice=args.voice)

    print(f"[Manim-only] Rendering with Manim from: {run_dir}")
    result = assemble_with_manim(run_dir)
    if result:
        print(f"[Manim-only] Animated short created: {result}")
        return 0
    print("[Manim-only] No video generated (see log output above).")
    return 1


def main() -> None:
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()

