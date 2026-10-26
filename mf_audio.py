"""mf_audio: decode MP3 / M4A (AAC) / WMA / AIFF and the audio of video files with Windows' own Media Foundation
(no extra installs, no codecs shipped). Windows only; everything else in fl-headless reads WAV, FLAC and Ogg itself.

    x, rate = decode(path, max_seconds=None)   # float32 (frames, channels), as the file's own rate and channels
    available() -> (bool, why)

The Source Reader converts whatever the file holds to 32-bit float PCM; samples are copied out buffer by buffer.
All COM work runs on its own thread (MTA) so the caller's COM apartment is never touched.
PC-VERIFY: an MP3 and an M4A decode to the same loudness as the WAV they were made from (pc check render.mp3_read).
"""
import ctypes
import os
import sys
import threading

import numpy as np

SUFFIXES = (".mp3", ".m4a", ".aac", ".mp4", ".wma", ".aif", ".aiff", ".aifc", ".mov", ".m4v", ".3gp", ".caf")

MF_VERSION = 0x00020070
MFSTARTUP_LITE = 1
MF_SOURCE_READER_FIRST_AUDIO_STREAM = 0xFFFFFFFD
MF_SOURCE_READER_ALL_STREAMS = 0xFFFFFFFE
MF_SOURCE_READERF_ERROR = 0x1
MF_SOURCE_READERF_ENDOFSTREAM = 0x2
MF_SOURCE_READERF_CURRENTMEDIATYPECHANGED = 0x20

# IMFAttributes (after IUnknown's 3): GetUINT32 7, SetGUID 24, SetUINT32 21
IA_GETUINT32, IA_SETUINT32, IA_SETGUID = 7, 21, 24
# IMFSourceReader
SR_SETSTREAMSELECTION, SR_GETCURRENTMEDIATYPE, SR_SETCURRENTMEDIATYPE, SR_READSAMPLE = 4, 6, 7, 9
# IMFSample (IMFAttributes' 30 methods, then): ConvertToContiguousBuffer 41
SA_CONVERTTOCONTIGUOUS = 41
# IMFMediaBuffer
MB_LOCK, MB_UNLOCK = 3, 4
RELEASE = 2


def available():
    if sys.platform != "win32":
        return False, f"MP3/M4A decoding uses Windows Media Foundation (running on {sys.platform}): convert to WAV/FLAC"
    try:
        ctypes.WinDLL("mfplat"); ctypes.WinDLL("mfreadwrite")
        return True, "ok"
    except OSError as e:
        return False, f"Media Foundation isn't available ({e}; Windows 'N' editions need the Media Feature Pack)"


def _guids():
    import fl_capture as C
    g = C._guid
    return {"MF_MT_MAJOR_TYPE": g("48eba18e-f8c9-4687-bf11-0a74c9f96a8f"),
            "MFMediaType_Audio": g("73647561-0000-0010-8000-00aa00389b71"),
            "MF_MT_SUBTYPE": g("f7e34c9a-42e8-4714-b74b-cb29d72c35e5"),
            "MFAudioFormat_Float": g("00000003-0000-0010-8000-00aa00389b71"),
            "MF_MT_AUDIO_NUM_CHANNELS": g("37e48bf5-645e-4c5b-89de-ada9e29b696a"),
            "MF_MT_AUDIO_SAMPLES_PER_SECOND": g("5faeeae7-0290-4c31-9e8a-c534f68d9dba")}


