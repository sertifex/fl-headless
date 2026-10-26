"""Record what the PC is playing (FL Studio's output) through WASAPI loopback on the default render device.

Standard library + ctypes + numpy only: the COM interfaces are called through their raw vtables, no comtypes.
Import-safe on any OS; everything Windows-only happens inside functions.

    rec = LoopbackRecorder(); rec.start(); ...; sr, x = rec.stop()   # x: float32 (n_frames, channels)
    sr, x = record(5.0)
    write_wav("take.wav", sr, x)                                   # 24-bit PCM
"""
from __future__ import annotations

import ctypes
import os
import struct
import sys
import threading
import time
import uuid
import wave
from functools import lru_cache

import numpy as np

__all__ = ["LoopbackRecorder", "record", "write_wav", "available"]


# ---------------------------------------------------------------- GUIDs and constants

class GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16), ("Data3", ctypes.c_uint16),
                ("Data4", ctypes.c_ubyte * 8)]


def _guid(s: str) -> GUID:
    u = uuid.UUID(s)
    return GUID(u.time_low, u.time_mid, u.time_hi_version, (ctypes.c_ubyte * 8)(*u.bytes[8:]))


CLSID_MMDeviceEnumerator = _guid("BCDE0395-E52F-467C-8E3D-C4579291692E")
IID_IMMDeviceEnumerator = _guid("A95664D2-9614-4F35-A746-DE8DB63617E6")
IID_IAudioClient = _guid("1CB9AD4C-DBFA-4C32-B178-C2F568A703B2")
IID_IAudioCaptureClient = _guid("C8ADBD64-E71E-48A0-A4DE-185C395CD317")
SUBTYPE_PCM = uuid.UUID("00000001-0000-0010-8000-00aa00389b71")
SUBTYPE_FLOAT = uuid.UUID("00000003-0000-0010-8000-00aa00389b71")

COINIT_MULTITHREADED = 0
RPC_E_CHANGED_MODE = 0x80010106
CLSCTX_ALL = 0x17
E_RENDER, E_CONSOLE = 0, 0
AUDCLNT_SHAREMODE_SHARED = 0
AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000
AUDCLNT_BUFFERFLAGS_SILENT = 0x2
AUDCLNT_S_BUFFER_EMPTY = 0x08890001          # success code from GetBuffer: nothing to read
BUFFER_HNS = 20_000_000                       # 2 s shared buffer, in 100 ns units
WAVE_FORMAT_PCM, WAVE_FORMAT_IEEE_FLOAT, WAVE_FORMAT_EXTENSIBLE = 1, 3, 0xFFFE
POLL_S = 0.01
MIN_GAP_S = 0.02

_KNOWN_HR = {
    0x80070490: "E_NOTFOUND (no such / no default output device)",
    0x80070005: "E_ACCESSDENIED",
    0x800401F0: "CO_E_NOTINITIALIZED",
    0x80040154: "REGDB_E_CLASSNOTREG",
    0x88890003: "AUDCLNT_E_WRONG_ENDPOINT_TYPE",
    0x88890004: "AUDCLNT_E_DEVICE_INVALIDATED (output device removed or changed)",
    0x88890008: "AUDCLNT_E_UNSUPPORTED_FORMAT",
    0x8889000A: "AUDCLNT_E_DEVICE_IN_USE (exclusive mode)",
    0x88890010: "AUDCLNT_E_SERVICE_NOT_RUNNING (Windows Audio service)",
}

# vtable argument types (after the implicit `this`), shared so prototypes are cached once
_P_VOIDP = ctypes.POINTER(ctypes.c_void_p)
_P_U32 = ctypes.POINTER(ctypes.c_uint32)
_P_U64 = ctypes.POINTER(ctypes.c_uint64)
_P_GUID = ctypes.POINTER(GUID)
A_NONE = ()
A_GET_DEFAULT = (ctypes.c_int, ctypes.c_int, _P_VOIDP)                       # dataFlow, role, IMMDevice**
A_GET_DEVICE = (ctypes.c_wchar_p, _P_VOIDP)                                   # LPCWSTR id, IMMDevice**
A_ACTIVATE = (_P_GUID, ctypes.c_uint32, ctypes.c_void_p, _P_VOIDP)            # iid, clsctx, PROPVARIANT*, void**
A_OUT_PTR = (_P_VOIDP,)
A_OUT_U32 = (_P_U32,)
A_INITIALIZE = (ctypes.c_int, ctypes.c_uint32, ctypes.c_int64, ctypes.c_int64, ctypes.c_void_p, ctypes.c_void_p)
A_GET_SERVICE = (_P_GUID, _P_VOIDP)
A_GET_BUFFER = (_P_VOIDP, _P_U32, _P_U32, _P_U64, _P_U64)                     # data, frames, flags, devpos, qpcpos
A_RELEASE_BUFFER = (ctypes.c_uint32,)


