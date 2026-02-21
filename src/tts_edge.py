# src/tts_edge.py
import asyncio
import edge_tts
from pathlib import Path
from typing import List, Dict

async def generate_one_audio(
    text: str,
    output_file: Path,
    voice: str = "en-US-GuyNeural",
    rate: str = "+0%"
) -> bool:
    try:
        communicate = edge_tts.Communicate(text, voice, rate=rate)
        await communicate.save(str(output_file))
        print(f"Audio saved → {output_file}")
        return True
    except Exception as e:
        print(f"[TTS error] {e}")
        return False


async def generate_section_audios(
    sections: List[Dict[str, str]],
    output_folder: Path,
    voice: str = "en-US-GuyNeural"
) -> List[Path]:
    output_folder.mkdir(parents=True, exist_ok=True)
    tasks = []
    created = []

    for i, section in enumerate(sections, 1):
        text = section.get("text", section.get("narration", "")).strip()
        if not text:
            continue
        # Zero-padded to match Mermaid image naming (section_01.png, section_01.mp3)
        outfile = output_folder / f"section_{i:02d}.mp3"
        tasks.append(generate_one_audio(text, outfile, voice))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    for i, result in enumerate(results, 1):
        if isinstance(result, Exception):
            print(f"Section {i} failed: {result}")
        else:
            created.append(output_folder / f"section_{i:02d}.mp3")

    return created