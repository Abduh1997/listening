"""
generate_shadowing.py  (SHADOWING)
shadowing/<level>/<nama-set>.txt  ->  site/shadowing/audio/<level>-<nama>.mp3
                                      site/shadowing.json

- Suara tutor: LAKI-LAKI saja.
- Set yang isinya tidak berubah tidak diproses ulang (cache).
- Waktu tiap fase (LISTEN / YOUR TURN) dihitung dari sampel audio, jadi
  ucapan, teks, dan giliran penonton sinkron.

Pemakaian:  python generate_shadowing.py
            python generate_shadowing.py --check
"""
import asyncio, glob, json, os, shutil, sys
import ttslib as T

# ------------------------------------------------------------------
# SUARA (laki-laki saja)
# ------------------------------------------------------------------
VOICES = {
    "andrew": ["en-US-AndrewMultilingualNeural", "en-US-GuyNeural"],
    "brian":  ["en-US-BrianMultilingualNeural", "en-US-ChristopherNeural"],
}
DEFAULT_VOICE = "andrew"   # bisa diganti per file dengan baris  @voice=brian
VOLUME = "+0%"

# ------------------------------------------------------------------
# PENGATURAN PER LEVEL
#   rates       : kecepatan tutor tiap putaran (persen, 0 = normal)
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
         "Then repeat it out loud, right after me. Let's start!")
OUTRO = "Great job! Keep practicing every day. See you in the next practice."

GAP_BETWEEN = 0.4     # jeda setelah giliran penonton, sebelum kalimat berikutnya
INTRO_GAP = 0.8       # jeda setelah pembukaan
TURN_TONE = True      # bunyi "ding" pendek sebagai tanda giliran penonton
TRIM_SILENCE = True
SCRIPT_VERSION = "3"

CACHE_OUT = os.path.join(T.CACHE_DIR, "shadowing")
OUT_AUDIO = "site/shadowing/audio"
OUT_JSON = "site/shadowing.json"


def settings_hash():
    return T.sha(SCRIPT_VERSION, VOICES, DEFAULT_VOICE, VOLUME, LEVELS, INTRO, OUTRO,
                 GAP_BETWEEN, INTRO_GAP, TURN_TONE, TRIM_SILENCE)


def speed_label(rate):
    if rate <= -15:
        return "SLOW"
    if rate >= 5:
        return "FAST"
    return "NORMAL"


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


def cache_files(key):
    return (os.path.join(CACHE_OUT, key + ".mp3"), os.path.join(CACHE_OUT, key + ".json"))


def is_cached(key):
    mp3, js = cache_files(key)
    return os.path.exists(mp3) and os.path.exists(js)


def make_plan():
    sh = settings_hash()
    plan = []
    for path in sorted(glob.glob("shadowing/*/*.txt")):
        level = os.path.basename(os.path.dirname(path)).lower()
        if level not in LEVELS:
            T.log(f"Lewati {path}: nama folder level harus salah satu dari {LEVEL_ORDER}")
            continue
        meta, sentences = read_set(path)
        if not sentences:
            continue
        name = os.path.splitext(os.path.basename(path))[0]
        voice = meta["voice"] if meta["voice"] in VOICES else DEFAULT_VOICE
        key = T.sha(sh, level, voice, meta["intro"], meta["outro"],
                    json.dumps(sentences, ensure_ascii=False))
        plan.append({"level": level, "name": name, "meta": meta, "voice": voice,
                     "sentences": sentences, "key": key})
    return plan