# ---------------------------------------------------------------- pure helpers (tested on any OS)

def _parse_wfx(buf: bytes) -> dict:
    """Parse a WAVEFORMATEX (18 bytes + cbSize extra) as returned by GetMixFormat."""
    tag, ch, rate, _avg, block_align, bits, cb = struct.unpack_from("<HHIIHHH", buf, 0)
    is_float = None
    valid = bits
    mask = 0
    if tag == WAVE_FORMAT_IEEE_FLOAT:
        is_float = True
    elif tag == WAVE_FORMAT_PCM:
        is_float = False
    elif tag == WAVE_FORMAT_EXTENSIBLE and cb >= 22 and len(buf) >= 40:
        valid, mask = struct.unpack_from("<HI", buf, 18)
        sub = uuid.UUID(bytes_le=bytes(buf[24:40]))       # GUID in memory: Data1-3 little-endian
        is_float = True if sub == SUBTYPE_FLOAT else False if sub == SUBTYPE_PCM else None
    if is_float is None or ch < 1 or block_align < ch:
        raise ValueError(f"unsupported mix format (tag 0x{tag:04X}, {ch} ch, {bits} bit, block {block_align})")
    container = block_align * 8 // ch                      # real per-sample size (24-in-32 etc.)
    if (is_float and container not in (32, 64)) or (not is_float and container not in (8, 16, 24, 32)):
        raise ValueError(f"unsupported sample size {container} bit ({'float' if is_float else 'int'})")
    return {"tag": tag, "channels": ch, "rate": rate, "block_align": block_align, "bits": container,
            "valid_bits": valid or container, "is_float": is_float, "channel_mask": mask}


