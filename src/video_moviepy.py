# src/video_moviepy.py
"""Video assembly using MoviePy - creates vertical shorts from images and audio."""

from pathlib import Path
from typing import Optional, List, Dict

import numpy as np

# Pillow 10+ removed Image.ANTIALIAS which MoviePy still references.
# Provide a backward-compatible alias so resize/on_color keep working.
try:  # pragma: no cover - environment dependent
    from PIL import Image as _PILImage  # type: ignore[import-not-found]

    if not hasattr(_PILImage, "ANTIALIAS") and hasattr(_PILImage, "Resampling"):
        _PILImage.ANTIALIAS = _PILImage.Resampling.LANCZOS  # type: ignore[attr-defined]
except Exception:
    _PILImage = None  # type: ignore[assignment]

# Explicit imports - no wildcard, no linter warnings
# `moviepy` is an external dependency provided via requirements.txt
from moviepy.editor import (  # type: ignore[import-not-found]
    AudioFileClip,
    ImageClip,
    concatenate_videoclips,
)
from moviepy.video.fx.fadein import fadein  # type: ignore[import-not-found]
from moviepy.video.fx.fadeout import fadeout  # type: ignore[import-not-found]

try:
    from imageio import imread
except ImportError:
    imread = None  # fallback to ImageClip(path) later


# Default transition durations (seconds) — synced with diagram/script changes
DEFAULT_FADE_IN = 0.4
DEFAULT_FADE_OUT = 0.3
DEFAULT_CROSSFADE = 0.15  # overlap between sections


def _calculate_safe_fade_durations(
    duration: float,
    fade_in: float = DEFAULT_FADE_IN,
    fade_out: float = DEFAULT_FADE_OUT,
    min_visible: float = 0.5,  # minimum time content is fully visible
) -> tuple[float, float]:
    """Calculate fade durations that don't exceed audio duration.
    
    Ensures content is visible for at least min_visible seconds.
    Returns (adjusted_fade_in, adjusted_fade_out).
    """
    total_fade_budget = duration - min_visible
    if total_fade_budget <= 0:
        # Extremely short clip: minimal fades
        return (0.05, 0.05)
    
    # Scale fades proportionally if they exceed budget
    requested_total = fade_in + fade_out
    if requested_total > total_fade_budget:
        scale = total_fade_budget / requested_total
        return (fade_in * scale, fade_out * scale)
    
    return (fade_in, fade_out)


