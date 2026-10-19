import asyncio, glob, json, os, edge_tts

VOICES = {"A": "en-US-JennyNeural", "B": "en-US-GuyNeural"}

async def main():
    os.makedirs("site/audio", exist_ok=True)
    items = []
    for path in sorted(glob.glob("dialogs/*.txt")):
        name = os.path.splitext(os.path.basename(path))[0]
        lines = []
        with open(path, encoding="utf-8") as f:
            for l in f:
                if ":" in l:
                    spk, text = l.strip().split(":", 1)
                    if spk.strip() in VOICES:
                        lines.append((spk.strip(), text.strip()))
        with open(f"site/audio/{name}.mp3", "wb") as out:
            for spk, text in lines:
                comm = edge_tts.Communicate(text, VOICES[spk])
                async for chunk in comm.stream():
                    if chunk["type"] == "audio":
                        out.write(chunk["data"])
        items.append({
            "title": name.replace("-", " ").title(),
            "audio": f"audio/{name}.mp3",
            "lines": [{"speaker": s, "text": t} for s, t in lines],
        })
    with open("site/dialogs.json", "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)

asyncio.run(main())
