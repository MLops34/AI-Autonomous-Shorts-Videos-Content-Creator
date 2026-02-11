# AI Autonomous Shorts Videos Content Creator
It is the project where You can Write Topic which generate a Video of 1-2 min of Video but for Selected Content.

## Quick start

1. **Create / activate virtualenv (optional but recommended)**  
   - Windows (PowerShell): `python -m venv .venv` then `.\.venv\Scripts\Activate.ps1`  
   - Or re‑use the existing `001` environment: `.\001\Scripts\Activate.ps1`

2. **Install dependencies**  
   - `pip install -r requirements.txt`

3. **Install Mermaid CLI (for diagram rendering)**  
   - `npm install -g @mermaid-js/mermaid-cli`  
   - This provides the `mmdc` command used in `src/mermaid_renderer.py`.

4. **Configure OpenRouter + DeepSeek**  
   - Create an API key on OpenRouter and set it in your environment:  
     - PowerShell: `$env:OPENROUTER_API_KEY = 'sk-...'`  
   - The default model used here is `deepseek/deepseek-chat`, but you can override it with `--model`.

5. **Run a dry-run (no files created, just checks CLI)**  
   - `python main.py "Explain how database indexes work" --dry-run`

6. **Run the full pipeline**  
   - `python main.py "Explain how database indexes work"`  
   - Outputs (script, diagrams, audio, video) are written under the `outputs/` folder (ignored by git).

## CLI reference

```bash
python main.py "your topic here" \
  --model "deepseek/deepseek-chat" \
  --voice "en-US-GuyNeural" \
  [--dry-run]
```

- `topic` **(required)**: The explainer topic for the YouTube Short.  
- `--model` **(optional)**: OpenRouter model id (e.g. DeepSeek model you use).  
- `--voice` **(optional)**: Edge TTS voice name.  
- `--dry-run` **(optional)**: Only prints what would happen, without running the pipeline.