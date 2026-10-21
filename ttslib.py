"""
ttslib.py
Alat bersama untuk generate.py (podcast) dan generate_shadowing.py (shadowing).

Yang dikerjakan file ini:
- Cache: klip suara yang sudah pernah dibuat tidak dibuat ulang.
- Pembuatan klip secara paralel (lebih cepat saat ada dialog baru).
- Penyusunan audio dengan PCM, sehingga waktu tiap kalimat tepat
  (hitungan sampel, bukan perkiraan ffprobe). Ini yang membuat teks,
  suara, dan giliran penonton sinkron.
"""
import asyncio, hashlib, math, os, subprocess, sys
from array import array

SR = 24000                      # sample rate semua audio
CACHE_DIR = ".cache"
TTS_DIR = os.path.join(CACHE_DIR, "tts")   # klip mentah dari edge-tts (MP3 kecil)


def log(*args):
    print(*args, file=sys.stderr, flush=True)


def sha(*parts):
    h = hashlib.sha1()
    for p in parts:
        h.update(str(p).encode("utf-8"))
        h.update(b"\x1f")
    return h.hexdigest()


# ------------------------------------------------------------------
# KLIP SUARA (edge-tts) DENGAN CACHE
# ------------------------------------------------------------------
def tts_key(text, voices, rate, pitch, volume):
    return sha("tts", ",".join(voices), rate, pitch, volume, text)


def tts_path(key):
    return os.path.join(TTS_DIR, key + ".mp3")


async def _synth(text, voices, rate, pitch, volume):
    import edge_tts  # diimpor di sini supaya mode --check tidak butuh edge-tts
    last_err = None
    for voice in voices:
        for attempt in range(1, 4):
            try:
                data = b""
                comm = edge_tts.Communicate(text, voice, rate=rate, volume=volume, pitch=pitch)
                async for chunk in comm.stream():
                    if chunk["type"] == "audio":
                        data += chunk["data"]
                if data:
                    return data
                raise RuntimeError("audio kosong")
            except Exception as e:
                last_err = e
                log(f"[{voice}] percobaan {attempt} gagal untuk '{text[:40]}': {e}")
                await asyncio.sleep(2 * attempt)
    raise RuntimeError(f"Gagal semua suara: {last_err}")


async def fetch_clips(reqs, concurrency=3):
    """
    reqs: {key: {"text","voices","rate","pitch","volume"}}
    Hanya klip yang belum ada di cache yang dibuat, maksimal 'concurrency' sekaligus.
    Mengembalikan jumlah klip baru.
    """
    os.makedirs(TTS_DIR, exist_ok=True)
    missing = [k for k in reqs if not os.path.exists(tts_path(k))]
    if not missing:
        return 0
    log(f"  {len(missing)} klip baru dibuat, {len(reqs) - len(missing)} dari cache")
    sem = asyncio.Semaphore(concurrency)
    errors = []

    async def one(k):
        r = reqs[k]
        async with sem:
            try:
                data = await _synth(r["text"], r["voices"], r["rate"], r["pitch"], r["volume"])
                tmp = tts_path(k) + ".part"
                with open(tmp, "wb") as f:
                    f.write(data)
                os.replace(tmp, tts_path(k))
            except Exception as e:
                errors.append(e)

    await asyncio.gather(*(one(k) for k in missing))
    if errors:
        raise errors[0]
    return len(missing)


# ------------------------------------------------------------------
# PCM: DECODE, POTONG KEHENINGAN, KEHENINGAN, NADA
# ------------------------------------------------------------------
_TRIM_AF = ("silenceremove=start_periods=1:start_silence=0.03:start_threshold=-50dB,"
            "areverse,"
            "silenceremove=start_periods=1:start_silence=0.05:start_threshold=-50dB,"
            "areverse")


def decode_pcm(mp3_bytes, trim=True):
    cmd = ["ffmpeg", "-v", "error", "-i", "pipe:0"]
    if trim:
        cmd += ["-af", _TRIM_AF]
    cmd += ["-f", "s16le", "-ar", str(SR), "-ac", "1", "pipe:1"]
    out = subprocess.run(cmd, input=mp3_bytes, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, check=True).stdout
    if trim and len(out) < 2 * int(0.05 * SR):
        return decode_pcm(mp3_bytes, trim=False)   # klip terlalu "sunyi", pakai apa adanya
    return out


_pcm_memo = {}

def clip_pcm(key, trim=True):
    mk = (key, trim)
    if mk not in _pcm_memo:
        with open(tts_path(key), "rb") as f:
            _pcm_memo[mk] = decode_pcm(f.read(), trim)
    return _pcm_memo[mk]


def silence_pcm(seconds):
    n = int(round(seconds * SR))
    return b"\x00\x00" * n if n > 0 else b""


def tone_pcm(freq=880, dur=0.14, vol=0.25):
    n = int(dur * SR)
    fade_from = int(n * 0.55)
    a = array("h")
    for i in range(n):
        env = 1.0 if i < fade_from else (n - i) / max(1, n - fade_from)
        a.append(int(32767 * vol * env * math.sin(2 * math.pi * freq * i / SR)))
    if sys.byteorder == "big":
        a.byteswap()
    return a.tobytes()


class Track:
    """Susunan audio (PCM). Semua waktu dihitung dari jumlah sampel, jadi tepat."""
    def __init__(self):
        self.parts = []
        self.samples = 0

    def add(self, pcm):
        if len(pcm) % 2:
            pcm += b"\x00"
        start = self.samples / SR
        self.parts.append(pcm)
        self.samples += len(pcm) // 2
        return start, self.samples / SR

    def seconds(self):
        return self.samples / SR


def encode_mp3(parts, out_path, bitrate="64k"):
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "s16le", "-ar", str(SR), "-ac", "1",
           "-i", "pipe:0", "-c:a", "libmp3lame", "-b:a", bitrate, out_path]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for part in parts:
            p.stdin.write(part)
    finally:
        p.stdin.close()
    err = p.stderr.read().decode("utf-8", "ignore")
    if p.wait() != 0:
        raise RuntimeError("ffmpeg gagal membuat MP3: " + err)
