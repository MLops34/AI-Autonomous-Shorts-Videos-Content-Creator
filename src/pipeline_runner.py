# src/pipeline_runner.py
import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
import re

from .ollama_client import DEFAULT_MODEL, generate_script, query_ollama
from .mermaid_renderer import render_all_mmd_to_png, ensure_mermaid_has_diagram_type
from .tts_edge import generate_section_audios
from .video_moviepy import assemble_vertical_short
from .video_manim import assemble_with_manim


# In your pipeline_runner.py, before calling render_all_mmd_to_png:
import os

# Force the path to mmdc (adjust to your actual path)
mmdc_path = r"C:\Users\Acer\AppData\Roaming\npm\mmdc.cmd"

if os.path.exists(mmdc_path):
    os.environ["PATH"] = os.path.dirname(mmdc_path) + os.pathsep + os.environ.get("PATH", "")
    print(f"Added mmdc to PATH: {mmdc_path}")

logger = logging.getLogger("shorts_pipeline")


def setup_logging(out_dir: Path):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(out_dir / "pipeline.log", encoding="utf-8"),
        ],
    )


async def run_short_creation(
    topic: str,
    config_folder: Path = Path("config"),
    outputs_base: Path = Path("outputs"),
    model: str = DEFAULT_MODEL,
):
    # ── Create safe, unique output folder ────────────────────────────────
    safe_topic = re.sub(r"[^a-zA-Z0-9_-]", "_", topic.strip().lower())
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    topic_slug = f"{timestamp}_{safe_topic[:40]}"
    out_dir = outputs_base / topic_slug
    out_dir.mkdir(parents=True, exist_ok=True)

    setup_logging(out_dir)
    logger.info("Starting pipeline for topic: %s", topic)
    logger.info("Output folder: %s", out_dir)

    try:
        # ── 1. Generate script ───────────────────────────────────────────
        # NOTE: file names are based on the config/prompts folder in this repo
        script_prompt_path = config_folder / "prompts" / "01_scripts.txt"
        if not script_prompt_path.exists():
            raise FileNotFoundError(f"Prompt not found: {script_prompt_path}")

        script_prompt = script_prompt_path.read_text(encoding="utf-8")
        script_data = generate_script(topic, script_prompt, model=model)

        if not script_data or "sections" not in script_data:
            logger.error("Failed to generate valid script structure")
            return

        script_path = out_dir / "script.json"
        script_path.write_text(json.dumps(script_data, indent=2, ensure_ascii=False))
        logger.info("Script saved → %s (sections: %d)", script_path, len(script_data["sections"]))

        # Rough early check: too many sections / too long?
        total_est = sum(s.get("duration_estimate_sec", 15) for s in script_data["sections"])
        if total_est > 75:
            logger.warning("Estimated duration %.1fs — may exceed 60s target", total_est)

        # ── 2. Generate Mermaid for each section ─────────────────────────
        mermaid_tmpl_path = config_folder / "prompts" / "02_mermaid.txt"
        mermaid_tmpl = mermaid_tmpl_path.read_text(encoding="utf-8")

        mmd_folder = out_dir / "mmd"
        mmd_folder.mkdir(exist_ok=True)

        for i, section in enumerate(script_data["sections"], 1):
            heading = section.get("heading", "")
            text = section.get("text", section.get("narration", ""))  # flexible key names
            duration_sec = section.get("duration_sec", 10.0)  # default 10s if not provided

            prompt = (
                mermaid_tmpl.replace("{{heading}}", heading)
                .replace("{{text}}", text)
                .replace("{{NARRATION}}", text)  # support both styles
                .replace("{{duration_sec}}", str(duration_sec))
            )

            logger.info("Generating diagram for section %d: %s...", i, heading[:60])
            diagram_code = query_ollama(prompt, model=model)

            # Robust cleanup of common LLM wrappers
            diagram_code = diagram_code.strip()
            if "```mermaid" in diagram_code:
                diagram_code = diagram_code.split("```mermaid", 1)[1].split("```", 1)[0].strip()
            elif "```" in diagram_code:
                diagram_code = diagram_code.split("```", 2)[1].strip() if len(diagram_code.split("```")) > 2 else diagram_code

            # Ensure a valid diagram type so mmdc does not raise UnknownDiagramError
            diagram_code = ensure_mermaid_has_diagram_type(diagram_code)

            mmd_path = mmd_folder / f"section_{i:02d}.mmd"
            mmd_path.write_text(diagram_code, encoding="utf-8")
            logger.debug("Mermaid code saved → %s", mmd_path)

        # ── 3. Render Mermaid to PNG ─────────────────────────────────────
        logger.info("Rendering all diagrams to PNG...")
        images_folder = out_dir / "images"
        render_all_mmd_to_png(mmd_folder, images_folder)

        # ── 4. Generate audio clips ──────────────────────────────────────
        logger.info("Generating voiceover segments...")
        audio_folder = out_dir / "audio"
        await generate_section_audios(
            script_data["sections"],
            audio_folder,
            voice="en-US-GuyNeural"  # ← read from config/settings.yaml later
        )

        # ── 5. Assemble final video(s) ───────────────────────────────────
        logger.info("Assembling final vertical short with MoviePy...")
        video_folder = out_dir / "videos"
        video_folder.mkdir(exist_ok=True)
        final_video_path = video_folder / f"{topic_slug}_short.mp4"

        assemble_vertical_short(
            images_folder=images_folder,
            audio_folder=audio_folder,
            output_path=final_video_path,
            sections=script_data["sections"],
            zoom_factor=0.025,
        )

        logger.info("MoviePy short completed: %s", final_video_path)

        # ── 6. Optional: assemble animated short with Manim ──────────────
        try:
            logger.info("Assembling animated vertical short with Manim...")
            # Pass timing configuration from settings
            manim_result = assemble_with_manim(
                out_dir,
                title_reveal_ratio=0.12,
                content_reveal_ratio=0.25,
                hold_ratio=0.50,
                exit_ratio=0.13,
                min_step_time=0.4,
                max_step_time=1.2,
            )
            if manim_result:
                logger.info("Manim short completed: %s", manim_result)
            else:
                logger.warning("Manim assembly returned no video (see logs above).")
        except Exception as manim_err:
            logger.warning("Manim assembly skipped or failed: %s", manim_err)

        logger.info("Pipeline completed successfully!")
        logger.info("Final MoviePy video: %s", final_video_path)

    except Exception as e:
        logger.exception("Pipeline failed: %s", str(e))
        print(f"\nERROR — check {out_dir / 'pipeline.log'} for details")
        raise


def main_entry(topic: str, **kwargs):
    """Sync wrapper called from main.py"""
    asyncio.run(run_short_creation(topic, **kwargs))