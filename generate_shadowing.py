"""
generate_shadowing.py
Membuat audio latihan SHADOWING (tutor bicara, lalu giliran penonton menirukan).

Terpisah dari generate.py (podcast), jadi tidak mengganggu konten sebelumnya.

Input  : shadowing/<level>/<nama-set>.txt   (level = a1, a2, b1, b2, c1, c2)
Output : site/shadowing/audio/<level>-<nama-set>.mp3
         site/shadowing.json   (daftar set + waktu tiap fase untuk video)
"""
import asyncio, glob, json, os, re, subprocess, sys, edge_tts

# ------------------------------------------------------------------
# PENGATURAN SUARA
# ------------------------------------------------------------------
VOICES = {
    "female": ["en-US-AvaMultilingualNeural", "en-US-JennyNeural"],
    "male":   ["en-US-AndrewMultilingualNeural", "en-US-GuyNeural"],
}
DEFAULT_VOICE = "female"   # bisa diganti per file dengan baris  @voice=male
VOLUME = "+0%"

# ------------------------------------------------------------------
# PENGATURAN PER LEVEL
#   rates       : kecepatan tutor tiap putaran (persen, 0 = normal).
#                 Contoh [-25, -8] = putaran 1 pelan, putaran 2 hampir normal.
#   turn_factor : lama giliran penonton = durasi kalimat x angka ini
#   turn_min    : giliran penonton minimal (detik)
# ------------------------------------------------------------------
LEVELS = {
    "a1": {"rates": [-25, -8], "turn_factor": 1.7, "turn_min": 2.0},
    "a2": {"rates": [-15, 0],  "turn_factor": 1.6, "turn_min": 2.0},
    "b1": {"rates": [-5, 5],   "turn_factor": 1.5, "turn_min": 2.2},
    "b2": {"rates": [0],       "turn_factor": 1.5, "turn_min": 2.4},
    "c1": {"rates": [5],       "turn_factor": 1.5, "turn_min": 2.6},
    "c2": {"rates": [10],      "turn_factor": 1.5, "turn_min": 2.8},
}
LEVEL_ORDER = ["a1", "a2", "b1", "b2", "c1", "c2"]

INTRO = ("Welcome to shadowing practice. Listen to each sentence. "
         "Then repeat it out loud, right after the tutor. Let's start!")
OUTRO = "Great job! Keep practicing every day. See you in the next practice."

GAP_BETWEEN = 0.4     # jeda setelah giliran penonton, sebelum kalimat berikutnya
TURN_TONE = True      # bunyi "ding" pendek sebagai tanda giliran penonton
TRIM_SILENCE = True   # potong keheningan di awal/akhir tiap klip suara

OUT_DIR = "site/shadowing/audio"
OUT_JSON = "site/shadowing.json"


# ------------------------------------------------------------------
# ALAT BANTU AUDIO
# ------------------------------------------------------------------
def duration(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", path],
        capture_output=True, text=True, check=True)
    return float(r.stdout.strip())


_silences = {}

def get_silence(seconds):
    """Keheningan dalam MP3 (kelipatan 0.05 detik). Mengembalikan (bytes, durasi)."""
    if seconds < 0.03:
        return b"", 0.0
    seconds = max(0.05, round(seconds * 20) / 20)
    if seconds not in _silences:
        fn = f"tmp_silence_{int(seconds * 100)}.mp3"
        subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
            "-t", str(seconds), "-c:a", "libmp3lame", "-b:a", "48k",
            "-write_xing", "0", fn
        ], check=True)
        with open(fn, "rb") as f:
            data = f.read()
        _silences[seconds] = (data, duration(fn))
    return _silences[seconds]


def make_tone():
    """Bunyi 'ding' pendek. Mengembalikan (bytes, durasi)."""
    if not TURN_TONE:
        return b"", 0.0
    fn = "tmp_tone.mp3"
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", "sine=frequency=880:duration=0.14:sample_rate=24000",
        "-af", "volume=0.25,afade=t=out:st=0.08:d=0.06",
        "-ac", "1", "-c:a", "libmp3lame", "-b:a", "48k", "-write_xing", "0", fn
    ], check=True)
    with open(fn, "rb") as f:
        data = f.read()
    return data, duration(fn)


def trim_silence(data):
    if not TRIM_SILENCE:
        return data
    try:
        with open("tmp_raw.mp3", "wb") as f:
            f.write(data)
        subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error", "-i", "tmp_raw.mp3",
            "-af",
            "silenceremove=start_periods=1:start_silence=0.03:start_threshold=-50dB,"
            "areverse,"
            "silenceremove=start_periods=1:start_silence=0.05:start_threshold=-50dB,"
            "areverse",
            "-ar", "24000", "-ac", "1", "-c:a", "libmp3lame", "-b:a", "48k",
            "-write_xing", "0", "tmp_trimmed.mp3"
        ], check=True)
        with open("tmp_trimmed.mp3", "rb") as f:
            out = f.read()
        return out if len(out) > 500 else data
    except Exception as e:
        print("WARNING: gagal memotong keheningan, memakai audio asli:", e)
        return data


