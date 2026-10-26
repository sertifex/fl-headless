"""Core leftovers of the bug hunt: a damaged state.json is never wiped by the next update (copied aside, the last
good backup restored, said once in the next tool result); job ids come from a locked counter (two jobs started at
once never share an id); a hats-only drum part has no measurable fill (None, not 0).
Run: python3 tests/test_core_state_jobs.py"""
import json, os, sys, tempfile, threading, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
tmp = tempfile.mkdtemp(prefix="flh_statejobs_")
os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = os.path.join(tmp, "local")
import fl_headless_mcp as m  # noqa: E402
import fl_musicality as MU  # noqa: E402
m.fl_running = lambda: False
SDIR = os.path.dirname(m.STATE)


def damaged_copies():
    return sorted(f for f in os.listdir(SDIR) if f.startswith("state.json.damaged-"))


def write_raw(text):
    os.makedirs(SDIR, exist_ok=True)
    with open(m.STATE, "w", encoding="utf-8") as f:
        f.write(text)
    m._STATE_MEMO["key"] = None


def call(name, **args):
    r = m.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})
    return json.loads(r["content"][0]["text"])


# ---- 1. a damaged state.json with a good backup: the backup comes back, the update is merged into it
m._state({"fl_edition": "Producer", "collection": "Album A", "theme": {"dark": True}})
assert os.path.isfile(m.STATE + ".bak"), os.listdir(SDIR)                 # first successful write makes the backup
write_raw('{"fl_edition": "Producer", "collect')                         # cut off (a crash / disk error / hand edit)
m._pop_state_news()
st = m._state({"last_tool": "x"})
assert st.get("fl_edition") == "Producer" and st.get("collection") == "Album A" and st["theme"] == {"dark": True}, st
assert st.get("last_tool") == "x", st
assert m._state_read().get("collection") == "Album A"
assert len(damaged_copies()) == 1, damaged_copies()
with open(os.path.join(SDIR, damaged_copies()[0]), encoding="utf-8") as f:
    assert f.read().startswith('{"fl_edition": "Producer", "collect')    # the damaged file itself is kept
news = m._pop_state_news()
assert len(news) == 1 and "damaged" in news[0] and "state.json.bak" in news[0], news
print("damaged state + backup: restored, update merged ok")

# ---- 2. said once in the next tool result (a plain read finds the damage)
write_raw("[1, 2")
assert m._state().get("collection") == "Album A"                         # a read recovers too
r = call("flh_tools")
r = r if isinstance(r, dict) else {}
assert "damaged" in str(r.get("state_file")), str(r)[:400]
r2 = call("flh_tools")
assert not (isinstance(r2, dict) and r2.get("state_file")), "said only once"
print("note in the next tool result, once ok")

# ---- 3. not a JSON object counts as damaged too; at most 3 damaged copies kept
for i in range(4):
    write_raw('"just a string"' if i % 2 else "{broken")
    time.sleep(0.01)
    m._state({"n": i})
assert len(damaged_copies()) == m.STATE_DAMAGED_KEEP == 3, damaged_copies()
assert m._state_read().get("n") == 3 and m._state_read().get("fl_edition") == "Producer", m._state_read()
print("damaged copies capped at 3 ok")

# ---- 4. no backup: starts empty, says so honestly, the update is still written
os.remove(m.STATE + ".bak"); m._STATE_BAK_T[0] = 0.0
write_raw("{nope"); m._pop_state_news()
st = m._state({"k": 1})
assert st == {"k": 1}, st
news = m._pop_state_news()
assert news and "no good backup" in news[0], news
assert os.path.isfile(m.STATE + ".bak")                                   # the next good write makes one again
print("no backup: empty + said ok")

# ---- 5. a Notepad-saved state.json (UTF-8 with BOM) is not 'damaged'
with open(m.STATE, "w", encoding="utf-8-sig") as f:
    json.dump({"fl_edition": "Fruity", "k": 2}, f)
m._STATE_MEMO["key"] = None; n0 = len(damaged_copies())
assert m._state_read().get("fl_edition") == "Fruity" and m._state({"k": 3})["fl_edition"] == "Fruity"
assert len(damaged_copies()) == n0 and not m._pop_state_news()
print("BOM state reads ok")

# ---- 6. job ids: a locked counter - many jobs started at once all get their own id
ids, errs, go = [], [], threading.Event()


def starter():
    go.wait()
    try:
        for _ in range(25):
            ids.append(m._start_job("idtest", lambda: 1, fl=False)["job"])
    except Exception as e:                                                   # noqa: BLE001
        errs.append(repr(e))


ts = [threading.Thread(target=starter) for _ in range(8)]
[t.start() for t in ts]; go.set(); [t.join() for t in ts]
assert not errs and len(ids) == 200 and len(set(ids)) == 200, (errs, len(ids), len(set(ids)))
assert all("+" not in j for j in ids), [j for j in ids if "+" in j][:3]
a, b = m._job_id("x"), m._job_id("x")
assert a != b and int(b.split("-")[1]) == int(a.split("-")[1]) + 1, (a, b)
m.JOBS.pop("x-999999", None)
m._JOB_SEQ[0] = 0                                                           # a counter behind JOBS (old ids): skips
m.JOBS["y-1"] = {"job": "y-1", "status": "done"}
assert m._job_id("y") == "y-2"
print("job ids unique under concurrency ok")

# ---- 7. fill_metric: hats / ride / shaker only -> not measurable (None); with skins it still measures
hats = [{"part": "hat", "s": b * 16 + q * 2} for b in range(8) for q in range(8)]
assert MU.fill_metric(hats, 16, 8) is None
kick = [{"part": "kick", "s": b * 16 + q * 4} for b in range(8) for q in range(4)]
assert MU.fill_metric(hats + kick, 16, 8) == 0.0                         # skins with no fill: measured, 0
fill = kick + [{"part": "snare", "s": b * 16 + s} for b in (3, 7) for s in (10, 12, 14)]
assert MU.fill_metric(hats + fill, 16, 8) == 1.0
r = MU.score_beat({"drums": hats, "bass": [{"k": 36, "s": b * 16, "l": 8} for b in range(8)]}, [], 16, bars=8,
                  full=True)
assert "fills" not in (r.get("detail", {}).get("form") or {}), r
assert not any("fill" in w for w in r.get("weak", [])), r
print("hats-only fill: not measurable ok")
print("ok")
