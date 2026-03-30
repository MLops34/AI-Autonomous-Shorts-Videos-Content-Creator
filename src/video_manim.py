"""
Manim-based video assembly — ENHANCED FLOWCHART MODE
Now with **much more specific, dynamic, and visually rich animations** for flowcharts:

- Nodes pop in with a satisfying "grow + bounce" effect
- Arrows are **drawn** progressively (true stroke animation via Create)
- Sequential reveal follows the actual flow (topological order)
- Target node highlights (scale + color flash) when an arrow reaches it
- Subtle "pulse" during the hold phase so the full diagram feels alive
- Perfect sync with TTS audio duration
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Tuple

import json
import re
import textwrap
import logging

from moviepy.editor import (
    AudioFileClip,
    VideoFileClip,
    concatenate_videoclips,
)

# Manim Community Edition
try:
    from manim import (
        Scene,
        ImageMobject,
        FadeIn,
        FadeOut,
        VGroup,
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
        WHITE,
        config,
        tempconfig,
    )
    from manim.utils.rate_functions import smooth, ease_out_sine, ease_in_out_sine, ease_out_bounce, ease_in_out_quad
except ImportError as e:
    Scene = object
    _MANIM_IMPORT_ERROR: Optional[Exception] = e
else:
    _MANIM_IMPORT_ERROR = None

# Visual palette (optimized for vertical shorts)
_ARROW_COLOR = "#4FC3FF"
_NODE_FILL = "#152B45"
_NODE_STROKE = "#7EB6FF"
_NODE_TEXT = "#F5F9FF"
_NODE_TEXT_STROKE = "#071018"
_HIGHLIGHT_COLOR = "#FFDD57"


def _require_manim() -> None:
    if _MANIM_IMPORT_ERROR is not None:
        raise RuntimeError(
            "Manim is not installed.\n"
            "    pip install manim\n"
            "Then restart your environment."
        ) from _MANIM_IMPORT_ERROR


def _wrap_label(raw: str, chars_per_line: int = 16, max_lines: int = 2) -> str:
    t = " ".join((raw or "").split()).strip()
    if not t:
        return "·"
    lines = textwrap.wrap(
        t.replace("\n", " "),
        width=chars_per_line,
        break_long_words=False,
        break_on_hyphens=True,
    )
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1][: chars_per_line - 1].rstrip() + "…"
    return "\n".join(lines)


def _build_node_card(label: str, font_size: int = 32) -> VGroup:
    """Rounded node with multiline text — ready for animation."""
    _require_manim()
    wrapped = _wrap_label(label, chars_per_line=16 if font_size <= 28 else 15)

    txt = Text(
        wrapped,
        font_size=font_size,
        line_spacing=0.55,
        color=_NODE_TEXT,
        stroke_width=1.8,
        stroke_color=_NODE_TEXT_STROKE,
        disable_ligatures=True,
    )

    pad_x, pad_y = 0.65, 0.50
    box_w = max(2.4, min(6.5, float(txt.width) + pad_x))
    box_h = max(0.95, float(txt.height) + pad_y)

    box = RoundedRectangle(
        width=box_w,
        height=box_h,
        corner_radius=0.22,
        fill_color=_NODE_FILL,
        fill_opacity=1.0,
        stroke_color=_NODE_STROKE,
        stroke_width=3.8,
    )

    txt.move_to(box.get_center())
    return VGroup(box, txt)


def _flow_arrow(start, end, horizontal: bool) -> Arrow:
    buff = 0.22 if horizontal else 0.28
    return Arrow(
        start,
        end,
        buff=buff,
        stroke_width=9.5,
        color=_ARROW_COLOR,
        max_tip_length_to_length_ratio=0.24,
    )


# ====================== Mermaid Parsing ======================

def _load_sections(script_path: Path) -> List[Dict]:
    raw = script_path.read_bytes()
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        data = json.loads(raw.decode("utf-8", errors="ignore"))
    return data.get("sections", [])


def _parse_mermaid(mmd_path: Path) -> Optional[Tuple[List[Tuple[str, str]], List[Tuple[str, str]], str]]:
    if not mmd_path.exists():
        return None

    content = mmd_path.read_text(encoding="utf-8", errors="ignore")
    lines = [line.strip() for line in content.splitlines() if line.strip() and not line.strip().startswith("%")]

    if not lines or not (lines[0].lower().startswith(("graph", "flowchart"))):
        return None

    layout = "TD" if " lr" not in lines[0].lower() and " rl" not in lines[0].lower() else "LR"

    node_labels: Dict[str, str] = {}
    edges: List[Tuple[str, str]] = []

    node_re = re.compile(r"([A-Za-z0-9_]+)(?:\s*[\[\(\{]([^}\)\]]*)[\]\)\}])?")

    for line in lines[1:]:
        if any(kw in line.lower() for kw in ("classdiagram", "subgraph", "end")):
            continue
        parts = re.split(r"\s*-->\s*", line)
        if len(parts) < 2:
            continue
        for i in range(len(parts) - 1):
            src_match = node_re.match(parts[i].strip())
            dst_match = node_re.match(parts[i + 1].strip())
            if src_match and dst_match:
                src_id = src_match.group(1)
                dst_id = dst_match.group(1)
                src_label = (src_match.group(2) or src_id).strip()[:80]
                dst_label = (dst_match.group(2) or dst_id).strip()[:80]

                node_labels[src_id] = src_label
                node_labels[dst_id] = dst_label
                if src_id != dst_id:
                    edges.append((src_id, dst_id))

    if not node_labels:
        return None

    # Topological-ish order
    seen = set()
    ordered = []
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


# ====================== ENHANCED GRAPH SCENE ======================

def _build_graph_scene_class(
    nodes: List[Tuple[str, str]],
    edges: List[Tuple[str, str]],
    duration: float,
    section_heading: str = "",
    layout: str = "TD",
) -> type[Scene]:
    _require_manim()
    horizontal = layout.upper() == "LR"

    # Precise timing that sums exactly to audio length
    title_time = max(0.35, min(0.65, duration * 0.10))
    fade_out_time = max(0.45, min(0.95, duration * 0.13))
    mid_time = duration - title_time - fade_out_time

    num_elements = len(nodes) + len(edges)
    build_time = mid_time * 0.68
    per_element = max(0.28, min(0.85, build_time / max(1, num_elements)))

    heading = (section_heading or "Section").strip()[:70]

    class GraphScene(Scene):
        def construct(self):
            self.camera.background_color = "#12122A"  # type: ignore[attr-defined]

            # Title
            title = Text(heading, font_size=46, color=WHITE, stroke_width=2.4, stroke_color=_NODE_TEXT_STROKE)
            title.to_edge(UP, buff=0.35)
            self.play(FadeIn(title, shift=UP * 0.3), run_time=title_time, rate_func=ease_out_sine)

            # Build node cards
            n_n = len(nodes)
            font_size = 32 if n_n <= 4 else 28 if n_n <= 6 else 24
            node_groups: Dict[str, VGroup] = {}
            node_mobs: List[VGroup] = []

            for node_id, label in nodes:
                group = _build_node_card(label, font_size=font_size)
                node_groups[node_id] = group
                node_mobs.append(group)

            if not node_mobs:
                self.play(FadeOut(title), run_time=fade_out_time)
                return

            # Arrange flow (horizontal or vertical)
            flow = VGroup(*node_mobs)
            if horizontal:
                flow.arrange(RIGHT, buff=0.65)
                flow.next_to(title, DOWN, buff=0.45)
                if flow.width > 6.8:
                    flow.scale(6.8 / flow.width)
            else:
                flow.arrange(DOWN, buff=0.75)
                flow.next_to(title, DOWN, buff=0.45)
                if flow.height > 5.7:
                    flow.scale(5.7 / flow.height)

            # Start all nodes invisible
            for g in flow:
                g.scale(0.3).set_opacity(0)
            self.add(flow)

            visible_nodes = set()
            arrows: List[Arrow] = []

            # === DYNAMIC ANIMATION SEQUENCE ===
            # 1. First node pops in
            first_id = nodes[0][0]
            first_node = node_groups[first_id]
            first_node.scale(1).set_opacity(1)
            self.play(
                GrowFromCenter(first_node, rate_func=ease_out_bounce),
                run_time=per_element,
            )
            visible_nodes.add(first_id)

            # 2. Process every edge with full animation
            for src_id, dst_id in edges:
                if src_id == dst_id or src_id not in node_groups or dst_id not in node_groups:
                    continue

                src_m = node_groups[src_id]
                dst_m = node_groups[dst_id]

                # Create arrow
                arrow = _flow_arrow(
                    src_m.get_right() if horizontal else src_m.get_bottom(),
                    dst_m.get_left() if horizontal else dst_m.get_top(),
                    horizontal,
                )
                arrow.set_z_index(5)
                arrows.append(arrow)

                # Draw arrow (true stroke animation)
                self.play(
                    Create(arrow, rate_func=ease_in_out_quad),
                    run_time=per_element * 0.65,
                )

                # Highlight + pop target node if new
                if dst_id not in visible_nodes:
                    dst_m.scale(1).set_opacity(1)
                    self.play(
                        GrowFromCenter(dst_m, rate_func=ease_out_bounce),
                        dst_m.animate.set_fill(_HIGHLIGHT_COLOR, opacity=0.3).set_stroke(_HIGHLIGHT_COLOR, width=5),
                        run_time=per_element * 0.45,
                    )
                    # Reset highlight
                    self.play(
                        dst_m.animate.set_fill(_NODE_FILL).set_stroke(_NODE_STROKE, width=3.8),
                        run_time=0.18,
                    )
                    visible_nodes.add(dst_id)
                else:
                    # Already visible → just a quick pulse
                    self.play(
                        dst_m.animate.scale(1.12).set_fill(_HIGHLIGHT_COLOR, opacity=0.3),
                        rate_func=ease_in_out_quad,
                        run_time=0.22,
                    )
                    self.play(
                        dst_m.animate.scale(1).set_fill(_NODE_FILL).set_stroke(_NODE_STROKE, width=3.8),
                        rate_func=ease_in_out_quad,
                        run_time=0.22,
                    )

            # === HOLD PHASE with subtle pulse ===
            hold_time = mid_time - self.time + title_time  # remaining time
            if hold_time > 0.6:
                self.play(
                    flow.animate.scale(1.04).shift(0.12 * UP),
                    rate_func=smooth,
                    run_time=hold_time * 0.5,
                )
                self.play(
                    flow.animate.scale(1 / 1.04).shift(0.12 * DOWN),
                    rate_func=smooth,
                    run_time=hold_time * 0.5,
                )
            else:
                self.wait(hold_time)

            # === Clean exit ===
            all_content = VGroup(flow, *arrows)
            self.play(
                FadeOut(all_content, shift=DOWN * 0.4),
                FadeOut(title, shift=DOWN * 0.4),
                run_time=fade_out_time,
                rate_func=ease_in_out_sine,
            )

    return GraphScene


# ====================== SIMPLE IMAGE FALLBACK ======================

def _build_section_scene_class(image_path: Path, duration: float, section_heading: str = "") -> type[Scene]:
    _require_manim()

    title_time = max(0.3, min(0.6, duration * 0.10))
    reveal_time = max(0.5, min(1.0, duration * 0.18))
    fade_out_time = max(0.4, min(0.9, duration * 0.13))
    hold_time = max(0.2, duration - title_time - reveal_time - fade_out_time)

    heading = (section_heading or "Section").strip()[:70]

    class SectionScene(Scene):
        def construct(self):
            self.camera.background_color = "#12122A"  # type: ignore[attr-defined]

            title = Text(heading, font_size=46, color=WHITE).to_edge(UP, buff=0.4)
            self.play(FadeIn(title, shift=UP * 0.3), run_time=title_time, rate_func=ease_out_sine)

            diagram = ImageMobject(str(image_path))
            diagram.set_height(5.3)
            diagram.next_to(title, DOWN, buff=0.35)

            self.play(GrowFromCenter(diagram), run_time=reveal_time, rate_func=ease_out_sine)

            if hold_time > 0.4:
                self.play(
                    diagram.animate.scale(1.07).shift(0.18 * RIGHT + 0.12 * UP),
                    run_time=hold_time,
                    rate_func=smooth,
                )
            else:
                self.wait(hold_time)

            self.play(
                FadeOut(diagram),
                FadeOut(title, shift=DOWN * 0.3),
                run_time=fade_out_time,
                rate_func=ease_in_out_sine,
            )

    return SectionScene


# ====================== MAIN ASSEMBLY ======================

def assemble_with_manim(
    run_dir: Path,
    resolution: Tuple[int, int] = (1080, 1920),
    fps: int = 30,
    background_color: Tuple[int, int, int] = (18, 18, 38),
    use_manim_audio: bool = False,
) -> Optional[Path]:
    """Full pipeline — now with **supercharged flowchart animations**."""
    _require_manim()

    run_dir = Path(run_dir).resolve()
    script_path = run_dir / "script.json"
    images_dir = run_dir / "images"
    audio_dir = run_dir / "audio"
    mmd_dir = run_dir / "mmd"
    out_dir = run_dir / "videos_manim"

    if not script_path.exists() or not audio_dir.exists():
        logging.error("Missing script.json or audio/ folder")
        return None

    sections = _load_sections(script_path)
    if not sections:
        logging.warning("No sections in script.json")
        return None

    out_dir.mkdir(parents=True, exist_ok=True)
    clips: List[VideoFileClip] = []

    for i, section in enumerate(sections, 1):
        aud_path = audio_dir / f"section_{i:02d}.mp3"
        img_path = images_dir / f"section_{i:02d}.png"
        mmd_path = mmd_dir / f"section_{i:02d}.mmd"

        if not aud_path.exists():
            continue

        audio = AudioFileClip(str(aud_path))
        duration = float(audio.duration)
        heading = section.get("heading", f"Section {i}")

        print(f"→ Rendering section {i:02d} ({duration:.2f}s) — enhanced flowchart mode")

        parsed = _parse_mermaid(mmd_path) if mmd_path.exists() else None
        use_graph = parsed and len(parsed[0]) >= 2 and len(parsed[1]) >= 1

        if use_graph:
            nodes, edges, flow_layout = parsed
            scene_cls = _build_graph_scene_class(nodes, edges, duration, heading, flow_layout)
        else:
            if not img_path.exists():
                audio.close()
                continue
            scene_cls = _build_section_scene_class(img_path, duration, heading)

        # Render
        scene_name = f"section_{i:02d}"
        cfg = {
            "pixel_width": resolution[0],
            "pixel_height": resolution[1],
            "frame_rate": fps,
            "background_color": tuple(c / 255.0 for c in background_color),
            "output_file": scene_name,
            "video_dir": str(out_dir),
        }

        with tempconfig(cfg):
            scene = scene_cls()
            scene.render(preview=False)

        video_path = out_dir / f"{scene_name}.mp4"
        if not video_path.exists():
            audio.close()
            continue

        vclip = VideoFileClip(str(video_path))
        if not use_manim_audio:
            vclip = vclip.set_audio(audio)

        clips.append(vclip)

    if not clips:
        return None

    print(f"Concatenating {len(clips)} sections...")
    final = concatenate_videoclips(clips, method="chain")
    final_path = out_dir / f"{run_dir.name}_manim_short.mp4"

    final.write_videofile(
        str(final_path),
        fps=fps,
        codec="libx264",
        audio_codec="aac",
        preset="slow",
        threads=6,
        ffmpeg_params=["-crf", "18"],
        logger=None,
    )

    for clip in clips:
        clip.close()

    print(f"✅ Final animated short ready: {final_path}")
    return final_path