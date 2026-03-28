"""
Experimental Manim-based video assembly for animated sections.

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
import textwrap

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
        RoundedRectangle,
        Arrow,
        Text,
        Create,
        GrowFromCenter,
        GrowArrow,
        DOWN,
        UP,
        LEFT,
        RIGHT,
        ORIGIN,
        WHITE,
        config,
        tempconfig,
    )
    from manim.utils.rate_functions import smooth, ease_out_sine, ease_in_out_sine  # type: ignore[import-not-found]
except ImportError as e:  # pragma: no cover - runtime environment dependent
    Scene = object  # type: ignore[assignment]
    ImageMobject = object  # type: ignore[assignment]
    FadeIn = FadeOut = None  # type: ignore[assignment]
    VGroup = Rectangle = RoundedRectangle = Arrow = Text = Create = GrowFromCenter = GrowArrow = object  # type: ignore[assignment]
    DOWN = UP = LEFT = RIGHT = ORIGIN = WHITE = None  # type: ignore[assignment]
    config = None  # type: ignore[assignment]
    tempconfig = None  # type: ignore[assignment]
    smooth = ease_out_sine = ease_in_out_sine = None  # type: ignore[assignment]
    _MANIM_IMPORT_ERROR = e
else:
    _MANIM_IMPORT_ERROR = None

# Graph scene palette (readable on dark vertical shorts)
_ARROW_VIS = "#4FC3FF"
_NODE_FILL = "#152B45"
_NODE_STROKE = "#7EB6FF"
_NODE_TEXT = "#F5F9FF"
_NODE_TEXT_STROKE = "#071018"


def _wrap_mermaid_label(raw: str, chars_per_line: int = 16, max_lines: int = 2) -> str:
    """Break long Mermaid labels into 1–2 lines so boxes stay legible on phone."""
    t = " ".join((raw or "").split()).strip()
    if not t:
        return "·"
    if len(t) <= chars_per_line and "\n" not in t:
        return t
    lines = textwrap.wrap(
        t.replace("\n", " "),
        width=chars_per_line,
        break_long_words=True,
        break_on_hyphens=True,
    )
    if not lines:
        return t[: chars_per_line + 1] + "…"
    if len(lines) <= max_lines:
        return "\n".join(lines)
    trimmed = lines[:max_lines]
    trimmed[-1] = trimmed[-1][: max(1, chars_per_line - 1)].rstrip() + "…"
    return "\n".join(trimmed)


def _build_node_card(
    label: str,
    font_size: int,
    chars_per_line: int,
) -> "VGroup":
    """Rounded card + multiline text sized to the label."""
    _require_manim()
    wrapped = _wrap_mermaid_label(label, chars_per_line=chars_per_line, max_lines=2)
    try:
        txt = Text(
            wrapped,
            font_size=font_size,
            line_spacing=0.52,
            color=_NODE_TEXT,
            stroke_width=1.5,
            stroke_color=_NODE_TEXT_STROKE,
            disable_ligatures=True,
        )
    except TypeError:
        txt = Text(
            wrapped,
            font_size=font_size,
            line_spacing=0.52,
            color=_NODE_TEXT,
            stroke_width=1.5,
            stroke_color=_NODE_TEXT_STROKE,
        )
    pad_x, pad_y = 0.58, 0.46
    box_w = min(6.4, max(2.35, float(txt.width) + pad_x))
    box_h = max(0.88, float(txt.height) + pad_y)
    box = RoundedRectangle(
        width=box_w,
        height=box_h,
        corner_radius=min(0.22, box_h * 0.18),
    )
    box.set_fill(_NODE_FILL, opacity=1.0)
    box.set_stroke(_NODE_STROKE, width=3.5, opacity=1.0)
    txt.move_to(box.get_center())
    return VGroup(box, txt)


def _flow_arrow(start, end, horizontal: bool) -> "Arrow":
    """Thick, high-contrast arrow with padding so tips do not sit inside the cards."""
    buff = 0.2 if horizontal else 0.26
    common: dict = {
        "stroke_width": 9,
        "color": _ARROW_VIS,
        "buff": buff,
    }
    try:
        return Arrow(
            start, end, max_tip_length_to_length_ratio=0.22, **common
        )  # type: ignore[call-arg]
    except TypeError:
        return Arrow(start, end, **common)


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


def _parse_mermaid_graph(
    mmd_path: Path,
) -> Optional[Tuple[List[Tuple[str, str]], List[Tuple[str, str]], str]]:
    """Parse a simple Mermaid graph (graph TD/LR) into nodes, edges, and layout.

    Returns ``(nodes, edges, layout)`` where layout is ``\"TD\"`` (vertical) or ``\"LR\"``
    (horizontal) from the diagram declaration. Manim uses this to match Mermaid flow.

    Supports:
        graph TD / graph LR / flowchart TD / flowchart LR
            A[Start] --> B[Next]
        Ignores classDiagram, subgraph, etc.
    """
    if not mmd_path.exists():
        return None

    raw = mmd_path.read_text(encoding="utf-8", errors="ignore")
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip() and not ln.strip().startswith("%")]
    if not lines:
        return None

    first = lines[0].lower()
    if not (first.startswith("graph") or first.startswith("flowchart")):
        return None

    layout = "TD"
    parts = lines[0].strip().lower().split()
    if len(parts) >= 2 and parts[1] in ("lr", "rl"):
        layout = "LR"

    node_labels: Dict[str, str] = {}
    edges: List[Tuple[str, str]] = []

    def _clean_label(raw_label: Optional[str], fallback: str) -> str:
        if not raw_label:
            return fallback
        txt = raw_label.strip()
        if len(txt) >= 2 and (txt[0], txt[-1]) in {("[", "]"), ("(", ")"), ("{", "}")}:
            txt = txt[1:-1]
        txt = txt.strip().replace("\n", " ").strip() or fallback
        return txt[:80] if len(txt) > 80 else txt

    # Match node id and optional label: ID, ID[Label], ID(Label), ID{Label}
    node_re = re.compile(
        r"([A-Za-z0-9_]+)\s*(?:\[([^\]]*)\]|\(([^)]*)\)|\{([^}]*)\})?"
    )

    def _extract_node(m: "re.Match") -> Tuple[str, str]:
        nid = m.group(1)
        label = m.group(2) or m.group(3) or m.group(4)
        return nid, _clean_label(label, nid)

    # Match edge: ID[opt] --> ID[opt] or chained ID --> ID --> ID
    edge_part_re = re.compile(
        r"([A-Za-z0-9_]+)\s*(?:\[[^\]]*\]|\([^)]*\)|\{[^}]*\})?\s*-->\s*"
    )

    for line in lines[1:]:
        # Skip non-graph lines (classDiagram, subgraph, etc.)
        if "classDiagram" in line.lower() or "subgraph" in line.lower():
            continue
        # Split by arrow to handle chains: A --> B --> C
        parts = re.split(r"\s*-->\s*", line)
        if len(parts) < 2:
            continue
        for i in range(len(parts) - 1):
            src_part = parts[i].strip()
            dst_part = parts[i + 1].strip()
            m_src = node_re.match(src_part)
            m_dst = node_re.match(dst_part)
            if m_src and m_dst:
                src_id, src_label = _extract_node(m_src)
                dst_id, dst_label = _extract_node(m_dst)
                if src_id not in node_labels:
                    node_labels[src_id] = src_label
                if dst_id not in node_labels:
                    node_labels[dst_id] = dst_label
                if src_id != dst_id:
                    edges.append((src_id, dst_id))

    if not node_labels:
        return None

    # Build node list in appearance order (topological-ish for flow)
    seen: set = set()
    ordered: List[str] = []
    for src, dst in edges:
        if src not in seen:
            seen.add(src)
            ordered.append(src)
        if dst not in seen:
            seen.add(dst)
            ordered.append(dst)
    for nid in node_labels:
        if nid not in seen:
            ordered.append(nid)

    nodes = [(nid, node_labels[nid]) for nid in ordered]
    return nodes, edges, layout


def _build_section_scene_class(
    image_path: Path,
    duration: float,
    background_color: Tuple[float, float, float] = (0.07, 0.07, 0.15),
    section_heading: str = "",
    # Timing ratios from settings.yaml
    title_reveal_ratio: float = 0.12,
    content_reveal_ratio: float = 0.25,
    hold_ratio: float = 0.50,
    exit_ratio: float = 0.13,
) -> type:
    """Build a Manim Scene for PNG: title, diagram with Ken Burns, smooth fade out.
    
    CRITICAL: Total animation time MUST equal audio duration exactly.
    """
    _require_manim()

    # Calculate timing phases that sum EXACTLY to duration
    title_fade_time = min(0.5, max(0.25, duration * 0.08))
    diagram_reveal_time = min(0.8, max(0.4, duration * 0.15))
    fade_out_time = min(0.8, max(0.3, duration * 0.12))
    
    # Hold time is whatever is left after reveals and exit
    hold_time = max(0.1, duration - title_fade_time - diagram_reveal_time - fade_out_time)
    
    # Verify total
    total = title_fade_time + diagram_reveal_time + hold_time + fade_out_time
    if abs(total - duration) > 0.1:
        # Adjust hold time to match duration exactly
        hold_time = duration - title_fade_time - diagram_reveal_time - fade_out_time
        hold_time = max(0.05, hold_time)
    
    heading = (section_heading or "Section").strip()[:60]

    class SectionScene(Scene):  # type: ignore[misc]
        def construct(self) -> None:  # type: ignore[override]
            try:
                self.camera.background_color = background_color  # type: ignore[attr-defined]
            except Exception:
                pass

            # Title fades in
            title = Text(heading, font_size=44, color=WHITE).to_edge(UP, buff=0.35)
            self.play(FadeIn(title), run_time=title_fade_time, rate_func=ease_out_sine)

            # Diagram reveals
            diagram = ImageMobject(str(image_path))
            diagram.set_height(5.2)
            diagram.next_to(title, DOWN, buff=0.3)
            self.play(
                GrowFromCenter(diagram),
                run_time=diagram_reveal_time,
                rate_func=ease_out_sine,
            )

            # Hold with subtle Ken Burns (only if enough time)
            if hold_time > 0.3:
                pan = (0.2, 0.15, 0.0)
                self.play(
                    diagram.animate.scale(1.08).shift(pan),
                    run_time=hold_time,
                    rate_func=smooth,
                )
            elif hold_time > 0:
                self.wait(hold_time)

            # Exit: fade out diagram and title together
            self.play(
                FadeOut(diagram),
                FadeOut(title),
                run_time=fade_out_time,
                rate_func=ease_in_out_sine,
            )

    return SectionScene


def _build_graph_scene_class(
    nodes: List[Tuple[str, str]],
    edges: List[Tuple[str, str]],
    duration: float,
    background_color: Tuple[float, float, float] = (0.07, 0.07, 0.15),
    section_heading: str = "",
    layout: str = "TD",
    title_reveal_ratio: float = 0.12,
    content_reveal_ratio: float = 0.25,
    hold_ratio: float = 0.50,
    exit_ratio: float = 0.13,
    min_step_time: float = 0.3,
    max_step_time: float = 0.8,
) -> type:
    """Build a Manim Scene: title → graph build → readable hold → fade out.

    Reserves part of the clip for a **hold** so the full diagram is on screen with the
    voiceover (avoids everything feeling rushed). ``layout`` matches Mermaid TD vs LR.
    """
    _require_manim()

    horizontal = layout.upper() == "LR"

    title_fade_time = min(0.5, max(0.22, duration * max(0.08, title_reveal_ratio)))
    fade_out_time = min(0.65, max(0.26, duration * max(0.09, exit_ratio)))
    mid = max(0.05, duration - title_fade_time - fade_out_time)
    num_nodes = len(nodes)
    num_edges = len(edges)
    total_elements = max(1, num_nodes + num_edges)
    # Spend ~65% of mid on reveals; remaining time is padded with wait() so clip == audio.
    build_budget = mid * (1.0 - min(0.35, max(0.12, hold_ratio * 0.25)))
    per_element_time = max(
        min_step_time,
        min(max_step_time, build_budget / total_elements),
    )

    heading = (section_heading or "Section").strip()[:60]

    class GraphScene(Scene):  # type: ignore[misc]
        def construct(self) -> None:  # type: ignore[override]
            try:
                self.camera.background_color = background_color  # type: ignore[attr-defined]
            except Exception:
                pass

            try:
                title = Text(
                    heading,
                    font_size=46,
                    color=WHITE,
                    stroke_width=2.4,
                    stroke_color=_NODE_TEXT_STROKE,
                ).to_edge(UP, buff=0.3)
            except TypeError:
                title = Text(heading, font_size=46, color=WHITE).to_edge(UP, buff=0.3)
            self.play(FadeIn(title), run_time=title_fade_time, rate_func=ease_out_sine)

            n_n = len(nodes)
            font_size = 32 if n_n <= 4 else 28 if n_n <= 6 else 25
            line_chars = 15 if font_size <= 28 else 16

            node_groups: Dict[str, VGroup] = {}
            node_mobs: List[VGroup] = []
            for node_id, label in nodes:
                group = _build_node_card(label, font_size=font_size, chars_per_line=line_chars)
                node_groups[node_id] = group
                node_mobs.append(group)

            if not node_mobs:
                self.play(FadeOut(title), run_time=fade_out_time)
                return

            if horizontal:
                flow = VGroup(*node_mobs).arrange(RIGHT, buff=0.5)
                flow.next_to(title, DOWN, buff=0.42)
                max_span = 6.72
                if flow.width > max_span:
                    flow.scale(max_span / flow.width)
                    flow.next_to(title, DOWN, buff=0.42)
            else:
                flow = VGroup(*node_mobs).arrange(DOWN, buff=0.58)
                flow.next_to(title, DOWN, buff=0.42)
                max_flow_height = 5.65
                if flow.height > max_flow_height:
                    flow.scale(max_flow_height / flow.height)
                    flow.next_to(title, DOWN, buff=0.42)

            for g in flow:
                g.set_opacity(0.0)
            self.add(flow)

            visible_nodes = set()
            arrows: List[Arrow] = []
            element_time = per_element_time if (num_nodes + num_edges) > 0 else 0.45

            if nodes:
                first_group = node_groups[nodes[0][0]]
                first_group.set_opacity(1.0)
                self.play(
                    GrowFromCenter(first_group),
                    run_time=element_time,
                    rate_func=ease_out_sine,
                )
                visible_nodes.add(nodes[0][0])

            for src_id, dst_id in edges:
                if src_id == dst_id:
                    continue
                if src_id not in node_groups or dst_id not in node_groups:
                    continue
                src_m = node_groups[src_id]
                dst_m = node_groups[dst_id]
                if horizontal:
                    arrow = _flow_arrow(src_m.get_right(), dst_m.get_left(), True)
                else:
                    arrow = _flow_arrow(src_m.get_bottom(), dst_m.get_top(), False)
                try:
                    arrow.set_z_index(5)
                except Exception:
                    pass
                arrows.append(arrow)

                self.play(
                    GrowArrow(arrow),
                    run_time=element_time * 0.58,
                    rate_func=smooth,
                )

                if dst_id not in visible_nodes:
                    self.play(
                        FadeIn(dst_m),
                        run_time=element_time * 0.42,
                        rate_func=ease_out_sine,
                    )
                    visible_nodes.add(dst_id)

            r = getattr(self, "renderer", None)
            elapsed = 0.0
            if r is not None:
                elapsed = float(getattr(r, "time", 0.0))
            if elapsed <= 0.0:
                elapsed = float(getattr(self, "time", 0.0))
            pad = duration - fade_out_time - elapsed
            if pad > 0.02:
                self.wait(pad)

            all_content = VGroup(flow, *arrows)
            self.play(
                FadeOut(all_content),
                FadeOut(title),
                run_time=fade_out_time,
                rate_func=ease_in_out_sine,
            )

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
    section_heading: str = "",
    # Timing configuration
    title_reveal_ratio: float = 0.12,
    content_reveal_ratio: float = 0.25,
    hold_ratio: float = 0.50,
    exit_ratio: float = 0.13,
    min_step_time: float = 0.4,
    max_step_time: float = 1.2,
) -> Path:
    """Render a single section video using Manim and return the resulting mp4 path."""
    _require_manim()

    output_dir.mkdir(parents=True, exist_ok=True)
    bg_float = tuple(c / 255.0 for c in background_color)
    heading = section_heading or f"Section {section_index}"

    scene_cls: type
    parsed_graph: Optional[Tuple[List[Tuple[str, str]], List[Tuple[str, str]], str]] = None
    if mmd_path is not None and mmd_path.exists():
        parsed_graph = _parse_mermaid_graph(mmd_path)

    use_graph = (
        parsed_graph is not None
        and len(parsed_graph[0]) >= 2
        and len(parsed_graph[1]) >= 1
    )

    if use_graph:
        nodes, edges, flow_layout = parsed_graph
        scene_cls = _build_graph_scene_class(
            nodes=nodes,
            edges=edges,
            duration=duration,
            background_color=bg_float,  # type: ignore[arg-type]
            section_heading=heading,
            layout=flow_layout,
            title_reveal_ratio=title_reveal_ratio,
            content_reveal_ratio=content_reveal_ratio,
            hold_ratio=hold_ratio,
            exit_ratio=exit_ratio,
            min_step_time=min_step_time,
            max_step_time=max_step_time,
        )
    elif image_path.exists():
        scene_cls = _build_section_scene_class(
            image_path=image_path,
            duration=duration,
            background_color=bg_float,  # type: ignore[arg-type]
            section_heading=heading,
            title_reveal_ratio=title_reveal_ratio,
            content_reveal_ratio=content_reveal_ratio,
            hold_ratio=hold_ratio,
            exit_ratio=exit_ratio,
        )
    else:
        raise FileNotFoundError(
            f"Section {section_index}: need a parseable graph (2+ nodes, 1+ edge) or images/{image_path.name}. "
            "Run the pipeline with Mermaid→PNG rendering so images/ exists."
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
    # Timing configuration from settings.yaml
    title_reveal_ratio: float = 0.12,
    content_reveal_ratio: float = 0.25,
    hold_ratio: float = 0.50,
    exit_ratio: float = 0.13,
    min_step_time: float = 0.4,
    max_step_time: float = 1.2,
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

        section_heading = _section.get("heading", f"Section {i}")

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
            section_heading=section_heading,
            title_reveal_ratio=title_reveal_ratio,
            content_reveal_ratio=content_reveal_ratio,
            hold_ratio=hold_ratio,
            exit_ratio=exit_ratio,
            min_step_time=min_step_time,
            max_step_time=max_step_time,
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
        audio_bitrate="192k",
        threads=4,
        preset="slow",
        ffmpeg_params=["-crf", "20"],
        logger=None,
    )

    print(f"[Manim] Animated video ready: {final_output}")
    return final_output

