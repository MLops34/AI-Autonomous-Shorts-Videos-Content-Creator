"""Experimental Manim-based video assembly for animated sections.

This module reuses the existing pipeline outputs:
- Mermaid-rendered section images:  images/section_01.png, section_02.png, ...
- TTS audio clips:                  audio/section_01.mp3, section_02.mp3, ...
- Script JSON:                      script.json (for section metadata if needed)

For each section it:
- Uses Manim to animate the corresponding PNG into a short clip.
- (Optionally) attaches the matching audio and concatenates everything with MoviePy.

You can call `assemble_with_manim` from a small script or an interactive session.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Tuple

import json
import re

from moviepy.editor import (  # type: ignore[import-not-found]
    AudioFileClip,
    VideoFileClip,
    concatenate_videoclips,
)

try:
    # Manim Community Edition
    from manim import (  # type: ignore[import-not-found]
        Scene,
        ImageMobject,
        FadeIn,
        FadeOut,
        VGroup,
        Rectangle,
        Arrow,
        Text,
        Create,
        DOWN,
        UP,
        ORIGIN,
        config,
        tempconfig,
    )
except ImportError as e:  # pragma: no cover - runtime environment dependent
    Scene = object  # type: ignore[assignment]
    ImageMobject = object  # type: ignore[assignment]
    FadeIn = FadeOut = None  # type: ignore[assignment]
    VGroup = Rectangle = Arrow = Text = Create = object  # type: ignore[assignment]
    DOWN = UP = ORIGIN = None  # type: ignore[assignment]
    config = None  # type: ignore[assignment]
    tempconfig = None  # type: ignore[assignment]
    _MANIM_IMPORT_ERROR = e
else:
    _MANIM_IMPORT_ERROR = None


def _require_manim() -> None:
    """Raise a clear error if Manim is not installed."""
    if _MANIM_IMPORT_ERROR is not None:
        raise RuntimeError(
            "Manim is not available in this environment.\n"
            "Install the Manim Community Edition in your venv, for example:\n"
            "  pip install manim\n"
            "Then re-run the pipeline."
        ) from _MANIM_IMPORT_ERROR


def _load_sections(script_path: Path) -> List[Dict]:
    """Load sections from script.json, handling occasional bad encoding bytes gracefully."""
    raw = script_path.read_bytes()
    try:
        txt = raw.decode("utf-8")
    except UnicodeDecodeError:
        # Fallback: ignore invalid bytes but still try to parse JSON
        txt = raw.decode("utf-8", errors="ignore")
    try:
        data = json.loads(txt)
    except json.JSONDecodeError:
        return []
    return data.get("sections", [])


def _parse_mermaid_graph(mmd_path: Path) -> Optional[Tuple[List[Tuple[str, str]], List[Tuple[str, str]]]]:
    """Parse a simple Mermaid graph (graph TD/LR) into nodes and edges.

    Supports lines like:
        graph TD
            A[Start] --> B[Next]
            B --> C[End]

    Returns:
        (nodes, edges) where:
          nodes = [(id, label), ...]
          edges = [(src_id, dst_id), ...]
        or None if parsing fails or type unsupported.
    """
    if not mmd_path.exists():
        return None

    raw = mmd_path.read_text(encoding="utf-8", errors="ignore")
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip() and not ln.strip().startswith("%")]
    if not lines:
        return None

    first = lines[0].lower()
    if not first.startswith("graph"):
        return None

    node_labels: Dict[str, str] = {}
    edges: List[Tuple[str, str]] = []

    # Pattern: ID[Label] --> ID[Label]
    edge_re = re.compile(
        r"^([A-Za-z0-9_]+)\s*(\[[^\]]+\]|\([^)]+\)|\{[^}]+\})?\s*-->\s*([A-Za-z0-9_]+)\s*(\[[^\]]+\]|\([^)]+\)|\{[^}]+\})?\s*$"
    )

    def _clean_label(raw_label: Optional[str], fallback: str) -> str:
        if not raw_label:
            return fallback
        # Strip surrounding brackets/parentheses/braces
        txt = raw_label.strip()
        if (txt[0], txt[-1]) in {("[", "]"), ("(", ")"), ("{", "}")}:
            txt = txt[1:-1]
        return txt.strip() or fallback

    for line in lines[1:]:
        m = edge_re.match(line)
        if not m:
            continue
        src_id, src_lbl_raw, dst_id, dst_lbl_raw = m.group(1), m.group(2), m.group(3), m.group(4)
        src_label = _clean_label(src_lbl_raw, src_id)
        dst_label = _clean_label(dst_lbl_raw, dst_id)
        if src_id not in node_labels:
            node_labels[src_id] = src_label
        if dst_id not in node_labels:
            node_labels[dst_id] = dst_label
        edges.append((src_id, dst_id))

    if not node_labels:
        return None

    nodes = [(nid, node_labels[nid]) for nid in node_labels.keys()]
    return nodes, edges


def _build_section_scene_class(
    image_path: Path,
    duration: float,
    background_color: Tuple[float, float, float] = (0.07, 0.07, 0.15),
) -> type:
    """Dynamically build a simple Manim Scene class for one static PNG.

    The animation is:
    - Fade in the diagram.
    - Hold while narration plays.
    - Fade out before the next section.
    """
    _require_manim()

    fade = min(0.5, max(0.2, duration * 0.15))
    # Split remaining time into two beats for simple "flow" animation
    remaining = max(0.0, duration - 2 * fade)
    beat1 = remaining * 0.5
    beat2 = remaining - beat1

    class SectionScene(Scene):  # type: ignore[misc]
        def construct(self) -> None:  # type: ignore[override]
            # Background
            try:
                self.camera.background_color = background_color  # type: ignore[attr-defined]
            except Exception:
                pass

            diagram = ImageMobject(str(image_path))
            # Fit nicely for a 9:16 vertical frame
            diagram.set_height(6)

            # 1) Fade diagram in (like elements appearing)
            self.play(FadeIn(diagram), run_time=fade)

            # 2) Gentle "flow" motion: small zoom + shift to mimic arrows/boxes progressing
            if beat1 > 0:
                self.play(
                    diagram.animate.scale(1.05).shift(0.3 * UP),
                    run_time=beat1,
                )

            # 3) Second beat: slight pan the other way (gives a feeling of flow across nodes)
            if beat2 > 0:
                self.play(
                    diagram.animate.shift(0.6 * DOWN),
                    run_time=beat2,
                )

            # 4) Fade out to transition to the next section
            self.play(FadeOut(diagram), run_time=fade)

    return SectionScene


def _build_graph_scene_class(
    nodes: List[Tuple[str, str]],
    edges: List[Tuple[str, str]],
    duration: float,
    background_color: Tuple[float, float, float] = (0.07, 0.07, 0.15),
) -> type:
    """Build a Manim Scene that draws a simple flowchart node-by-node and edge-by-edge.

    Animation:
      - First node fades in.
      - For each edge: draw arrow, then reveal destination node (if not yet visible).
      - Finally fade everything out.
    """
    _require_manim()

    fade = min(0.8, max(0.3, duration * 0.15))
    steps = max(1, len(edges) + 1)  # first node + one step per edge
    remaining = max(0.0, duration - fade)
    step_time = remaining / steps if steps > 0 else remaining

    class GraphScene(Scene):  # type: ignore[misc]
        def construct(self) -> None:  # type: ignore[override]
            # Background
            try:
                self.camera.background_color = background_color  # type: ignore[attr-defined]
            except Exception:
                pass

            # Build node boxes with labels inside the rectangles
            node_groups: Dict[str, VGroup] = {}
            node_mobs: List[VGroup] = []
            for node_id, label in nodes:
                # Older Manim versions don't support corner_radius on Rectangle
                box = Rectangle(width=5.0, height=1.2)
                # Slightly larger font so labels stay readable on portrait video
                text = Text(label, font_size=40, line_spacing=0.8)
                text.move_to(box.get_center())
                group = VGroup(box, text)
                node_groups[node_id] = group
                node_mobs.append(group)

            if not node_mobs:
                return

            # Arrange nodes vertically to suggest flow
            flow = VGroup(*node_mobs).arrange(DOWN, buff=0.6).move_to(ORIGIN)
            # Keep the whole flowchart comfortably inside the portrait frame
            max_flow_height = 6.5
            if flow.height > max_flow_height:
                flow.scale(max_flow_height / flow.height)

            # Initially hide all nodes
            for g in flow:
                g.set_opacity(0.0)

            visible_nodes = set()
            arrows: List[Arrow] = []

            # 1) Show first node
            first_group = node_groups[nodes[0][0]]
            self.play(FadeIn(first_group), run_time=step_time * 0.7)
            visible_nodes.add(nodes[0][0])

            # 2) Edges: draw arrow, then reveal destination node
            for src_id, dst_id in edges:
                if src_id not in node_groups or dst_id not in node_groups:
                    continue
                src = node_groups[src_id]
                dst = node_groups[dst_id]
                arrow = Arrow(
                    src.get_bottom(),
                    dst.get_top(),
                    buff=0.12,
                )
                arrows.append(arrow)
                # Draw arrow
                self.play(Create(arrow), run_time=step_time * 0.6)
                # Reveal destination node if not yet visible
                if dst_id not in visible_nodes:
                    self.play(FadeIn(dst), run_time=step_time * 0.4)
                    visible_nodes.add(dst_id)

            # 3) Hold briefly if we have spare time
            leftover = max(0.0, duration - fade - steps * step_time)
            if leftover > 0:
                self.wait(leftover)

            # 4) Fade everything out
            all_mobs = VGroup(flow, *arrows)
            self.play(FadeOut(all_mobs), run_time=fade)

    return GraphScene


def _render_section_with_manim(
    image_path: Path,
    duration: float,
    output_dir: Path,
    resolution: Tuple[int, int] = (1080, 1920),
    fps: int = 30,
    background_color: Tuple[int, int, int] = (18, 18, 38),
    section_index: int = 1,
    mmd_path: Optional[Path] = None,
) -> Path:
    """Render a single section video using Manim and return the resulting mp4 path."""
    _require_manim()

    output_dir.mkdir(parents=True, exist_ok=True)

    # Manim expects colors in 0–1 floats; convert template color.
    bg_float = tuple(c / 255.0 for c in background_color)

    # Prefer graph-based animation when we can parse the Mermaid spec
    scene_cls: type
    parsed_graph: Optional[Tuple[List[Tuple[str, str]], List[Tuple[str, str]]]] = None
    if mmd_path is not None and mmd_path.exists():
        parsed_graph = _parse_mermaid_graph(mmd_path)

    if parsed_graph is not None:
        nodes, edges = parsed_graph
        scene_cls = _build_graph_scene_class(
            nodes=nodes,
            edges=edges,
            duration=duration,
            background_color=bg_float,  # type: ignore[arg-type]
        )
    else:
        # Fallback: animate the static PNG like before
        scene_cls = _build_section_scene_class(
            image_path=image_path,
            duration=duration,
            background_color=bg_float,  # type: ignore[arg-type]
        )

    # Configure Manim for this render only.
    scene_name = f"section_{section_index:02d}_manim"
    cfg_overrides = {
        "pixel_width": resolution[0],
        "pixel_height": resolution[1],
        "frame_rate": fps,
        "background_color": bg_float,
        "output_file": scene_name,
        "media_dir": str(output_dir),
        "video_dir": str(output_dir),
        "images_dir": str(output_dir / "images"),
    }

    if tempconfig is None:
        raise RuntimeError("Manim tempconfig is not available. Check your Manim installation.")

    with tempconfig(cfg_overrides):
        scene = scene_cls()
        scene.render()

    # Manim writes to <output_dir>/<scene_name>.mp4
    return output_dir / f"{scene_name}.mp4"


def assemble_with_manim(
    run_dir: Path,
    resolution: Tuple[int, int] = (1080, 1920),
    fps: int = 30,
    background_color: Tuple[int, int, int] = (18, 18, 38),
    use_manim_audio: bool = False,
) -> Optional[Path]:
    """High-level helper to build an animated short using Manim per section.

    Args:
        run_dir:      One pipeline run folder under outputs (contains script.json, images/, audio/).
        resolution:   Output resolution (width, height) in pixels.
        fps:          Frames per second.
        background_color: RGB tuple for the vertical canvas.
        use_manim_audio: If True, you will embed audio in Manim scenes yourself and this
                         function will only concatenate the resulting videos. If False
                         (default), this function attaches the existing section_XX.mp3
                         to each rendered Manim clip and then concatenates.

    Returns:
        Path to the final assembled video, or None on failure.
    """
    _require_manim()

    run_dir = run_dir.resolve()
    script_path = run_dir / "script.json"
    images_dir = run_dir / "images"
    audio_dir = run_dir / "audio"
    mmd_dir = run_dir / "mmd"
    videos_dir = run_dir / "videos_manim"

    if not script_path.exists():
        print(f"script.json not found in {run_dir}")
        return None
    if not images_dir.exists():
        print(f"images/ folder not found in {run_dir} (still needed as fallback)")
    if not audio_dir.exists():
        print(f"audio/ folder not found in {run_dir}")
        return None

    sections = _load_sections(script_path)
    if not sections:
        print("No sections found in script.json")
        return None

    videos_dir.mkdir(parents=True, exist_ok=True)

    clips: List[VideoFileClip] = []

    for i, _section in enumerate(sections, start=1):
        img_path = images_dir / f"section_{i:02d}.png"
        aud_path = audio_dir / f"section_{i:02d}.mp3"
        mmd_path = mmd_dir / f"section_{i:02d}.mmd"

        if not aud_path.exists():
            print(f"[Manim] Skip section {i}: missing audio {aud_path.name}")
            continue

        # Use audio length as the section duration.
        audio_clip = AudioFileClip(str(aud_path))
        duration = float(audio_clip.duration)

        if not mmd_path.exists() and not img_path.exists():
            print(f"[Manim] Skip section {i}: missing both Mermaid spec and image")
            continue

        print(f"[Manim] Rendering animated section {i} ({duration:.2f}s)...")
        section_video_path = _render_section_with_manim(
            image_path=img_path,
            duration=duration,
            output_dir=videos_dir,
            resolution=resolution,
            fps=fps,
            background_color=background_color,
            section_index=i,
            mmd_path=mmd_path if mmd_dir.exists() else None,
        )

        vclip = VideoFileClip(str(section_video_path))
        if not use_manim_audio:
            vclip = vclip.set_audio(audio_clip)
        clips.append(vclip)

    if not clips:
        print("[Manim] No section clips rendered.")
        return None

    print(f"[Manim] Concatenating {len(clips)} animated sections...")
    final_clip = concatenate_videoclips(clips, method="chain")
    final_output = videos_dir / f"{run_dir.name}_short_manim.mp4"

    final_clip.write_videofile(
        str(final_output),
        fps=fps,
        codec="libx264",
        audio_codec="aac",
        threads=4,
        preset="medium",
        logger=None,
    )

    print(f"[Manim] Animated video ready: {final_output}")
    return final_output

