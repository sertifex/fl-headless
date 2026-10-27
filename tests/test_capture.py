"""Unit tests for fl_capture (WASAPI loopback). Runs on any OS: the COM layer is replaced by a fake device that,
like real loopback, delivers no packets at all while nothing plays. Run: python tests/test_capture.py"""
import ctypes
import os
import struct
import sys
import tempfile
import time
import wave

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
import fl_capture as m  # noqa: E402

SR = 48000

# ---- write_wav round trip (24-bit)
tmp = tempfile.mkdtemp()
t = np.arange(4800) / SR
x = np.stack([0.5 * np.sin(2 * np.pi * 440 * t), -0.25 * np.ones_like(t)], axis=1)
x[0] = [1.5, -1.5]                                           # clipped
p = m.write_wav(os.path.join(tmp, "a.wav"), SR, x)
with wave.open(p, "rb") as w:
    assert (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()) == (2, 3, SR, 4800)
    raw = w.readframes(4800)
y = m._to_float(raw, 24, False, 2)
assert y.shape == (4800, 2) and y.dtype == np.float32
assert np.allclose(y[1:], x[1:], atol=2e-7), np.abs(y[1:] - x[1:]).max()
assert abs(y[0, 0] - 8388607 / 8388608) < 1e-7 and abs(y[0, 1] + 8388607 / 8388608) < 1e-7
m.write_wav(os.path.join(tmp, "mono.wav"), 44100, np.zeros(10))
with wave.open(os.path.join(tmp, "mono.wav"), "rb") as w:
    assert (w.getnchannels(), w.getnframes(), w.getframerate()) == (1, 10, 44100)

# ---- available() never raises off Windows
if sys.platform != "win32":
    ok, why = m.available()
    assert ok is False and "Windows" in why, why

# ---- _to_float for every sample format
ref = np.array([[0.0, 0.5], [-0.5, -1.0], [0.25, 0.75]])
assert np.array_equal(m._to_float(ref.astype("<f4").tobytes(), 32, True, 2), ref.astype(np.float32))
assert np.allclose(m._to_float(ref.astype("<f8").tobytes(), 64, True, 2), ref)
assert np.allclose(m._to_float((ref * 32768).clip(-32768, 32767).astype("<i2").tobytes(), 16, False, 2), ref,
                   atol=1 / 32768)
i32 = (ref * 2147483648).clip(-2147483648, 2147483647).astype("<i4")
assert np.allclose(m._to_float(i32.tobytes(), 32, False, 2), ref, atol=1e-7)
i24 = (ref * 8388608).clip(-8388608, 8388607).astype("<i4")
b24 = i24.view(np.uint8).reshape(-1, 4)[:, :3].tobytes()
assert np.allclose(m._to_float(b24, 24, False, 2), ref, atol=1 / 8388608)
assert np.allclose(m._to_float(bytes([128, 192, 64, 0]), 8, False, 2), [[0, 0.5], [-0.5, -1]])
assert m._to_float(ref.astype("<f4").tobytes() + b"\x01\x02", 32, True, 2).shape == (3, 2)   # partial frame
assert m._to_float(b"", 32, True, 2).shape == (0, 2)

# ---- _parse_wfx
FLOAT_EXT = (struct.pack("<HHIIHHH", 0xFFFE, 2, SR, SR * 8, 8, 32, 22) + struct.pack("<HI", 32, 3)
             + m.SUBTYPE_FLOAT.bytes_le)
f = m._parse_wfx(FLOAT_EXT)
assert (f["channels"], f["rate"], f["bits"], f["is_float"], f["block_align"], f["channel_mask"]) == (2, SR, 32, True, 8, 3)
pcm24in32 = struct.pack("<HHIIHHH", 0xFFFE, 2, 44100, 44100 * 8, 8, 32, 22) + struct.pack("<HI", 24, 3) + m.SUBTYPE_PCM.bytes_le
f = m._parse_wfx(pcm24in32)
assert (f["bits"], f["valid_bits"], f["is_float"]) == (32, 24, False)
f = m._parse_wfx(struct.pack("<HHIIHHH", 1, 6, SR, SR * 18, 18, 24, 0))
assert (f["channels"], f["bits"], f["is_float"]) == (6, 24, False)
assert m._parse_wfx(struct.pack("<HHIIHHH", 3, 2, SR, SR * 8, 8, 32, 0))["is_float"]
try:
    m._parse_wfx(struct.pack("<HHIIHHH", 0x55, 2, SR, 0, 4, 16, 0))
    raise AssertionError("mp3 tag accepted")
except ValueError:
    pass
