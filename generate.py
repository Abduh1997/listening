import asyncio, glob, json, os, random, subprocess, edge_tts

# Suara utama dan cadangan (dicoba berurutan kalau suara utama gagal)
VOICES = {
    "A": ["en-US-AvaMultilingualNeural", "en-US-JennyNeural"],
    "B": ["en-US-AndrewMultilingualNeural", "en-US-GuyNeural"],
}
# Alternatif B yang lebih santai: "en-US-BrianMultilingualNeural"

BASE_RATE = -2                 # persen, 0 = normal
BASE_PITCH = {"A": 2, "B": 0}  # Hz, nada dasar (naik sedikit = lebih ceria)
PAUSE_SECONDS = 0            # jeda saat pembicara berganti
SAME_SPEAKER_PAUSE = 0      # jeda antar kalimat dari pembicara yang sama
VOLUME = "+0%"
VARIATION = 1.5                # 0 = datar, 1 = normal, 1.5 = lebih ekspresif


def duration(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", path],
        capture_output=True, text=True, check=True)
    return float(r.stdout.strip())


_silences = {}

def get_silence(seconds):
    seconds = round(max(0.1, seconds) * 20) / 20  # kelipatan 0.05 detik
    if seconds not in _silences:
        fn = f"silence_{int(seconds * 100)}.mp3"
        subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
            "-t", str(seconds), "-c:a", "libmp3lame", "-b:a", "48k", fn
        ], check=True)
        with open(fn, "rb") as f:
            data = f.read()
        _silences[seconds] = (data, duration(fn))
    return _silences[seconds]


def voice_params(spk, text, rng):
    rate, pitch = BASE_RATE, BASE_PITCH[spk]
    t = text.strip()
    if t.endswith("!"):
        rate += 3
        pitch += 4
    elif t.endswith("?"):
        pitch += 3
    rate += rng.uniform(-3, 3) * VARIATION
    pitch += rng.uniform(-2, 2) * VARIATION
    rate = max(-20, min(10, round(rate)))
    pitch = max(-10, min(12, round(pitch)))
    return f"{rate:+d}%", f"{pitch:+d}Hz"


def pause_after(text, same_next, rng):
    if same_next:
        return SAME_SPEAKER_PAUSE * (1 + rng.uniform(-0.2, 0.2) * VARIATION)
    t = text.strip()
    f = 1.0
    if t.endswith("?"):
        f = 1.6
    elif t.endswith("!"):
        f = 0.9
    f *= 1 + rng.uniform(-0.25, 0.25) * VARIATION
    return PAUSE_SECONDS * f


async def synth(text, spk, rate, pitch):
    last_err = None
    for voice in VOICES[spk]:
        for attempt in range(1, 4):
            try:
                data = b""
                comm = edge_tts.Communicate(text, voice, rate=rate, volume=VOLUME, pitch=pitch)
                async for chunk in comm.stream():
                    if chunk["type"] == "audio":
                        data += chunk["data"]
                if data:
                    return data
                raise RuntimeError("audio kosong")
            except Exception as e:
                last_err = e
                print(f"[{voice}] percobaan {attempt} gagal untuk '{text[:40]}': {e}")
                await asyncio.sleep(2 * attempt)
    raise RuntimeError(f"Gagal semua suara: {last_err}")


async def main():
    os.makedirs("site/audio", exist_ok=True)
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
        rng = random.Random(name)  # variasi tetap sama setiap build
        lines, t = [], 0.0
        with open(f"site/audio/{name}.mp3", "wb") as out:
            for i, (spk, text) in enumerate(raw):
                same_next = i + 1 < len(raw) and raw[i + 1][0] == spk
                rate, pitch = voice_params(spk, text, rng)
                data = await synth(text, spk, rate, pitch)
                with open("seg.mp3", "wb") as sf:
                    sf.write(data)
                d = duration("seg.mp3")
                sil, sd = get_silence(pause_after(text, same_next, rng))
                lines.append({"speaker": spk, "text": text,
                              "start": round(t, 2), "end": round(t + d, 2)})
                out.write(data)
                out.write(sil)
                t += d + sd
        items.append({
            "title": name.replace("-", " ").title(),
            "audio": f"audio/{name}.mp3",
            "total": round(t, 2),
            "lines": lines,
        })
    with open("site/dialogs.json", "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


asyncio.run(main())
