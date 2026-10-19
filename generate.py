import asyncio, glob, json, os, subprocess, edge_tts

VOICES = {"A": "en-US-JennyNeural", "B": "en-US-GuyNeural"}
RATE = "-2%"
PAUSE_SECONDS = 0.01
VOLUME = "+0%"

def duration(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", path],
        capture_output=True, text=True, check=True)
    return float(r.stdout.strip())

def make_silence(seconds):
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
        "-t", str(seconds), "-c:a", "libmp3lame", "-b:a", "48k", "silence.mp3"
    ], check=True)
    with open("silence.mp3", "rb") as f:
        return f.read(), duration("silence.mp3")

async def synth(text, voice):
    last_err = None
    for attempt in range(1, 5):
        try:
            data = b""
            comm = edge_tts.Communicate(text, voice, rate=RATE, volume=VOLUME)
            async for chunk in comm.stream():
                if chunk["type"] == "audio":
                    data += chunk["data"]
            if data:
                return data
            raise RuntimeError("audio kosong")
        except Exception as e:
            last_err = e
            print(f"Percobaan {attempt} gagal untuk '{text[:40]}': {e}")
            await asyncio.sleep(2 * attempt)
    raise RuntimeError(f"Gagal setelah 4 percobaan: {last_err}")

async def main():
    os.makedirs("site/audio", exist_ok=True)
    silence, sil_dur = make_silence(PAUSE_SECONDS)
    items = []
    for path in sorted(glob.glob("dialogs/*.txt")):
        name = os.path.splitext(os.path.basename(path))[0]
        raw = []
        with open(path, encoding="utf-8") as f:
            for l in f:
                if ":" in l:
                    spk, text = l.strip().split(":", 1)
                    if spk.strip() in VOICES and text.strip():
                        raw.append((spk.strip(), text.strip()))
        print(f"Memproses {name}: {len(raw)} baris")
        lines, t = [], 0.0
        with open(f"site/audio/{name}.mp3", "wb") as out:
            for spk, text in raw:
                data = await synth(text, VOICES[spk])
                with open("seg.mp3", "wb") as sf:
                    sf.write(data)
                d = duration("seg.mp3")
                lines.append({"speaker": spk, "text": text,
                              "start": round(t, 2), "end": round(t + d, 2)})
                out.write(data)
                out.write(silence)
                t += d + sil_dur
        items.append({
            "title": name.replace("-", " ").title(),
            "audio": f"audio/{name}.mp3",
            "total": round(t, 2),
            "lines": lines,
        })
    with open("site/dialogs.json", "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)

asyncio.run(main())
