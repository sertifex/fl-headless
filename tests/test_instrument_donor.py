"""The INSTRUMENT donor for flp_instrument_sound is a Splice INSTRUMENT state, never a Spitfire BBC SO / LABS one
(their states hold a META too). PC 2026-10-24 newsong.instrument_convert: a saved 'BBC Celli' was copied, silent."""
import os, shutil, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
tmp = tempfile.mkdtemp(prefix="flh_donor_"); os.environ["FLH_ROOTS"] = tmp
import atexit; atexit.register(shutil.rmtree, tmp, True)  # noqa: E702
import fl_headless_mcp as m  # noqa: E402

ok = m._is_splice_instrument
assert ok("Splice INSTRUMENT") and ok("INSTRUMENT") and ok("instrument")
assert not ok("BBC Symphony Orchestra") and not ok("LABS") and not ok("Spitfire Audio - LABS")
assert not ok("Instrumental FX") and not ok(None) and not ok("")

def fake(name, body):
    p = os.path.join(tmp, name + ".fst"); open(p, "wb").write(body); return p
bbc = fake("BBC Celli", b'..<META family="BBC Celli" name="x"/>..')
inst = fake("Soft Piano", b'..<META family="Soft Piano" name="y"/>..')
m.flp_sounds = lambda: [{"name": "BBC Celli", "plugin": "BBC Symphony Orchestra", "path": bbc},
                        {"name": "Soft Piano", "plugin": "Splice INSTRUMENT", "path": inst}]
FIX = os.path.join(HERE, "corpus", "base.flp")                    # has INSTRUMENT on ch 4
if os.path.isfile(FIX):
    d = m._load(FIX); ev = m._events(d)
    assert m._instrument_source(FIX, d, ev) == inst, "the BBC sound was taken as the INSTRUMENT donor"
    _, ks = m._channel_block(d, ev, 4)
    assert ok(m._wrapper_name(m._payload(d, ev[ks]))), m._wrapper_name(m._payload(d, ev[ks]))
    m.flp_sounds = lambda: [{"name": "BBC Celli", "plugin": "BBC Symphony Orchestra", "path": bbc}]
    m.flp_save_sound = lambda project, name, channel: {"saved": f"saved ch {channel}"}
    assert m._instrument_source(FIX, d, ev) == "saved ch 0", "the project's own INSTRUMENT channel should be used"
else:
    print("skipped the project part (no corpus/base.flp)")
print("ok")
