# src/video_moviepy.py
"""Video assembly using MoviePy - creates vertical shorts from images and audio."""

from pathlib import Path
from typing import Optional, List, Dict

# Explicit imports - no wildcard, no linter warnings
# `moviepy` is an external dependency provided via requirements.txt
from moviepy.editor import AudioFileClip, ImageClip, concatenate_videoclips  # type: ignore[import-not-found]


def assemble_vertical_short(
    images_folder: Path,
    audio_folder: Path,
    output_path: Path,
    sections: List[Dict],
    resolution: tuple = (1080, 1920),
    fps: int = 30,
    zoom_factor: float = 0.02,
    background_color: tuple = (18, 18, 38)
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
    
    Returns:
        Path to generated video or None if failed
    """
    
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Get sorted lists of files
    image_files = sorted(images_folder.glob("section_*.png"))
    audio_files = sorted(audio_folder.glob("section_*.mp3"))

    if not image_files:
        print(f"No images found in {images_folder}")
        return None
        
    if not audio_files:
        print(f"No audio files found in {audio_folder}")
        return None

    if len(image_files) != len(audio_files):
        print(f"Mismatch: {len(image_files)} images vs {len(audio_files)} audio files")
        min_count = min(len(image_files), len(audio_files))
        image_files = image_files[:min_count]
        audio_files = audio_files[:min_count]

    clips = []
    current_time = 0.0

    for img_p, aud_p in zip(image_files, audio_files):
        try:
            audio_clip = AudioFileClip(str(aud_p))
            duration = audio_clip.duration

            img_clip = (
                ImageClip(str(img_p))
                .set_duration(duration)
                .set_start(current_time)
                .resize(height=resolution[1])
            )

            # Center crop if too wide, else pad
            if img_clip.w > resolution[0]:
                img_clip = img_clip.crop(
                    x_center=img_clip.w / 2,
                    width=resolution[0]
                )
            else:
                img_clip = img_clip.on_color(
                    size=resolution,
                    color=background_color,
                    pos="center"
                )

            # Gentle zoom-in effect
            if zoom_factor > 0 and duration > 0:
                img_clip = img_clip.resize(
                    lambda t: 1 + (zoom_factor * t / duration)
                )

            clips.append(img_clip.set_audio(audio_clip))
            current_time += duration
            
        except Exception as e:
            print(f"Error processing {img_p.name} + {aud_p.name}: {e}")
            continue

    if not clips:
        print("No clips to concatenate")
        return None

    final_clip = concatenate_videoclips(clips, method="compose")

    final_clip.write_videofile(
        str(output_path),
        fps=fps,
        codec="libx264",
        audio_codec="aac",
        threads=4,
        preset="medium",
        logger=None
    )

    print(f"Video ready: {output_path}")
    return output_path