g = m._guid("C8ADBD64-E71E-48a0-A4DE-185C395CD317")
assert bytes(g) == bytes.fromhex("64BDADC81EE7A048A4DE185C395CD317")    # in-memory GUID layout

# ---- _gap_frames
assert m._gap_frames(1.0, 1.015, SR) == (0, 1.015)          # jitter: absorbed, anchor slides forward
assert m._gap_frames(1.0, 0.99, SR) == (0, 1.0)             # data "before" anchor: ignored
n, a = m._gap_frames(1.0, 1.5, SR)
assert n == 24000 and abs(a - 1.5) < 1e-9
assert m._gap_frames(0.0, 0.03, SR, min_gap=0.05)[0] == 0
# slow device clock (10 ms packets that take 10.01 ms of wall time) for 100 s never pads
anchor, pads = 0.0, 0
for k in range(10000):
    pad, anchor = m._gap_frames(anchor, k * 0.01001 + (k % 7) * 0.001, SR)
    pads += pad
    anchor += 480 / SR
assert pads == 0, pads

# ---- _assemble mixes bytes and silence markers
fmt = {"channels": 2, "bits": 32, "is_float": True}
seg = np.ones((3, 2), "<f4")
y = m._assemble([seg.tobytes(), 2, seg.tobytes()], fmt)
assert y.shape == (8, 2) and y[3:5].sum() == 0 and y[:3].sum() == 6 and y[5:].sum() == 6
assert m._assemble([], fmt).shape == (0, 2)


# ---- fake COM: a stereo float device that plays 0.0-0.3 s and 0.8-1.0 s (no packets in between)
def hr(code):
    return code - (1 << 32) if code & 0x80000000 else code


ENUM, DEV, CLIENT, CAPTURE = 0x1000, 0x2000, 0x3000, 0x4000


class FakeOle:
    def __init__(self):
        self.calls = []

    def CoInitializeEx(self, reserved, mode):
        self.calls.append(("init", mode))
        return 0

    def CoUninitialize(self):
        self.calls.append(("uninit",))

    def CoCreateInstance(self, clsid, outer, ctx, iid, out):
        assert bytes(clsid._obj) == bytes(m.CLSID_MMDeviceEnumerator) and ctx == m.CLSCTX_ALL
        assert bytes(iid._obj) == bytes(m.IID_IMMDeviceEnumerator)
        out._obj.value = ENUM
        return 0

    def CoTaskMemFree(self, p):
        self.calls.append(("free", p))


class FakeDevice:
    PERIOD = 480

    def __init__(self, segments=((0.0, 0.3), (0.8, 1.0)), fail_default=False):
        self.fmt = ctypes.create_string_buffer(FLOAT_EXT, len(FLOAT_EXT))
        self.fail_default = fail_default
        self.released, self.calls = [], []
        self.packets = []                                   # (start time rel. to Start, silent flag)
        for s0, s1 in segments:
            k = 0
            while s0 + k * 0.01 < s1 - 1e-9:
                self.packets.append((s0 + k * 0.01, False))
                k += 1
        if len(self.packets) > 29:
            self.packets[29] = (self.packets[29][0], True)  # last packet of segment 1 flagged SILENT
        self.pos, self.t0, self.cur = 0, None, None

    def ready(self):
        now = time.perf_counter() - self.t0
        n = 0
        while self.pos + n < len(self.packets) and self.packets[self.pos + n][0] + 0.01 <= now:
            n += 1
        return n

    def __call__(self, ptr, index, argtypes, *args):
        key = (ptr, index)
        if index == 2:
            self.released.append(ptr)
            return 0
        if key == (ENUM, 4):
            assert args[:2] == (m.E_RENDER, m.E_CONSOLE)
            if self.fail_default:
                return hr(0x80070490)
            args[2]._obj.value = DEV
        elif key == (ENUM, 5):
            self.calls.append(("GetDevice", args[0]))
            args[1]._obj.value = DEV
        elif key == (DEV, 3):
            assert bytes(args[0]._obj) == bytes(m.IID_IAudioClient) and args[2] is None
            args[3]._obj.value = CLIENT
        elif key == (CLIENT, 8):
            args[0]._obj.value = ctypes.addressof(self.fmt)
        elif key == (CLIENT, 3):
            assert args == (0, m.AUDCLNT_STREAMFLAGS_LOOPBACK, 20_000_000, 0, ctypes.addressof(self.fmt), None), args
            self.calls.append(("Initialize",))
        elif key == (CLIENT, 14):
            assert bytes(args[0]._obj) == bytes(m.IID_IAudioCaptureClient)
            args[1]._obj.value = CAPTURE
        elif key == (CLIENT, 10):
            self.t0 = time.perf_counter()
            self.calls.append(("Start",))
        elif key == (CLIENT, 11):
            self.calls.append(("Stop",))
        elif key == (CLIENT, 6):
            args[0]._obj.value = self.ready() * self.PERIOD
        elif key == (CAPTURE, 5):
            args[0]._obj.value = self.PERIOD if self.ready() else 0
        elif key == (CAPTURE, 3):
            start, silent = self.packets[self.pos]
            tt = start + np.arange(self.PERIOD) / SR
            s = (0.5 * np.sin(2 * np.pi * 440 * tt)).astype("<f4")
            self.cur = ctypes.create_string_buffer(np.stack([s, s], 1).tobytes())
            args[0]._obj.value = ctypes.addressof(self.cur)
            args[1]._obj.value = self.PERIOD
            args[2]._obj.value = m.AUDCLNT_BUFFERFLAGS_SILENT if silent else 0
        elif key == (CAPTURE, 4):
            assert args == (self.PERIOD,)
            self.pos += 1
        else:
            raise AssertionError(f"unexpected vtable call {key}")
        return 0


