# main.py
import argparse
import sys
from pathlib import Path
import asyncio

from src.pipeline_runner import main_entry

def main():
    parser = argparse.ArgumentParser(
        description="Generate a YouTube Short using local LLM + Mermaid + TTS + MoviePy"
    )
    parser.add_argument(
        "topic",
        type=str,
        help="The topic for the explainer short (e.g. 'how database indexes work')"
    )
    parser.add_argument(
        "--model",
        type=str,
        default="deepseek/deepseek-chat",
        help="OpenRouter model id (e.g. 'deepseek/deepseek-chat')"
    )
    parser.add_argument(
        "--voice",
        type=str,
        default="en-US-GuyNeural",
        help="edge-tts voice name"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would happen without running the pipeline"
    )

    args = parser.parse_args()

    # Basic validation
    if not args.topic.strip():
        print("Error: Please provide a topic.")
        sys.exit(1)

    print("=" * 60)
    print(f" Starting Shorts Pipeline ")
    print(f" Topic     : {args.topic}")
    print(f" Model     : {args.model}")
    print(f" Voice     : {args.voice}")
    print("=" * 60)

    if args.dry_run:
        print("\nDry run mode — no files will be created or processed.")
        print("Would run pipeline for topic:", args.topic)
        sys.exit(0)

    try:
        # Run the full pipeline (sync wrapper around async pipeline)
        main_entry(
            topic=args.topic,
            config_folder=Path("config"),
            outputs_base=Path("outputs"),
            model=args.model,
        )
    except KeyboardInterrupt:
        print("\nStopped by user.")
    except Exception as e:
        print(f"\nPipeline failed: {e}")
        sys.exit(1)

    print("\n" + "=" * 60)
    print("Pipeline finished successfully!")
    print("Check the 'outputs/' folder for script, images, audio and video.")
    print("=" * 60)


if __name__ == "__main__":
    main()