def _decode_here(path, max_seconds):
    import fl_capture as C
    P = ctypes.POINTER
    ole = C._ole32()
    uninit = C._co_init(ole)
    mfplat = ctypes.WinDLL("mfplat"); mfrw = ctypes.WinDLL("mfreadwrite")
    mfplat.MFStartup.argtypes = [ctypes.c_ulong, ctypes.c_ulong]; mfplat.MFStartup.restype = ctypes.c_long
    mfplat.MFShutdown.argtypes = []; mfplat.MFShutdown.restype = ctypes.c_long
    mfplat.MFCreateMediaType.argtypes = [P(ctypes.c_void_p)]; mfplat.MFCreateMediaType.restype = ctypes.c_long
    mfrw.MFCreateSourceReaderFromURL.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p, P(ctypes.c_void_p)]
    mfrw.MFCreateSourceReaderFromURL.restype = ctypes.c_long
    G = _guids()
    reader = mtype = cur = None
    started = False
    vcall, check = C._vcall, C._check
    try:
        check(mfplat.MFStartup(MF_VERSION, MFSTARTUP_LITE), "MFStartup"); started = True
        p = ctypes.c_void_p()
        check(mfrw.MFCreateSourceReaderFromURL(os.path.abspath(path), None, ctypes.byref(p)),
              f"opening {os.path.basename(path)}")
        reader = C._out(p, "MFCreateSourceReaderFromURL")
        vcall(reader, SR_SETSTREAMSELECTION, "SetStreamSelection", (ctypes.c_ulong, ctypes.c_long),
              MF_SOURCE_READER_ALL_STREAMS, 0)
        vcall(reader, SR_SETSTREAMSELECTION, "SetStreamSelection", (ctypes.c_ulong, ctypes.c_long),
              MF_SOURCE_READER_FIRST_AUDIO_STREAM, 1)
        p = ctypes.c_void_p()
        check(mfplat.MFCreateMediaType(ctypes.byref(p)), "MFCreateMediaType"); mtype = C._out(p, "MFCreateMediaType")
        vcall(mtype, IA_SETGUID, "SetGUID(major)", (C._P_GUID, C._P_GUID), ctypes.byref(G["MF_MT_MAJOR_TYPE"]),
              ctypes.byref(G["MFMediaType_Audio"]))
        vcall(mtype, IA_SETGUID, "SetGUID(subtype)", (C._P_GUID, C._P_GUID), ctypes.byref(G["MF_MT_SUBTYPE"]),
              ctypes.byref(G["MFAudioFormat_Float"]))
        vcall(reader, SR_SETCURRENTMEDIATYPE, "SetCurrentMediaType (float PCM)",
              (ctypes.c_ulong, ctypes.c_void_p, ctypes.c_void_p), MF_SOURCE_READER_FIRST_AUDIO_STREAM, None, mtype)
        p = ctypes.c_void_p()
        vcall(reader, SR_GETCURRENTMEDIATYPE, "GetCurrentMediaType", (ctypes.c_ulong, P(ctypes.c_void_p)),
              MF_SOURCE_READER_FIRST_AUDIO_STREAM, ctypes.byref(p))
        cur = C._out(p, "GetCurrentMediaType")

        def fmt(mt):
            ch, rate = ctypes.c_uint32(), ctypes.c_uint32()
            vcall(mt, IA_GETUINT32, "channels", (C._P_GUID, P(ctypes.c_uint32)),
                  ctypes.byref(G["MF_MT_AUDIO_NUM_CHANNELS"]), ctypes.byref(ch))
            vcall(mt, IA_GETUINT32, "sample rate", (C._P_GUID, P(ctypes.c_uint32)),
                  ctypes.byref(G["MF_MT_AUDIO_SAMPLES_PER_SECOND"]), ctypes.byref(rate))
            return int(ch.value), int(rate.value)
        nch, sr = fmt(cur)
        if nch < 1 or sr < 1:
            raise OSError("Media Foundation reported no audio format")
        limit = int(max_seconds * sr) * nch * 4 if max_seconds else None
        chunks, got = [], 0
        while True:
            idx, flags, ts, sample = ctypes.c_ulong(), ctypes.c_ulong(), ctypes.c_longlong(), ctypes.c_void_p()
            vcall(reader, SR_READSAMPLE, "ReadSample",
                  (ctypes.c_ulong, ctypes.c_ulong, P(ctypes.c_ulong), P(ctypes.c_ulong), P(ctypes.c_longlong),
                   P(ctypes.c_void_p)),
                  MF_SOURCE_READER_FIRST_AUDIO_STREAM, 0, ctypes.byref(idx), ctypes.byref(flags), ctypes.byref(ts),
                  ctypes.byref(sample))
            if flags.value & MF_SOURCE_READERF_ERROR:
                if sample.value:
                    C._raw_vcall(sample.value, RELEASE, ())
                raise OSError("Media Foundation stopped with a stream error (a damaged file?)")
            if sample.value:
                try:
                    buf = ctypes.c_void_p()
                    vcall(sample.value, SA_CONVERTTOCONTIGUOUS, "ConvertToContiguousBuffer", (P(ctypes.c_void_p),),
                          ctypes.byref(buf))
                    try:
                        data, mx, ln = ctypes.c_void_p(), ctypes.c_ulong(), ctypes.c_ulong()
                        vcall(buf.value, MB_LOCK, "Lock", (P(ctypes.c_void_p), P(ctypes.c_ulong), P(ctypes.c_ulong)),
                              ctypes.byref(data), ctypes.byref(mx), ctypes.byref(ln))
                        try:
                            chunks.append(ctypes.string_at(data.value, ln.value)); got += ln.value
                        finally:
                            C._raw_vcall(buf.value, MB_UNLOCK, ())
                    finally:
                        C._raw_vcall(buf.value, RELEASE, ())
                finally:
                    C._raw_vcall(sample.value, RELEASE, ())
            if flags.value & MF_SOURCE_READERF_CURRENTMEDIATYPECHANGED:   # e.g. HE-AAC: the real rate shows now
                p2 = ctypes.c_void_p()
                vcall(reader, SR_GETCURRENTMEDIATYPE, "GetCurrentMediaType", (ctypes.c_ulong, P(ctypes.c_void_p)),
                      MF_SOURCE_READER_FIRST_AUDIO_STREAM, ctypes.byref(p2))
                try:
                    n2, r2 = fmt(p2.value)
                finally:
                    C._raw_vcall(p2.value, RELEASE, ())
                if (n2, r2) != (nch, sr):
                    if got > len(chunks[-1] if chunks else b""):
                        raise OSError("the audio format changes inside the file (not supported)")
                    nch, sr = n2, r2
                    limit = int(max_seconds * sr) * nch * 4 if max_seconds else None
            if flags.value & MF_SOURCE_READERF_ENDOFSTREAM or (limit and got >= limit):
                break
        raw = b"".join(chunks)
        if limit:
            raw = raw[:limit]
        x = np.frombuffer(raw[:len(raw) // (4 * nch) * 4 * nch], dtype="<f4").reshape(-1, nch).copy()
        return x, sr
    finally:
        for obj in (cur, mtype, reader):
            if obj:
                try:
                    C._raw_vcall(obj, RELEASE, ())
                except Exception:                                 # noqa: BLE001
                    pass
        if started:
            mfplat.MFShutdown()
        if uninit:
            try:
                ole.CoUninitialize()
            except Exception:                                     # noqa: BLE001
                pass


def decode(path, max_seconds=None):
    """-> (float32 (frames, channels), rate). Raises OSError with a plain reason when it can't."""
    ok, why = available()
    if not ok:
        raise OSError(why)
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    box = {}

    def run():
        try:
            box["r"] = _decode_here(path, max_seconds)
        except BaseException as e:                                # noqa: BLE001 - handed to the caller
            box["e"] = e
    t = threading.Thread(target=run, name="mf-decode", daemon=True)
    t.start(); t.join(600)
    if "e" in box:
        e = box["e"]
        raise OSError(f"couldn't decode {os.path.basename(path)} with Windows Media Foundation: {e}") from e
    if "r" not in box:
        raise OSError(f"decoding {os.path.basename(path)} took over 10 minutes")
    return box["r"]


def is_media(path):
    return str(path).lower().endswith(SUFFIXES)
