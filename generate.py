import asyncio, glob, os, edge_tts

VOICES = {"A": "en-US-JennyNeural", "B": "en-US-GuyNeural"}

async def main():
    os.makedirs("site/audio", exist_ok=True)
    for path in glob.glob("dialogs/*.txt"):
        name = os.path.splitext(os.path.basename(path))[0]
        with open(path, encoding="utf-8") as f:
            lines = [l.strip() for l in f if ":" in l]
        with open(f"site/audio/{name}.mp3", "wb") as out:
            for l in lines:
                spk, text = l.split(":", 1)
                comm = edge_tts.Communicate(text.strip(), VOICES[spk.strip()])
                async for chunk in comm.stream():
                    if chunk["type"] == "audio":
                        out.write(chunk["data"])

asyncio.run(main())
