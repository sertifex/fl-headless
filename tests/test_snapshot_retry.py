"""fl_snapshot (2.2.0): a blank capture right after a reload (FL repainting) is taken again instead of failing; a
region over a dark part of FL is no 'blank window' (blank is judged on the whole window); a window that stays blank
still fails clearly; bad regions are refused. The Windows capture is replaced by a fake. Run:
python3 tests/test_snapshot_retry.py"""
import base64, os, struct, sys, tempfile, zlib
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
tmp = tempfile.mkdtemp()
os.environ.setdefault("FLH_ROOTS", tmp); os.environ.setdefault("LOCALAPPDATA", os.path.join(tmp, "local"))
import fl_headless_mcp as m  # noqa: E402

W, H = 200, 100


def window(lit=True):
    """BGRA, top-down: black everywhere, a grey band in the bottom half when lit (alpha always 255)."""
    px = bytearray(b"\x00\x00\x00\xff" * (W * H))
    if lit:
        for y in range(60, 80):
            for x in range(W):
                o = (y * W + x) * 4; px[o:o + 3] = b"\x50\x50\x50"
    return bytes(px)


grabs = []
plan = []


def fake_grab(hwnd):
    grabs.append(hwnd)
    return W, H, plan.pop(0) if plan else window(True)


m._snap_grab = fake_grab
m._fl_main_window = lambda: (77, "Song - FL Studio 2026")
m.fl_dialogs = lambda: []
m.BRIDGE.call = lambda *a, **k: {}
m.SNAP_BLANK_RETRY_S = (0.0, 0.0, 0.0)
m.time.sleep = lambda s: None


def png_size(r):
    png = base64.b64decode(r["_image_png"])
    return struct.unpack(">II", png[16:24])


# blank twice (FL repainting after a reload), then the picture: taken again, said so
plan[:] = [window(False), window(False)]
r = m.fl_snapshot(region=[0, 0, 0.5, 0.3])
assert len(grabs) == 3 and "taken again" in r.get("note", ""), (grabs, r.get("note"))
assert png_size(r) == (100, 30), png_size(r)
# a region over FL's black top while the window has content: the picture, not 'blank'
grabs.clear()
r = m.fl_snapshot(region=[0, 0, 1, 0.5])
assert len(grabs) == 1 and "note" not in r and png_size(r) == (200, 50), (grabs, r.get("note"))
idat = base64.b64decode(r["_image_png"]); raw = zlib.decompress(idat[idat.index(b"IDAT") + 4:idat.index(b"IEND") - 8])
assert not any(raw[i] for i in range(len(raw)) if i % (200 * 3 + 1)), "the region really is black"
# alpha alone doesn't count as content; a window that stays blank fails clearly
grabs.clear(); plan[:] = [window(False)] * 4
try:
    m.fl_snapshot(); raise SystemExit("a blank window passed")
except RuntimeError as e:
    assert "blank 4 times" in str(e) and len(grabs) == 4, (str(e), grabs)
# bad regions are refused before any capture
grabs.clear()
for bad in ([0.5, 0, 0.2, 1], [0, 0, 1], ["a", 0, 1, 1]):
    try:
        m.fl_snapshot(region=bad); raise SystemExit(f"bad region {bad} accepted")
    except ValueError:
        pass
assert not grabs
# the real capture is marked Windows-only: off Windows it says so
if os.name != "nt":
    win_grab = lambda h: fake_grab(h)                             # noqa: E731
    win_grab.__win__ = True
    m._snap_grab = win_grab
    try:
        m.fl_snapshot(); raise SystemExit("ran off Windows")
    except RuntimeError as e:
        assert "needs Windows" in str(e)
print("ok")
