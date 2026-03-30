# src/mermaid_renderer.py
"""Render Mermaid diagrams to PNG with robust Windows support."""

import re
import subprocess
import os
import platform
from pathlib import Path
from typing import List, Optional

# Diagram types Mermaid CLI accepts (first word of the diagram declaration)
MERMAID_DIAGRAM_STARTS = (
    "graph",
    "flowchart",
    "sequenceDiagram",
    "classDiagram",
    "stateDiagram",
    "erDiagram",
    "journey",
    "gantt",
    "pie",
    "mindmap",
    "timeline",
    "blockDiagram",
    "quadrantChart",
)


def ensure_mermaid_has_diagram_type(code: str) -> str:
    """
    Ensure the Mermaid code has a valid diagram type so mmdc does not raise
    UnknownDiagramError. If the code is empty or has no detected type, return
    a minimal valid graph so the section still renders.
    """
    code = code.strip()
    if not code:
        return "graph TD\n    A[Section]"
    first_line = code.split("\n")[0].strip()
    first_word = (first_line.split() or [""])[0].lower()
    if first_word in MERMAID_DIAGRAM_STARTS:
        return code
    # No valid type: use a minimal graph (LLM may have returned explanation text)
    safe = re.sub(r"[\[\]]", " ", code[:60]).strip() or "Section"
    return f"graph TD\n    A[{safe}]"


def find_mmdc() -> Optional[str]:
    """
    Find mmdc executable across platforms.
    Returns full path or command name if found in PATH.
    """
    system = platform.system()
    
    # Windows-specific detection
    if system == "Windows":
        # Common npm global installation paths
        possible_paths = [
            os.path.expandvars(r"%APPDATA%\npm\mmdc.cmd"),
            os.path.expandvars(r"%LOCALAPPDATA%\npm\mmdc.cmd"),
            os.path.expandvars(r"%USERPROFILE%\AppData\Roaming\npm\mmdc.cmd"),
            os.path.expandvars(r"%USERPROFILE%\AppData\Local\npm\mmdc.cmd"),
            r"C:\Program Files\nodejs\mmdc.cmd",
            r"C:\Program Files (x86)\nodejs\mmdc.cmd",
            r"C:\nodejs\mmdc.cmd",
            "mmdc.cmd",  # Try with .cmd extension
            "mmdc",      # Try without extension
        ]
    else:
        # Linux/Mac paths
        possible_paths = [
            os.path.expanduser("~/.npm-global/bin/mmdc"),
            "/usr/local/bin/mmdc",
            "/usr/bin/mmdc",
            "mmdc",
        ]
    
    # Test each path
    for cmd in possible_paths:
        if _test_mmdc(cmd):
            print(f"✅ Found mmdc: {cmd}")
            return cmd
    
    return None


def _test_mmdc(cmd: str) -> bool:
    """Test if mmdc command works."""
    try:
        # On Windows, use shell=True for .cmd files
        use_shell = platform.system() == "Windows" and cmd.endswith(".cmd")
        
        result = subprocess.run(
            [cmd, "--version"],
            capture_output=True,
            text=True,
            shell=use_shell,
            timeout=10,
            check=True
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return False


def render_all_mmd_to_png(
    input_dir: Path,
    output_dir: Path,
    theme: str = "dark",
    background: str = "transparent",
    width: int = 1200,
    scale: float = 2.5,
) -> List[Path]:
    """
    Render all .mmd files to PNG using mermaid-cli.
    Falls back to placeholder if mmdc not available.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    created = []
    
    # Find mmdc executable
    mmdc_cmd = find_mmdc()
    
    if not mmdc_cmd:
        print("⚠️  mmdc (Mermaid CLI) not found — creating placeholder images.")
        print("   Install: npm install -g @mermaid-js/mermaid-cli")
        print("   Or add npm global path to your System PATH")
        
        # Create placeholders for all files
        for mmd_path in input_dir.glob("*.mmd"):
            png_path = output_dir / f"{mmd_path.stem}.png"
            _create_placeholder(png_path, mmd_path.stem)
            created.append(png_path)
            print(f"Placeholder → {png_path}")
        
        return created
    
    # Render with mmdc
    for mmd_path in sorted(input_dir.glob("*.mmd")):
        png_path = output_dir / f"{mmd_path.stem}.png"
        
        cmd = [
            mmdc_cmd,
            "-i", str(mmd_path),
            "-o", str(png_path),
            "--theme", theme,
            "--backgroundColor", background,
            "--width", str(width),
            "--scale", str(scale)
        ]
        
        try:
            use_shell = platform.system() == "Windows" and mmdc_cmd.endswith(".cmd")
            
            result = subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
                shell=use_shell,
                timeout=30
            )
            
            if png_path.exists():
                created.append(png_path)
                print(f"Rendered → {png_path}")
            else:
                print(f"[Failed] {mmd_path.name} - output not created")
                
        except subprocess.TimeoutExpired:
            print(f"[Timeout] {mmd_path.name}")
        except subprocess.CalledProcessError as e:
            stderr = (e.stderr or "")[:300]
            print(f"[mmdc failed] {mmd_path.name}")
            print(f"   Error: {stderr or 'Unknown'}")
            # Retry once after fixing "No diagram type detected"
            if "UnknownDiagramError" in stderr or "No diagram type" in stderr or "detectType" in stderr:
                try:
                    raw = mmd_path.read_text(encoding="utf-8")
                    fixed = ensure_mermaid_has_diagram_type(raw)
                    mmd_path.write_text(fixed, encoding="utf-8")
                    result = subprocess.run(
                        cmd,
                        check=True,
                        capture_output=True,
                        text=True,
                        shell=use_shell,
                        timeout=30,
                    )
                    if png_path.exists():
                        created.append(png_path)
                        print(f"Rendered (after fix) → {png_path}")
                except Exception:
                    pass
            if png_path not in created and not png_path.exists():
                _create_placeholder(png_path, mmd_path.stem)
                if png_path.exists():
                    created.append(png_path)
    
    return created


def _create_placeholder(png_path: Path, title: str) -> None:
    """Create a placeholder PNG with text."""
    try:
        from PIL import Image, ImageDraw, ImageFont
        
        # Create 1080x1920 vertical image
        img = Image.new("RGB", (1080, 1920), color=(18, 18, 38))
        draw = ImageDraw.Draw(img)
        
        # Text to display
        text = f"[Diagram]\n{title.replace('_', ' ').title()}\n\n[Install mermaid-cli\nfor real diagrams]"
        
        # Try to load a font, fallback to default
        try:
            # Try system fonts
            font_paths = [
                "arial.ttf",
                "Arial.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",  # Linux
                "/System/Library/Fonts/Helvetica.ttc",  # Mac
            ]
            font = None
            for fp in font_paths:
                try:
                    font = ImageFont.truetype(fp, 60)
                    break
                except:
                    continue
            
            if not font:
                font = ImageFont.load_default()
        except:
            font = ImageFont.load_default()
        
        # Calculate text position (center)
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        
        x = (1080 - text_width) // 2
        y = (1920 - text_height) // 2
        
        # Draw text
        draw.text((x, y), text, fill=(255, 255, 255), font=font, align="center")
        
        # Save
        img.save(png_path)
        
    except ImportError:
        # PIL not available, create empty file
        png_path.touch()
        print(f"Empty placeholder (PIL not installed) → {png_path}")
    except Exception as e:
        print(f"Placeholder creation failed: {e}")
        png_path.touch()  # At least create the file