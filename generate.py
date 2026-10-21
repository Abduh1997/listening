"""
generate.py  (PODCAST)
Dialog di dialogs/*.txt  ->  site/audio/<nama>.mp3  +  site/dialogs.json

Versi cepat:
- Dialog yang isinya tidak berubah TIDAK diproses ulang (diambil dari cache).
- Dialog baru atau yang diedit: hanya kalimat baru/berubah yang dibuat suaranya,
  dan dibuat paralel.
- Waktu tiap kalimat dihitung dari sampel audio, jadi subtitle sinkron.

Pemakaian:  python generate.py           (membuat semuanya)
            python generate.py --check   (hanya mencetak true/false: ada yang perlu dibuat?)
"""
import asyncio, glob, json, os, random, re, shutil, sys
import ttslib as T

# ------------------------------------------------------------------
# PENGATURAN
# ------------------------------------------------------------------
VOICES = {
    "A": ["en-US-AvaMultilingualNeural", "en-US-JennyNeural"],
    "B": ["en-US-AndrewMultilingualNeural", "en-US-GuyNeural"],
}
BASE_RATE = -2                 # persen, 0 = normal
BASE_PITCH = {"A": 2, "B": 0}  # Hz, nada dasar (naik sedikit = lebih ceria)
PAUSE_SECONDS = 0.15           # jeda saat pembicara berganti (0 = tanpa jeda)
SAME_SPEAKER_PAUSE = 0.05      # jeda antar kalimat dari pembicara yang sama
VOLUME = "+0%"
VARIATION = 1.0                # 0 = datar, 1 = normal, 1.5 = lebih ekspresif
TRIM_SILENCE = True            # potong keheningan di awal/akhir tiap klip
CLEAN_COMMAS = True            # hapus koma yang membuat jeda tidak natural
SCRIPT_VERSION = "3"           # naikkan angka ini kalau logika skrip diubah

CACHE_OUT = os.path.join(T.CACHE_DIR, "podcast")


def settings_hash():
    return T.sha(SCRIPT_VERSION, VOICES, BASE_RATE, BASE_PITCH, PAUSE_SECONDS,
                 SAME_SPEAKER_PAUSE, VOLUME, VARIATION, TRIM_SILENCE, CLEAN_COMMAS)


# ------------------------------------------------------------------
# TEKS DAN PARAMETER SUARA
# ------------------------------------------------------------------
def clean_text(text):
    """Hapus koma yang membuat suara berhenti tidak natural (hanya untuk suara)."""
    if not CLEAN_COMMAS:
        return text
    t = re.sub(r"\b(say|says|said|is|are|means|answer|try|ask|call|called)\s*,\s+",
               r"\1 ", text, flags=re.I)
    t = re.sub(r",\s+(and then|or)\b", r" \1", t, flags=re.I)
    t = re.sub(r"\b(or|and then)\s*,\s+", r"\1 ", t, flags=re.I)
    return re.sub(r"\s{2,}", " ", t).strip()


def voice_params(spk, text):
    """Variasi dibuat dari teks itu sendiri, jadi kalimat yang sama selalu terdengar sama."""
    rng = random.Random(T.sha("v", spk, text))
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


def pause_after(text, same_next):
    rng = random.Random(T.sha("p", text))
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


def parse_dialog(path):
    raw = []
    with open(path, encoding="utf-8") as f:
        for l in f:
            if ":" in l:
                spk, text = l.strip().split(":", 1)
                if spk.strip() in VOICES and text.strip():
                    raw.append((spk.strip(), text.strip()))
    return raw


# ------------------------------------------------------------------
# MEMBUAT SATU DIALOG
# ------------------------------------------------------------------
def cache_files(key):
    return (os.path.join(CACHE_OUT, key + ".mp3"), os.path.join(CACHE_OUT, key + ".json"))


def is_cached(key):
    mp3, js = cache_files(key)
    return os.path.exists(mp3) and os.path.exists(js)


async def build_dialog(name, raw, key):
    reqs, keys = {}, []
    for spk, text in raw:
        rate, pitch = voice_params(spk, text)
        ct = clean_text(text)
        k = T.tts_key(ct, VOICES[spk], rate, pitch, VOLUME)
        keys.append(k)
        reqs[k] = {"text": ct, "voices": VOICES[spk], "rate": rate, "pitch": pitch, "volume": VOLUME}
    await T.fetch_clips(reqs)

    track = T.Track()
    lines = []
    for i, (spk, text) in enumerate(raw):
        same_next = i + 1 < len(raw) and raw[i + 1][0] == spk
        start, end = track.add(T.clip_pcm(keys[i], TRIM_SILENCE))
        lines.append({"speaker": spk, "text": text,
                      "start": round(start, 3), "end": round(end, 3)})
        gap = T.silence_pcm(pause_after(text, same_next))
        if gap:
            track.add(gap)

    mp3, js = cache_files(key)
    os.makedirs(CACHE_OUT, exist_ok=True)
    T.encode_mp3(track.parts, mp3)
    meta = {"total": round(track.seconds(), 3), "lines": lines}
    with open(js, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)
    return meta


def make_plan():
    sh = settings_hash()
    plan = []
    for path in sorted(glob.glob("dialogs/*.txt")):
        name = os.path.splitext(os.path.basename(path))[0]
        raw = parse_dialog(path)
        if not raw:
            continue
        key = T.sha(sh, json.dumps(raw, ensure_ascii=False))
        plan.append({"name": name, "raw": raw, "key": key})
    return plan


async def main():
    plan = make_plan()
    os.makedirs("site/audio", exist_ok=True)
    os.makedirs(CACHE_OUT, exist_ok=True)
    items, failed = [], []

    for p in plan:
        name, key = p["name"], p["key"]
        try:
            if is_cached(key):
                print(f"Cache: {name} (tidak diproses ulang)")
            else:
                print(f"Membuat: {name} ({len(p['raw'])} baris)")
                await build_dialog(name, p["raw"], key)
            mp3, js = cache_files(key)
            with open(js, encoding="utf-8") as f:
                meta = json.load(f)
            shutil.copyfile(mp3, f"site/audio/{name}.mp3")
            items.append({
                "title": name.replace("-", " ").title(),
                "audio": f"audio/{name}.mp3",
                "total": meta["total"],
                "lines": meta["lines"],
                "v": key[:10],
            })
        except Exception as e:
            failed.append(name)
            print(f"::error::Gagal memproses dialog {name}: {e}")

    # bersihkan hasil lama (versi dialog yang sudah tidak dipakai)
    used = {p["key"] for p in plan}
    for f in glob.glob(os.path.join(CACHE_OUT, "*")):
        if os.path.splitext(os.path.basename(f))[0] not in used:
            os.remove(f)

    with open("site/dialogs.json", "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    print(f"Selesai. {len(items)} dialog ditulis ke site/dialogs.json" +
          (f", gagal: {failed}" if failed else ""))


if __name__ == "__main__":
    if "--check" in sys.argv:
        need = any(not is_cached(p["key"]) for p in make_plan())
        print("true" if need else "false")
    else:
        asyncio.run(main())
