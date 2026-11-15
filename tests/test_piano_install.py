"""The one-click Transkun install: asks before anything is downloaded, remembers a no, downloads only from
Hugging Face, checks every file's size + SHA-256, keeps nothing from a bad download. No network: a fake opener
serves the files (from the developer add-on folder when present, else a fake manifest)."""
import hashlib, io, json, os, sys, tempfile
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
TMP = tempfile.mkdtemp()
os.environ["LOCALAPPDATA"] = TMP                         # user_dir() lives here
os.environ["FLH_ROOTS"] = TMP
os.environ["FLH_UPDATE_CHECK"] = "0"
os.environ.pop("FLH_TRANSKUN_DIR", None)
import fl_piano_tk as K  # noqa: E402
K._candidates = lambda data_dir=None: [data_dir] if data_dir else [K.user_dir()]   # ignore a developer copy

# host rule
assert K._host_ok("https://huggingface.co/x") and K._host_ok("https://us.aws.cdn.hf.co/y")
for bad in ("http://huggingface.co/x", "https://evil.com/huggingface.co", "https://xhf.co/", "https://hf.co.evil.com/",
            "https://user:pw@huggingface.co/x", "https://huggingface.co:8443/x"):
    assert not K._host_ok(bad), bad
assert K.file_url("LICENSE.transkun").endswith(f"/resolve/{K.REVISION}/LICENSE")

# a fake manifest + server
files = {n: os.urandom(1000 + i) for i, n in enumerate(K.MANIFEST)}
K.MANIFEST = {n: (len(b), hashlib.sha256(b).hexdigest()) for n, b in files.items()}


class Resp(io.BytesIO):
    def __init__(self, data, url):
        super().__init__(data)
        self.url = url
    def geturl(self):
        return self.url
    def __enter__(self):
        return self
    def __exit__(self, *a):
        self.close()


class Opener:
    def __init__(self, serve, final_host="us.aws.cdn.hf.co"):
        self.serve, self.final_host, self.urls = serve, final_host, []
    def open(self, req, timeout=None):
        url = req.full_url
        self.urls.append(url)
        name = url.rsplit("/", 1)[-1]
        name = {"LICENSE": "LICENSE.transkun"}.get(name, name)
        return Resp(self.serve(name), f"https://{self.final_host}/{name}")


dest = os.path.join(TMP, "m1")
# a tampered file: refused, nothing of it kept
try:
    K.install(dest, opener=Opener(lambda n: files[n] if n != "windows.f32" else b"x" * len(files[n])))
    raise AssertionError("tampered file accepted")
except ValueError as e:
    assert "fingerprint" in str(e)
assert not os.path.exists(os.path.join(dest, "windows.f32")) and not any(f.endswith(".part") for f in os.listdir(dest))
# too big: refused
try:
    K.install(dest, opener=Opener(lambda n: files[n] + b"!"))
    raise AssertionError("oversized file accepted")
except ValueError as e:
    assert "bigger" in str(e)
# a response that ends up off Hugging Face: refused
try:
    K.install(os.path.join(TMP, "m2"), opener=Opener(lambda n: files[n], final_host="evil.example"))
    raise AssertionError("off-host response accepted")
except ValueError as e:
    assert "off Hugging Face" in str(e)
# good: every file in place, all requests to the pinned revision on huggingface.co
op = Opener(lambda n: files[n])
r = K.install(dest, opener=op)
assert r["files"] == len(files) and all(open(os.path.join(dest, n), "rb").read() == b for n, b in files.items())
assert all(u.startswith(f"https://huggingface.co/{K.REPO}/resolve/{K.REVISION}/") for u in op.urls)
# a second install skips intact files
op2 = Opener(lambda n: files[n])
K.install(dest, opener=op2)
assert op2.urls == []
# choice memory
assert not K.declined()
K.remember_choice(True); assert K.declined()
K.remember_choice(False); assert not K.declined()

# audio_to_midi asks first (no download, no listening) and respects a remembered no
import fl_headless_mcp  # noqa: E402,F401
import flh_transcribe as T  # noqa: E402
calls = []
real_install = K.install
K.install = lambda *a, **k: calls.append(1) or (_ for _ in ()).throw(RuntimeError("offline"))
fs = 22050
def tone(f, d):
    t = np.arange(int(fs * d)) / fs
    return np.exp(-t * 1.5) * np.minimum(1, t / 0.003) * sum(np.sin(2 * np.pi * f * h * t) / h ** 1.4 for h in (1, 2, 3, 4))
x = np.zeros(fs * 4)
for st, f in ((0.2, 261.6), (1.0, 329.6), (1.8, 392.0), (2.6, 523.3)):
    seg = tone(f, 1.2); i = int(st * fs); x[i:i + len(seg)] += seg[:len(x) - i]
x = (x / np.abs(x).max() * 0.5).astype(np.float32)
wav = os.path.join(TMP, "c.wav")
import fl_audio_edit as AE  # noqa: E402
AE.save(wav, x, fs, bits=16)
r = T.audio_to_midi(wav, mode="piano")
assert r.get("needs_choice") and "MB" in r["ask_the_user"] and calls == [], r
r = T.audio_to_midi(wav, mode="piano", install_model=False, self_check=False)
assert r["model"]["name"].startswith("basic-pitch") and K.declined()
r = T.audio_to_midi(wav, mode="piano", self_check=False)               # remembered: no question this time
assert not r.get("needs_choice") and r["model"]["name"].startswith("basic-pitch")
try:                                                                     # yes, but offline: a clear error, nothing kept
    T.audio_to_midi(wav, mode="piano", install_model=True, self_check=False)
    raise AssertionError("expected the install error")
except Exception as e:
    assert calls and "offline" in str(e)
print("ok")