def assemble_vertical_short(
    images_folder: Path,
    audio_folder: Path,
    output_path: Path,
    sections: List[Dict],
    resolution: tuple = (1080, 1920),
    fps: int = 30,
    zoom_factor: float = 0.02,
    background_color: tuple = (18, 18, 38),
    fade_in_duration: float = DEFAULT_FADE_IN,
    fade_out_duration: float = DEFAULT_FADE_OUT,
) -> Optional[Path]:
    """
    Assemble vertical short video from images and audio files.
    
    Args:
        images_folder: Path to folder containing section_*.png files
        audio_folder: Path to folder containing section_*.mp3 files
        output_path: Final video output path
        sections: Script sections metadata
        resolution: Output video resolution (width, height)
        fps: Frames per second
        zoom_factor: Ken Burns zoom intensity (0 to disable)
        background_color: RGB tuple for padding color
        fade_in_duration: Seconds for each diagram to fade in (arrow/box appear)
        fade_out_duration: Seconds for each diagram to fade out before next
    
    Returns:
        Path to generated video or None if failed
    """
    
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Build clips by section index so each Mermaid diagram stays in sync with its narration.
    # Use section_01.png + section_01.mp3, section_02.png + section_02.mp3, etc.
    num_sections = len(sections) if sections else 0
    if num_sections == 0:
        # Fallback: discover from files (zero-padded names)
        image_files = sorted(images_folder.glob("section_*.png"))
        audio_files = sorted(audio_folder.glob("section_*.mp3"))
        num_sections = min(len(image_files), len(audio_files))
        if num_sections == 0:
            print(f"No sections or matching image/audio files in {images_folder} / {audio_folder}")
            return None

    # Log what we have so user can see if clips are missing
    existing_images = [images_folder / f"section_{i:02d}.png" for i in range(1, num_sections + 1) if (images_folder / f"section_{i:02d}.png").exists()]
    existing_audio = [audio_folder / f"section_{i:02d}.mp3" for i in range(1, num_sections + 1) if (audio_folder / f"section_{i:02d}.mp3").exists()]
    print(f"Video assembly: {len(existing_images)} images, {len(existing_audio)} audio files (sections 1..{num_sections})")
    if len(existing_images) < num_sections or len(existing_audio) < num_sections:
        print("WARNING: Some section_XX.png or section_XX.mp3 files are missing — only existing pairs will be used.")

    clips = []
    for i in range(1, num_sections + 1):
        img_p = images_folder / f"section_{i:02d}.png"
        aud_p = audio_folder / f"section_{i:02d}.mp3"
        if not img_p.exists():
            print(f"Skip section {i}: image missing {img_p.name}")
            continue
        if not aud_p.exists():
            print(f"Skip section {i}: audio missing {aud_p.name}")
            continue
        try:
            audio_clip = AudioFileClip(str(aud_p))
            duration = audio_clip.duration

            # Load image as array so each clip has its own pixel data (avoids any caching)
            if imread is not None:
                img_array = np.array(imread(str(img_p)), dtype=np.uint8)
                if img_array.ndim == 2:
                    img_array = np.dstack([img_array] * 3)
                img_clip = ImageClip(img_array.copy()).set_duration(duration)
            else:
                img_clip = ImageClip(str(img_p)).set_duration(duration)

            # Short template size (1080×1920): fit entire diagram inside frame — scale to fit, then pad.
            # No cropping so diagrams are never cut off ("out of space").
            w, h = resolution[0], resolution[1]
            img_w, img_h = img_clip.w, img_clip.h
            scale = min(w / img_w, h / img_h)  # fit inside template; scale up small diagrams too
            img_clip = img_clip.resize(scale)
            # Center on template and pad to exact short size (1080×1920)
            img_clip = img_clip.on_color(
                size=resolution,
                color=background_color,
                pos="center"
            )

            # Gentle Ken Burns zoom (settings.yaml zoom_factor) — was unused before
            if zoom_factor and float(zoom_factor) > 0 and duration > 0.4:
                zf = float(zoom_factor)
                try:
                    img_clip = img_clip.resize(lambda t: 1.0 + zf * (t / max(duration, 0.01)))
                except Exception:
                    pass

            # Transitions synced with script: diagram fades in (arrow/box appear), then fades out to next
            bg = list(background_color)
            # Use safe fade calculation to ensure audio sync
            fade_in, fade_out = _calculate_safe_fade_durations(
                duration, fade_in_duration, fade_out_duration
            )
            img_clip = img_clip.fx(fadein, fade_in, initial_color=bg)
            img_clip = img_clip.fx(fadeout, fade_out, final_color=bg)

            # Every clip is exactly resolution so chained output stays consistent.
            clip_with_audio = img_clip.set_audio(audio_clip)
            if getattr(clip_with_audio, "fps", None) is None:
                clip_with_audio.fps = fps
            clips.append(clip_with_audio)
            print(f"Clip {i}: {img_p.name} + {aud_p.name} ({duration:.1f}s)")
        except Exception as e:
            print(f"Error processing section {i} ({img_p.name} + {aud_p.name}): {e}")
            continue

    if not clips:
        print("No clips to concatenate")
        return None

    print(f"Assembling {len(clips)} clips sequentially (chain).")
    # method="chain" = play one clip after another; "compose" can misbehave with multiple segments
    final_clip = concatenate_videoclips(clips, method="chain")
    if getattr(final_clip, "fps", None) is None:
        final_clip.fps = fps

    final_clip.write_videofile(
        str(output_path),
        fps=fps,
        codec="libx264",
        audio_codec="aac",
        audio_bitrate="192k",
        threads=4,
        preset="slow",
        ffmpeg_params=["-crf", "20"],
        logger=None,
    )

    print(f"Video ready: {output_path}")
    return output_path