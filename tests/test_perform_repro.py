"""flp_perform on a song flh_new_song makes (drill: one pattern per layer, stacked on tracks, sections repeated, a
'Verse 808' and a 'Hook 808' on one channel and one track at the same bars, a 'Fill', 808 slides) - the review
repros perf_identity / perf_slide2 (F3, C1/C2 in internal write-ups):
style none is a byte-exact no-op; natural keeps every heard note and slide on its channel and every note in its own
pattern; the read-back is ok; undo is exact; unique_repeats copies the repeats and keeps every note.
Builds the song with tests/test_newsong.py's own setup (its first part). Run: python3 tests/test_perform_repro.py"""
import json, os, sys
from collections import Counter
HERE = os.path.dirname(os.path.abspath(__file__))
T = os.path.join(HERE, "test_newsong.py")
src = open(T).read()
if "# 3) house at producer edition" not in src:
    print("skipped: test_newsong layout changed (its first part is this test's setup)"); sys.exit(0)
src = src[:src.index("# 3) house at producer edition")]
_old_file = __file__; __file__ = T                                   # noqa: F821 (the newsong setup reads __file__)
_out = sys.stdout
_cap = None
try:
    import io
    sys.stdout = _cap = io.StringIO()                                # its prints aren't this test's
    exec(compile(src, T, "exec"))
except SystemExit:                                                   # the setup skipped itself: say why, never silent
    sys.stdout = _out
    why = [x for x in _cap.getvalue().splitlines() if "skipped" in x]
    print("skipped: test_newsong's setup didn't run" + (f" ({why[-1]})" if why else "")); sys.exit(0)
finally:
    sys.stdout = _out
__file__ = _old_file
import flh_perform as PF, flh_score as SC  # noqa: E402
SC.register(ns); PF.register(ns)                                     # noqa: F821
m.fl_running = lambda: False                                         # noqa: F821
p = r["project"]                                                     # noqa: F821


def song(path):
    d = m._load(path); s, _ = m._song_x(d, m._events(d)); return s     # noqa: F821


def per_pattern(path):
    d = m._load(path); pats = m._patterns(d, m._events(d))             # noqa: F821
    return {pid: sorted((r_[2], r_[4], bool(r_[1] & m.NOTE_SLIDE)) for r_ in v["notes"]) for pid, v in pats.items()}  # noqa: F821


b = song(p); raw = open(p, "rb").read(); pp0 = per_pattern(p)
cb = Counter(n[2] for n in b); slides_b = sum(n[5] for n in b)
assert slides_b > 0 and any("808" in str(c["name"]) for c in m.flp_playlist(p)["clips"])  # noqa: F821
# perf_identity: style none, every part forced -> nothing moves, nothing is written
PF.flp_perform_setup(p, style="none")
res = PF.flp_perform(p, parts="all")
assert open(p, "rb").read() == raw and "nothing" in str(res["edit"]), res
assert not res["sections_changed"] and res["write"]["parts"] == [], res
# perf_slide2: natural keeps every heard note and slide, every note in its pattern, read-back ok
PF.flp_perform_setup(p, style="natural")
res = PF.flp_perform(p)
a = song(p)
assert Counter(n[2] for n in a) == cb, (cb, Counter(n[2] for n in a))
assert sum(n[5] for n in a) == slides_b, (slides_b, sum(n[5] for n in a))
assert per_pattern(p) == pp0, "a note moved to another pattern, or changed key / slide"
assert res["verify"]["ok"] and not res["verify"]["problems"], res["verify"]
assert any("repeated clips" in w for w in res["warnings"]), res["warnings"]
print(f"natural: {len(a)} notes, {slides_b} slides heard as before; " + json.dumps(res["edit"]["results"][0]))
PF.flp_perform_undo(p)
assert sorted(n[:6] for n in song(p)) == sorted(n[:6] for n in b), "undo must restore the song exactly"
# unique_repeats: the repeats get their own copies, nothing lost, undo exact in sound
PF.flp_perform_setup(p, style="natural")
res = PF.flp_perform(p, unique_repeats=True)
a = song(p)
assert res["verify"]["ok"] and res["write"].get("unique_copies"), res
assert Counter(n[2] for n in a) == cb and sum(n[5] for n in a) == slides_b
assert not any("repeated clips" in w for w in res.get("warnings", [])), res.get("warnings")
PF.flp_perform_undo(p)
assert sorted(n[:6] for n in song(p)) == sorted(n[:6] for n in b), "undo after unique copies: the same sound"
print(f"unique_repeats: {res['write']['unique_copies'][:60]}...")
# flp_replace_part (hand-written specs still write through it): the song's own notes in song time go back into the
# same patterns - the 'Verse 808' notes that share ticks and keys with 'Hook 808' stay in 'Verse 808'
cp = p[:-4] + "_rp.flp"; open(cp, "wb").write(raw)
for ch in sorted(cb):
    notes = [{"tick": n[0], "ticks": n[1], "key": n[3], "vel": n[4], "fl": m._fl_fields(n[7]), "slide": n[5]}  # noqa: F821
             for n in b if n[2] == ch]
    m.flp_replace_part(cp, cp, ch, notes=notes)                     # noqa: F821
assert sorted(n[:6] for n in song(cp)) == sorted(n[:6] for n in b), "flp_replace_part identity on stacked layers"
print("flp_replace_part: identity exact on stacked 808 layers")
print("ok")