def _to_float(raw: bytes, bits: int, is_float: bool, channels: int) -> np.ndarray:
    """Interleaved little-endian samples -> float32 (n_frames, channels). A trailing partial frame is dropped.
    Int formats are scaled by their container size (MS convention left-justifies 24-in-32, so /2^31 is right)."""
    width = bits // 8
    frame = width * channels
    mv = memoryview(raw)[: len(raw) // frame * frame]
    if is_float:
        if bits not in (32, 64):
            raise ValueError(f"float{bits} not supported")
        x = np.frombuffer(mv, "<f4" if bits == 32 else "<f8").astype(np.float32)
    elif bits == 8:
        x = (np.frombuffer(mv, np.uint8).astype(np.float32) - 128.0) / 128.0
    elif bits == 16:
        x = np.frombuffer(mv, "<i2").astype(np.float32) / 32768.0
    elif bits == 24:
        b = np.frombuffer(mv, np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        v = (v ^ 0x800000) - 0x800000                       # sign-extend 24 -> 32
        x = (v.astype(np.float64) / 8388608.0).astype(np.float32)
    elif bits == 32:
        x = (np.frombuffer(mv, "<i4").astype(np.float64) / 2147483648.0).astype(np.float32)
    else:
        raise ValueError(f"int{bits} not supported")
    return x.reshape(-1, channels)


def _gap_frames(anchor: float, data_start: float, sr: int, min_gap: float = MIN_GAP_S) -> tuple[int, float]:
    """Silence to insert before new data, from wall-clock times (seconds).

    WASAPI loopback delivers NO packets while nothing is playing (not silent packets), so the stream just stops
    and resumes. `anchor` is the wall time the recording so far ends at; `data_start` the estimated wall time the
    newly available data begins. Returns (zero frames to insert, new anchor).
    Only gaps > min_gap are filled, so poll jitter (bounded by one engine period, ~10 ms) never inserts a dropout.
    A small forward slip (slow device clock vs. wall clock) moves the anchor without padding; an estimate behind
    the anchor (thread scheduled late, fast device clock) leaves it alone, so neither can accumulate into a pad."""
    gap = data_start - anchor
    if gap > min_gap:
        n = int(round(gap * sr))
        return n, anchor + n / sr
    if gap > 0:
        return 0, data_start
    return 0, anchor


def _assemble(chunks: list, fmt: dict) -> np.ndarray:
    """chunks: raw bytes, or an int = that many frames of silence. -> float32 (n, channels)."""
    ch = fmt["channels"]
    parts, run = [], []

    def flush():
        if run:
            parts.append(_to_float(b"".join(run), fmt["bits"], fmt["is_float"], ch))
            run.clear()

    for c in chunks:
        if isinstance(c, int):
            flush()
            parts.append(np.zeros((c, ch), np.float32))
        else:
            run.append(c)
    flush()
    return np.concatenate(parts) if parts else np.zeros((0, ch), np.float32)


def write_wav(path, sr, x) -> str:
    """Write float audio (n,) or (n, channels) as 24-bit PCM little-endian WAV. Values are clipped to [-1, 1]."""
    a = np.asarray(x, dtype=np.float64)
    if a.ndim == 1:
        a = a[:, None]
    if a.ndim != 2 or a.shape[1] < 1:
        raise ValueError(f"expected (n,) or (n, channels) audio, got shape {a.shape}")
    i = np.round(np.clip(np.nan_to_num(a, nan=0.0, posinf=0.0, neginf=0.0), -1.0, 1.0) * 8388607.0).astype("<i4")   # C-contiguous copy
    data = i.view(np.uint8).reshape(-1, 4)[:, :3].tobytes()                          # drop the high byte
    path = os.fspath(path)
    with wave.open(path, "wb") as w:
        w.setnchannels(a.shape[1])
        w.setsampwidth(3)
        w.setframerate(int(sr))
        w.writeframes(data)
    return path


# ---------------------------------------------------------------- raw COM (Windows only, called lazily)

@lru_cache(maxsize=None)
def _ole32():
    if sys.platform != "win32":
        raise OSError(f"WASAPI loopback needs Windows (running on {sys.platform})")
    ole = ctypes.WinDLL("ole32")          # own instance: don't clash with argtypes others set on ctypes.windll
    ole.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    ole.CoInitializeEx.restype = ctypes.c_long
    ole.CoUninitialize.argtypes = []
    ole.CoUninitialize.restype = None
    ole.CoCreateInstance.argtypes = [_P_GUID, ctypes.c_void_p, ctypes.c_uint32, _P_GUID, _P_VOIDP]
    ole.CoCreateInstance.restype = ctypes.c_long
    ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole.CoTaskMemFree.restype = None
    return ole


@lru_cache(maxsize=None)
def _proto(argtypes: tuple):
    return ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)   # HRESULT (this, ...)


def _raw_vcall(ptr: int, index: int, argtypes: tuple, *args) -> int:
    """Call method `index` of the COM object at `ptr`; returns the HRESULT (as signed long)."""
    vtbl = ctypes.cast(ptr, ctypes.POINTER(_P_VOIDP)).contents     # *(void***)ptr
    return _proto(argtypes)(vtbl[index])(ptr, *args)


def _check(hr: int, step: str) -> int:
    if hr < 0:
        code = hr & 0xFFFFFFFF
        known = _KNOWN_HR.get(code)
        err = OSError(f"{step} failed: HRESULT 0x{code:08X}" + (f" {known}" if known else ""))
        err.hresult = code
        raise err
    return hr


def _vcall(ptr: int, index: int, step: str, argtypes: tuple, *args) -> int:
    return _check(_raw_vcall(ptr, index, argtypes, *args), step)


def _out(p: ctypes.c_void_p, step: str) -> int:
    if not p.value:
        raise OSError(f"{step} returned a NULL interface")
    return p.value


class _Session:
    """Owns the COM objects for one capture stream; close() releases everything that was acquired."""

    def __init__(self):
        self.enum = self.dev = self.client = self.capture = None
        self.pwfx = None
        self.fmt = None
        self.started = False

    def open(self, device=None, init_stream=True):
        ole = _ole32()
        p = ctypes.c_void_p()
        _check(ole.CoCreateInstance(ctypes.byref(CLSID_MMDeviceEnumerator), None, CLSCTX_ALL,
                                    ctypes.byref(IID_IMMDeviceEnumerator), ctypes.byref(p)),
               "CoCreateInstance(MMDeviceEnumerator)")
        self.enum = _out(p, "CoCreateInstance(MMDeviceEnumerator)")

        p = ctypes.c_void_p()
        if device is None:
            step = "GetDefaultAudioEndpoint(eRender, eConsole)"
            _vcall(self.enum, 4, step, A_GET_DEFAULT, E_RENDER, E_CONSOLE, ctypes.byref(p))
        else:
            step = "IMMDeviceEnumerator.GetDevice"
            _vcall(self.enum, 5, step, A_GET_DEVICE, str(device), ctypes.byref(p))
        self.dev = _out(p, step)

        p = ctypes.c_void_p()
        _vcall(self.dev, 3, "IMMDevice.Activate(IAudioClient)", A_ACTIVATE,
               ctypes.byref(IID_IAudioClient), CLSCTX_ALL, None, ctypes.byref(p))
        self.client = _out(p, "IMMDevice.Activate(IAudioClient)")

        p = ctypes.c_void_p()
        _vcall(self.client, 8, "IAudioClient.GetMixFormat", A_OUT_PTR, ctypes.byref(p))
        self.pwfx = _out(p, "IAudioClient.GetMixFormat")
        cb = struct.unpack("<H", ctypes.string_at(self.pwfx + 16, 2))[0]
        self.fmt = _parse_wfx(ctypes.string_at(self.pwfx, 18 + cb))
        if not init_stream:
            return self
        _vcall(self.client, 3, "IAudioClient.Initialize(shared, loopback)", A_INITIALIZE,
               AUDCLNT_SHAREMODE_SHARED, AUDCLNT_STREAMFLAGS_LOOPBACK, BUFFER_HNS, 0, self.pwfx, None)
        p = ctypes.c_void_p()
        _vcall(self.client, 14, "IAudioClient.GetService(IAudioCaptureClient)", A_GET_SERVICE,
               ctypes.byref(IID_IAudioCaptureClient), ctypes.byref(p))
        self.capture = _out(p, "IAudioClient.GetService(IAudioCaptureClient)")
        return self

    def start(self):
        _vcall(self.client, 10, "IAudioClient.Start", A_NONE)
        self.started = True

    def queued(self) -> int:
        """Frames waiting in the capture buffer (for a capture stream GetCurrentPadding = data available)."""
        n = ctypes.c_uint32()
        _vcall(self.client, 6, "IAudioClient.GetCurrentPadding", A_OUT_U32, ctypes.byref(n))
        return n.value

    def drain(self, chunks: list) -> int:
        """Read every available packet into chunks (bytes, or int = silent frames). Returns frames read."""
        block = self.fmt["block_align"]
        total = 0
        while True:
            n = ctypes.c_uint32()
            _vcall(self.capture, 5, "IAudioCaptureClient.GetNextPacketSize", A_OUT_U32, ctypes.byref(n))
            if not n.value:
                return total
            data, frames, flags = ctypes.c_void_p(), ctypes.c_uint32(), ctypes.c_uint32()
            devpos, qpcpos = ctypes.c_uint64(), ctypes.c_uint64()
            hr = _vcall(self.capture, 3, "IAudioCaptureClient.GetBuffer", A_GET_BUFFER, ctypes.byref(data),
                        ctypes.byref(frames), ctypes.byref(flags), ctypes.byref(devpos), ctypes.byref(qpcpos))
            if hr == AUDCLNT_S_BUFFER_EMPTY:
                return total
            f = frames.value
            try:
                if f:
                    if flags.value & AUDCLNT_BUFFERFLAGS_SILENT or not data.value:
                        chunks.append(f)
                    else:
                        chunks.append(ctypes.string_at(data.value, f * block))
            finally:
                _vcall(self.capture, 4, "IAudioCaptureClient.ReleaseBuffer", A_RELEASE_BUFFER, f)
            total += f

    def close(self):
        if self.started:
            try:
                _raw_vcall(self.client, 11, A_NONE)         # Stop; failure here is irrelevant
            except Exception:
                pass
            self.started = False
        for name in ("capture", "client", "dev", "enum"):
            ptr = getattr(self, name)
            setattr(self, name, None)
            if ptr:
                try:
                    _raw_vcall(ptr, 2, A_NONE)              # IUnknown::Release
                except Exception:
                    pass
        if self.pwfx:
            try:
                _ole32().CoTaskMemFree(self.pwfx)
            except Exception:
                pass
            self.pwfx = None


def _co_init(ole) -> bool:
    """CoInitializeEx(MTA) on this thread. Returns True if CoUninitialize must be called later."""
    hr = ole.CoInitializeEx(None, COINIT_MULTITHREADED)
    if hr < 0 and (hr & 0xFFFFFFFF) != RPC_E_CHANGED_MODE:
        _check(hr, "CoInitializeEx")
    return hr >= 0          # S_OK and S_FALSE both need a matching CoUninitialize; RPC_E_CHANGED_MODE does not


# ---------------------------------------------------------------- public API

class LoopbackRecorder:
    """Records the default output device (what you hear) in the background.

    All COM work happens on the recorder's own thread (MTA). start() returns once the stream is running and
    raises OSError if the device could not be opened. If capture fails mid-way (e.g. the output device is
    unplugged or switched), the thread ends, `running` turns False, stop() returns what was captured and the
    exception is kept in `.error` (stop() raises it only if nothing was captured)."""

    def __init__(self, device=None):
        self.device = device            # None = default render endpoint (eRender, eConsole); or an endpoint ID string
        self.error = None
        self._thread = None
        self._stop_evt = threading.Event()
        self._ready = threading.Event()
        self._chunks: list = []
        self._fmt = None
        self._err = None
        self._stop_time = None

    def start(self):
        if self._thread is not None:
            raise RuntimeError("recorder already started; call stop() first")
        self._stop_evt = threading.Event()           # fresh per run: an orphaned earlier thread keeps its own
        self._ready = threading.Event()
        self._chunks, self._fmt, self._err, self._stop_time, self.error = [], None, None, None, None
        self._thread = threading.Thread(target=self._run, args=(self._chunks, self._stop_evt, self._ready),
                                        name="fl-capture-loopback", daemon=True)
        self._thread.start()
        if not self._ready.wait(10):
            self._stop_time = time.perf_counter()
            self._stop_evt.set()
            self._thread = None
            _PROBE_OK[0] = 0.0                       # the device changed: probe it again next time
            raise OSError("timed out opening the WASAPI loopback stream")
        if self._err is not None:
            self._thread.join(5)
            self._thread = None
            _PROBE_OK[0] = 0.0
            raise self._err

    def recent_peak(self, seconds=0.25):
        """Peak level (0..1) of the last `seconds` captured so far, while recording (None before the format is
        known). Lets a caller stop as soon as a reverb tail has died away instead of waiting a fixed time."""
        fmt = self._fmt
        if not fmt:
            return None
        need, ch = max(1, int(seconds * fmt["rate"])), fmt["channels"]
        width = (fmt["bits"] // 8) * ch
        tail, n = [], 0
        for c in reversed(self._chunks[-400:]):         # a slice copy: the recorder thread may append meanwhile
            if isinstance(c, int):
                k = min(c, need - n); tail.append(np.zeros((k, ch), np.float32))
            else:
                k = min(len(c) // width, need - n)
                tail.append(_to_float(c[len(c) - k * width:], fmt["bits"], fmt["is_float"], ch))
            n += k
            if n >= need:
                break
        if not n:
            return None
        return float(max(float(np.abs(t).max()) if len(t) else 0.0 for t in tail))

    @property
    def running(self) -> bool:
        t = self._thread
        return t is not None and t.is_alive() and not self._stop_evt.is_set()

    def stop(self) -> tuple[int, "np.ndarray"]:
        """Stop and return (sample_rate, float32 array (n_frames, channels))."""
        if self._thread is None:
            raise RuntimeError("recorder was not started")
        self._stop_time = time.perf_counter()       # set before the event so the thread sees it
        self._stop_evt.set()
        self._thread.join(10)                        # a hung driver must not hang the tool
        self._thread = None
        self.error = self._err
        if self._fmt is None:
            raise self._err or RuntimeError("no audio format negotiated")
        x = _assemble(self._chunks, self._fmt)
        self._chunks = []
        if self._err is not None and not len(x):
            raise self._err
        return self._fmt["rate"], x

    def _run(self, chunks=None, stop_evt=None, ready=None):
        # each run owns its list and events, so a late (timed-out) thread can't touch the next recording
        chunks = self._chunks if chunks is None else chunks
        stop_evt = stop_evt or self._stop_evt
        ready = ready or self._ready
        ole, uninit, s = None, False, _Session()
        try:
            ole = _ole32()
            uninit = _co_init(ole)
            s.open(self.device, init_stream=True)
            if stop_evt is self._stop_evt:          # only the current run publishes (an orphaned run - start()
                self._fmt = s.fmt                   # timed out - must not touch the next recording)
            sr = s.fmt["rate"]
            anchor = time.perf_counter()            # wall time the recording "so far" ends at
            s.start()
            ready.set()
            while True:
                stopping = stop_evt.is_set()  # checked first so one last drain runs after stop()
                # Drain first, then date the data by how much was actually read: the read ends ~now, so it
                # started n/sr ago. (GetCurrentPadding is only the NEXT packet's size for capture streams, so it
                # can't tell how much is queued when this thread runs late.)
                got = []
                n = s.drain(got)
                now = time.perf_counter()
                if n:
                    pad, anchor = _gap_frames(anchor, now - n / sr, sr)
                    if pad:
                        chunks.append(pad)
                    chunks.extend(got)
                    anchor += n / sr
                if stopping:
                    break
                time.sleep(POLL_S)                  # 3.11+: high-resolution timer on Windows
            pad, _ = _gap_frames(anchor, self._stop_time, sr)   # trailing silence up to the stop() call
            if pad:
                chunks.append(pad)
        except BaseException as e:                  # noqa: BLE001 - handed to the caller via start()/stop()
            if stop_evt is self._stop_evt:          # an orphan's late failure isn't the next take's error
                self._err = e
        finally:
            s.close()
            if uninit:
                try:
                    ole.CoUninitialize()
                except Exception:
                    pass
            ready.set()                       # unblock start() if init failed


def record(seconds: float, device=None) -> tuple[int, "np.ndarray"]:
    """Blocking: record `seconds` of the output device. Returns (sample_rate, float32 (n_frames, channels))."""
    rec = LoopbackRecorder(device)
    rec.start()
    try:
        time.sleep(max(0.0, float(seconds)))
    finally:
        out = rec.stop()
    return out


_PROBE_OK = [0.0]                                    # when the last probe succeeded (a success holds 2 minutes)
PROBE_FRESH_S = 120.0


def available() -> tuple[bool, str]:
    """(True, "ok") if a loopback stream can be opened on the default output device, else (False, reason).
    Never raises. Runs the probe on its own thread so the caller's COM apartment is untouched. A success is
    remembered for 2 minutes (back-to-back listens don't open the device twice each); a failure is probed again."""
    try:
        if sys.platform != "win32":
            return False, f"WASAPI loopback needs Windows (running on {sys.platform})"
        if time.time() - _PROBE_OK[0] < PROBE_FRESH_S:
            return True, "ok"
        result = [(False, "audio device probe timed out")]

        def probe():
            ole, uninit, s = None, False, _Session()
            try:
                ole = _ole32()
                uninit = _co_init(ole)
                s.open(None, init_stream=True)
                result[0] = (True, "ok")
            except BaseException as e:              # noqa: BLE001
                result[0] = (False, f"{type(e).__name__}: {e}")
            finally:
                s.close()
                if uninit:
                    try:
                        ole.CoUninitialize()
                    except Exception:
                        pass

        t = threading.Thread(target=probe, name="fl-capture-probe", daemon=True)
        t.start()
        t.join(10)
        if result[0][0]:
            _PROBE_OK[0] = time.time()
        return result[0]
    except Exception as e:                          # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"