ole, dev = FakeOle(), FakeDevice()
m._ole32 = lambda: ole
m._raw_vcall = dev

rec = m.LoopbackRecorder()
rec.start()
assert rec.running
time.sleep(1.2)
sr, y = rec.stop()
assert not rec.running and rec.error is None
dur = len(y) / sr
assert sr == SR and y.dtype == np.float32 and y.shape[1] == 2
assert abs(dur - 1.2) < 0.06, dur                           # silence padded to real time
mono = y[:, 0]
rms = lambda a: float(np.sqrt(np.mean(a.astype(np.float64) ** 2)))  # noqa: E731
assert rms(mono[int(0.02 * SR):int(0.28 * SR)]) > 0.3
assert not mono[int(0.32 * SR):int(0.77 * SR)].any()        # the gap is zeros, not missing
assert rms(mono[int(0.83 * SR):int(0.97 * SR)]) > 0.3
assert not mono[int(1.03 * SR):].any()                      # trailing silence up to stop()
onset = int(np.flatnonzero(mono[int(0.5 * SR):])[0]) + int(0.5 * SR)
assert abs(onset / SR - 0.8) < 0.03, onset / SR            # second burst lands at its real time
assert not mono[int(0.29 * SR) + 50:int(0.3 * SR) - 50].any()   # SILENT-flagged packet -> zeros
assert sorted(dev.released) == [ENUM, DEV, CLIENT, CAPTURE], dev.released
assert ("free", ctypes.addressof(dev.fmt)) in ole.calls and ole.calls[-1] == ("uninit",)
assert ("Stop",) in dev.calls and ole.calls[0] == ("init", 0)

# record() convenience + device ID path
ole, dev = FakeOle(), FakeDevice(segments=((0.0, 0.1),))
m._ole32, m._raw_vcall = (lambda: ole), dev
sr, y = m.record(0.25, device="{0.0.0.00000000}.{abc}")
assert ("GetDevice", "{0.0.0.00000000}.{abc}") in dev.calls and abs(len(y) / sr - 0.25) < 0.05

# failure: HRESULT -> OSError naming the step; everything acquired is still released
ole, dev = FakeOle(), FakeDevice(fail_default=True)
m._ole32, m._raw_vcall = (lambda: ole), dev
rec = m.LoopbackRecorder()
try:
    rec.start()
    raise AssertionError("start() should fail")
except OSError as e:
    assert "GetDefaultAudioEndpoint" in str(e) and "0x80070490" in str(e) and e.hresult == 0x80070490, e
assert dev.released == [ENUM] and ole.calls[-1] == ("uninit",) and not rec.running
try:
    rec.stop()
    raise AssertionError("stop() after failed start should fail")
except RuntimeError:
    pass

# recent_peak: the level of the newest audio while recording (bytes chunks and silence runs mixed)
import fl_capture as _C
_r = _C.LoopbackRecorder()
assert _r.recent_peak() is None                                   # nothing negotiated yet
_r._fmt = {"rate": 1000, "channels": 2, "bits": 32, "is_float": True}
loud = (np.ones((300, 2), np.float32) * 0.5).tobytes(); quiet = (np.ones((300, 2), np.float32) * 0.001).tobytes()
_r._chunks = [loud, 100, quiet]
assert abs(_r.recent_peak(0.25) - 0.001) < 1e-6                   # only the quiet end
assert abs(_r.recent_peak(0.4) - 0.001) < 1e-6                    # silence run + quiet
assert abs(_r.recent_peak(0.45) - 0.5) < 1e-6                     # reaches back into the loud part
print("capture ok")