async def build_set(p):
    cfg = LEVELS[p["level"]]
    voices = VOICES[p["voice"]]
    meta, sentences = p["meta"], p["sentences"]

    # kumpulkan semua klip yang dibutuhkan
    reqs = {}

    def need(text, rate):
        k = T.tts_key(text, voices, f"{rate:+d}%", "+0Hz", VOLUME)
        reqs[k] = {"text": text, "voices": voices, "rate": f"{rate:+d}%",
                   "pitch": "+0Hz", "volume": VOLUME}
        return k

    intro_k = need(meta["intro"], -5) if meta["intro"] else None
    outro_k = need(meta["outro"], -5) if meta["outro"] else None
    sent_keys = [[need(s, r) for r in cfg["rates"]] for s in sentences]
    await T.fetch_clips(reqs)

    # susun audio (PCM) + garis waktu tepat
    tone = T.tone_pcm() if TURN_TONE else b""
    tone_len = len(tone) / 2 / T.SR
    track = T.Track()
    phases = []

    def add(pcm, ptype, item, label, text):
        s, e = track.add(pcm)
        phases.append({"type": ptype, "item": item, "label": label, "text": text,
                       "start": round(s, 3), "end": round(e, 3)})

    def gap(seconds):
        g = T.silence_pcm(seconds)
        if g:
            track.add(g)

    if intro_k:
        add(T.clip_pcm(intro_k, TRIM_SILENCE), "intro", -1, "", meta["intro"])
        gap(INTRO_GAP)

    for i, sentence in enumerate(sentences):
        for r, rate in enumerate(cfg["rates"]):
            pcm = T.clip_pcm(sent_keys[i][r], TRIM_SILENCE)
            dur = len(pcm) / 2 / T.SR
            label = speed_label(rate)
            add(pcm, "listen", i, label, sentence)
            turn_total = max(cfg["turn_min"], dur * cfg["turn_factor"])
            add(tone + T.silence_pcm(turn_total - tone_len), "turn", i, label, sentence)
        gap(GAP_BETWEEN)

    if outro_k:
        add(T.clip_pcm(outro_k, TRIM_SILENCE), "outro", -1, "", meta["outro"])

    mp3, js = cache_files(p["key"])
    os.makedirs(CACHE_OUT, exist_ok=True)
    T.encode_mp3(track.parts, mp3)
    data = {"total": round(track.seconds(), 3), "phases": phases}
    with open(js, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


async def main():
    plan = make_plan()
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    os.makedirs(OUT_AUDIO, exist_ok=True)
    os.makedirs(CACHE_OUT, exist_ok=True)
    if not plan:
        print("Belum ada file di shadowing/<level>/. Tidak ada yang diproses.")

    sets, failed = [], []
    for p in plan:
        label = f"{p['level']}/{p['name']}"
        try:
            if is_cached(p["key"]):
                print(f"Cache: {label} (tidak diproses ulang)")
            else:
                print(f"Membuat: {label} ({len(p['sentences'])} kalimat)")
                await build_set(p)
            mp3, js = cache_files(p["key"])
            with open(js, encoding="utf-8") as f:
                data = json.load(f)
            out_name = f"{p['level']}-{p['name']}.mp3"
            shutil.copyfile(mp3, os.path.join(OUT_AUDIO, out_name))
            sets.append({
                "level": p["level"],
                "name": p["name"],
                "title": p["meta"]["title"] or p["name"].replace("-", " ").title(),
                "audio": f"shadowing/audio/{out_name}?v={p['key'][:10]}",
                "total": data["total"],
                "items": p["sentences"],
                "phases": data["phases"],
            })
        except Exception as e:
            failed.append(label)
            print(f"::error::Gagal memproses set {label}: {e}")

    used = {p["key"] for p in plan}
    for f in glob.glob(os.path.join(CACHE_OUT, "*")):
        if os.path.splitext(os.path.basename(f))[0] not in used:
            os.remove(f)

    sets.sort(key=lambda s: (LEVEL_ORDER.index(s["level"]), s["name"]))
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(sets, f, ensure_ascii=False, indent=2)
    print(f"Selesai. {len(sets)} set ditulis ke {OUT_JSON}" +
          (f", gagal: {failed}" if failed else ""))


if __name__ == "__main__":
    if "--check" in sys.argv:
        need = any(not is_cached(p["key"]) for p in make_plan())
        print("true" if need else "false")
    else:
        asyncio.run(main())