async def synth(text, voice_key, rate):
    last_err = None
    for voice in VOICES[voice_key]:
        for attempt in range(1, 4):
            try:
                data = b""
                comm = edge_tts.Communicate(text, voice, rate=f"{rate:+d}%", volume=VOLUME)
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


async def tts_clip(text, voice_key, rate):
    """Satu klip suara tutor (sudah dipotong). Mengembalikan (bytes, durasi)."""
    data = trim_silence(await synth(text, voice_key, rate))
    with open("tmp_seg.mp3", "wb") as f:
        f.write(data)
    return data, duration("tmp_seg.mp3")


def speed_label(rate):
    if rate <= -15:
        return "SLOW"
    if rate >= 5:
        return "FAST"
    return "NORMAL"


# ------------------------------------------------------------------
# MEMBUAT SATU SET
# ------------------------------------------------------------------
def read_set(path):
    meta = {"voice": DEFAULT_VOICE, "title": None, "intro": INTRO, "outro": OUTRO}
    sentences = []
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("@"):
                key, _, val = line[1:].partition("=")
                meta[key.strip().lower()] = val.strip()
                continue
            sentences.append(line)
    return meta, sentences


async def build_set(path, level, tone, tone_dur):
    cfg = LEVELS[level]
    meta, sentences = read_set(path)
    if not sentences:
        print(f"Lewati {path}: tidak ada kalimat")
        return None

    name = os.path.splitext(os.path.basename(path))[0]
    voice_key = meta["voice"] if meta["voice"] in VOICES else DEFAULT_VOICE
    title = meta["title"] or name.replace("-", " ").title()
    print(f"Memproses {level}/{name}: {len(sentences)} kalimat")

    chunks, phases = [], []
    t = 0.0

    def add_audio(data, dur, ptype, item, label, text):
        nonlocal t
        chunks.append(data)
        phases.append({"type": ptype, "item": item, "label": label, "text": text,
                       "start": round(t, 2), "end": round(t + dur, 2)})
        t += dur

    def add_gap(seconds):
        nonlocal t
        sil, sd = get_silence(seconds)
        if sd > 0:
            chunks.append(sil)
            t += sd

    # pembukaan
    if meta["intro"]:
        data, dur = await tts_clip(meta["intro"], voice_key, -5)
        add_audio(data, dur, "intro", -1, "", meta["intro"])
        add_gap(0.8)

    # kalimat latihan
    for i, sentence in enumerate(sentences):
        for rate in cfg["rates"]:
            data, dur = await tts_clip(sentence, voice_key, rate)
            label = speed_label(rate)
            add_audio(data, dur, "listen", i, label, sentence)

            # giliran penonton: ding + keheningan
            turn_total = max(cfg["turn_min"], dur * cfg["turn_factor"])
            sil, sd = get_silence(turn_total - tone_dur)
            turn_data = tone + sil
            turn_dur = tone_dur + sd
            add_audio(turn_data, turn_dur, "turn", i, label, sentence)
        add_gap(GAP_BETWEEN)

    # penutup
    if meta["outro"]:
        data, dur = await tts_clip(meta["outro"], voice_key, -5)
        add_audio(data, dur, "outro", -1, "", meta["outro"])

    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = f"{OUT_DIR}/{level}-{name}.mp3"
    with open(out_path, "wb") as f:
        for c in chunks:
            f.write(c)

    return {
        "level": level,
        "name": name,
        "title": title,
        "audio": f"shadowing/audio/{level}-{name}.mp3",
        "total": round(t, 2),
        "items": sentences,
        "phases": phases,
    }


async def main():
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    sets = []
    files = sorted(glob.glob("shadowing/*/*.txt"))
    if not files:
        print("Belum ada file di shadowing/<level>/. Tidak ada yang diproses.")
    else:
        tone, tone_dur = make_tone()
        for path in files:
            level = os.path.basename(os.path.dirname(path)).lower()
            if level not in LEVELS:
                print(f"Lewati {path}: nama folder level harus salah satu dari {LEVEL_ORDER}")
                continue
            try:
                s = await build_set(path, level, tone, tone_dur)
                if s:
                    sets.append(s)
            except Exception as e:
                print(f"GAGAL memproses {path}: {e}")

    sets.sort(key=lambda s: (LEVEL_ORDER.index(s["level"]), s["name"]))
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(sets, f, ensure_ascii=False, indent=2)
    print(f"Selesai. {len(sets)} set ditulis ke {OUT_JSON}")


if __name__ == "__main__":
    asyncio.run(main())
