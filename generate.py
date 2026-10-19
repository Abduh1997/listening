import asyncio, glob, json, os, subprocess, edge_tts

VOICES = {"A": "en-US-JennyNeural", "B": "en-US-GuyNeural"}

RATE = "-2%"          # kecepatan: 0% normal, -20% agak pelan, -35% sangat pelan
PAUSE_SECONDS = 0.5    # jeda antar kalimat (detik)
VOLUME = "+0%"

def make_silence(seconds):
    # silence MP3 dengan format sama seperti edge-tts (24 kHz, mono, 48 kbps)
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
        "-t", str(seconds), "-b:a", "48k", "silence.mp3"
    ], check=True)
    with open("silence.mp3", "rb") as f:
        return f.read()

async def main():
    os.makedirs("site/audio", exist_ok=True)
    silence = make_silence(PAUSE_SECONDS)
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
                comm = edge_tts.Communicate(text, VOICES[spk], rate=RATE, volume=VOLUME)
                async for chunk in comm.stream():
                    if chunk["type"] == "audio":
                        out.write(chunk["data"])
                out.write(silence)
        items.append({
            "title": name.replace("-", " ").title(),
            "audio": f"audio/{name}.mp3",
            "lines": [{"speaker": s, "text": t} for s, t in lines],
        })
    with open("site/dialogs.json", "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)

asyncio.run(main())
