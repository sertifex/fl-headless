"""Reload verification restores an edit FL didn't pick up; snapshot PNG/crop helpers are correct."""
import os, sys, zlib, struct
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import fl_headless_mcp as m

# PNG encoder round trip
w, h = 3, 2; rgb = bytes(range(18)); png = m._png_encode(w, h, rgb)
assert png[:8] == b"\x89PNG\r\n\x1a\n" and struct.unpack(">II", png[16:24]) == (3, 2)
idat = png[png.index(b"IDAT") + 4:png.index(b"IEND") - 8]
assert zlib.decompress(idat) == b"\x00" + rgb[:9] + b"\x00" + rgb[9:]
# BGRA -> RGB crop + step
W, H = 4, 4; buf = bytes(b for y in range(H) for x in range(W) for b in (x, y, 100 + x, 255))   # B=x G=y R=100+x
ow, oh, out = m._bgra_crop_scale(buf, W, H, (0, 0, 4, 4), 2)
assert (ow, oh) == (2, 2) and out[:6] == bytes([100, 0, 0, 102, 0, 2]), out
ow, oh, out = m._bgra_crop_scale(buf, W, H, (1, 2, 3, 4), 1)
assert (ow, oh) == (2, 2) and out[:3] == bytes([101, 2, 1])

# verification: FL's first save reverts the edit -> restored + reloaded, then verified
state = {"saves": 0, "reloads": 0, "file": b"edited"}
m._save_in_fl_and_wait = lambda p, wait_s=8.0, **k: (state.__setitem__("saves", state["saves"] + 1),
                                                 state.__setitem__("file", b"old" if state["saves"] == 1 else state["file"]), True)[2]
m._fingerprint = lambda p: {"notes": state["file"]}
m._write_bytes = lambda p, data: state.__setitem__("file", data)
m._backup = lambda *a, **k: None
m._reload_in_fl = lambda p, **k: state.__setitem__("reloads", state["reloads"] + 1)
m.BRIDGE.call = lambda *a, **k: {}
m.time.sleep = lambda s: None
r = m._verify_loaded("x.flp", b"edited", {"notes": b"edited"}, was_playing=False, pre={"notes": b"old"})
assert r["verified"] is True and state["reloads"] == 1 and state["file"] == b"edited", (r, state)
# FL never takes it -> verified False, edit left in the file
state.update(saves=0, reloads=0, file=b"edited")
m._save_in_fl_and_wait = lambda p, wait_s=8.0, **k: (state.__setitem__("file", b"old"), True)[1]
r = m._verify_loaded("x.flp", b"edited", {"notes": b"edited"}, was_playing=False, pre={"notes": b"old"})
assert r["verified"] is False and state["file"] == b"edited", (r, state)
# C2: the user drew notes in FL right after the reload: FL's save differs from BOTH versions -> nothing restored
backups = []
m._backup = lambda p, keep=40, data=None: backups.append(data) or "bk"
state.update(saves=0, reloads=0, file=b"edited")
m._save_in_fl_and_wait = lambda p, wait_s=8.0, **k: (state.__setitem__("file", b"edited+user notes"), True)[1]
r = m._verify_loaded("x.flp", b"edited", {"notes": b"edited"}, was_playing=False, pre={"notes": b"old"})
assert r["verified"] is False and r.get("hand_edits") and state["file"] == b"edited+user notes", (r, state)
assert state["reloads"] == 0 and backups == [b"edited"], (state, backups)    # the edit kept as a backup, no reload
# hand edits in a part the edit didn't touch: the edit held -> verified, the hand edits kept
fp = {"f": {"notes": b"edited", "clips": b"user"}}
m._fingerprint = lambda p: fp["f"]
m._save_in_fl_and_wait = lambda p, wait_s=8.0, **k: True
r = m._verify_loaded("x.flp", b"edited", {"notes": b"edited", "clips": b"c"}, was_playing=False,
                     pre={"notes": b"old", "clips": b"c"})
assert r["verified"] is True and r["hand_edits"] == ["clips"], r
# H7: a newer edit (generation moved on) -> the check steps aside before any write or reload
m._fingerprint = lambda p: {"notes": state["file"]}
state.update(saves=0, reloads=0, file=b"edited")
m._PENDING_VERIFY["__gen"] = 5
def save_then_newer(p, wait_s=8.0, **k):
    state["file"] = b"old"; m._PENDING_VERIFY["__gen"] = 6; return True
m._save_in_fl_and_wait = save_then_newer
writes = []
m._write_bytes = lambda p, data: writes.append(data)
r = m._verify_loaded("x.flp", b"edited", {"notes": b"edited"}, was_playing=False, pre={"notes": b"old"}, gen=5)
assert r["verified"] is None and not writes and state["reloads"] == 0, (r, writes, state)
print("ok")
