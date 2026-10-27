"""Server overhead caches: spliced projects come with their event list (no full re-parse) that always equals a full
parse; flp_info / arrangements cached by content with fresh copies per caller; state.json parsed once while it is
unchanged (any rewrite - ours, another server's - is seen); tool signatures worked out once; flh_reload uses a
compiled copy only when it provably matches the source; the render-cache fingerprint memo keyed by content.
Run: python3 tests/test_core_speed.py"""
import json
import os
import random
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE); sys.path.insert(0, os.path.join(ROOT, "src"))
TMP = tempfile.mkdtemp(prefix="flh_speed_core_")
os.environ["FLH_ROOTS"] = TMP; os.environ["LOCALAPPDATA"] = os.path.join(TMP, "local")
import fl_headless_mcp as m  # noqa: E402
import fl_render_cache as RC  # noqa: E402

m.fl_running = lambda: False
CORPUS = os.path.join(HERE, "corpus")
try:
    # ========================================================================= spliced events = a full parse
    rnd = random.Random(7)
    seeded0 = m._PARSE_STATS["seeded"]; tried = 0
    for fx in sorted(f for f in os.listdir(CORPUS) if f.endswith(".flp"))[:12]:
        d = open(os.path.join(CORPUS, fx), "rb").read()
        for _ in range(12):
            ev = m._events(d); edits = []
            for k in sorted(rnd.sample(range(len(ev) + 1), rnd.randint(1, 3))):
                r = rnd.random()
                if r < 0.35:
                    edits.append((k, k, m._data_event(201, os.urandom(rnd.randrange(0, 300)))))   # insert
                elif k < len(ev) and r < 0.6:
                    edits.append((k, k + 1, b""))                                                # delete
                elif k < len(ev):
                    edits.append((k, k + 1, m._data_event(202, os.urandom(rnd.randrange(0, 400)))))  # replace
            out = m._splice(d, ev, edits); tried += 1
            got = (m._PARSE_CACHE.get(out) or {}).get("events")
            if got is not None:
                assert got == m._events_parse(out), (fx, edits)
            assert m._events(out) == m._events_parse(out)
            if len(out) > 8000:
                d = out
    assert m._PARSE_STATS["seeded"] - seeded0 > tried * 0.8, (m._PARSE_STATS, tried)
    # an event list that isn't the cached parse of d (filtered) is never used to seed
    d = open(os.path.join(CORPUS, "base.flp"), "rb").read()
    ev = m._events(d); part = [e for e in ev if e["id"] != m.EV_NOTES]
    s0 = m._PARSE_STATS["seeded"]
    k = next(i for i, e in enumerate(part) if e["id"] == m.EV_TEMPO)
    m._splice(d, part, [(k, k + 1, m._data_event(m.EV_TEMPO, b"\x10\x27\x00\x00")[:1] + b"\x10\x27\x00\x00")])
    assert m._PARSE_STATS["seeded"] == s0
    # edit bytes that don't parse into whole events inside the edit: not seeded, the full parse decides
    s0 = m._PARSE_STATS["seeded"]
    k = len(ev) // 2
    bad = m._splice(d, ev, [(k, k + 1, b"\xd0\x85")])               # a length that runs into the next events
    assert m._PARSE_STATS["seeded"] == s0 and (m._PARSE_CACHE.get(bad) or {}).get("events") is None
    os.environ["FLH_PARSE_CACHE"] = "0"                              # cache off: nothing seeded
    s0 = m._PARSE_STATS["seeded"]; ev = m._events(d)
    m._splice(d, ev, [(3, 3, m._data_event(201, b"x" * 40))])
    assert m._PARSE_STATS["seeded"] == s0
    os.environ["FLH_PARSE_CACHE"] = "1"
    print("spliced events ok", m._PARSE_STATS["seeded"] - seeded0, "of", tried, "seeded")

    # ========================================================================= writers + readers agree with no cache
    p = os.path.join(TMP, "Song.flp"); shutil.copy(os.path.join(CORPUS, "mixed_kitchen_sink.flp"), p)
    m.flp_playlist(p, "unmute", output=p, clips=[c["id"] for c in m.flp_playlist(p, "list")["clips"]   # its pattern
                                                 if c["kind"] == "pattern" and c["muted"]])        # clip is muted
    q = os.path.join(TMP, "Song_nc.flp"); shutil.copy(p, q)
    notes = [{"note": 48 + i % 24, "bar": 1 + i % 8, "beat": (i % 4), "beats": 0.5} for i in range(64)]
    for path, cache in ((p, "1"), (q, "0")):
        os.environ["FLH_PARSE_CACHE"] = cache
        m.flp_add_notes(path, notes, pattern=1, channel=0)
        m.flp_set_tempo(path, 97)
        m.flp_add_notes(path, notes[:5], pattern=1, channel=1)
    os.environ["FLH_PARSE_CACHE"] = "1"
    assert open(p, "rb").read() == open(q, "rb").read()             # the same bytes either way

    # ========================================================================= flp_info: cached by content, fresh copies
    os.environ["FLH_PARSE_CACHE"] = "0"; ref = m.flp_info(p); os.environ["FLH_PARSE_CACHE"] = "1"
    a = m.flp_info(p); b = m.flp_info(p)
    assert a == ref and b == ref and a is not b and json.dumps(a, sort_keys=True) == json.dumps(ref, sort_keys=True)
    assert all(isinstance(k, int) for k in a["notes_per_channel"])  # channel keys stay numbers
    a["notes_per_channel"].clear(); a["tempo_bpm"] = 1
    assert m.flp_info(p) == ref                                      # a caller's edits never reach the cache
    other = os.path.join(TMP, "Other.flp"); shutil.copy(p, other)
    assert m.flp_info(other)["path"] == other                        # same content, its own path
    n0 = ref["notes_per_channel"][0]
    m.flp_add_notes(p, notes[:3], pattern=1, channel=0)              # our write: the next read sees it
    assert m.flp_info(p)["notes_per_channel"][0] == n0 + 3
    raw = bytearray(open(other, "rb").read())                        # an outside rewrite (same size, old mtime)
    ev = m._events(bytes(raw)); k = next(i for i, e in enumerate(ev) if e["id"] == m.EV_TEMPO)
    raw[ev[k]["ps"]:ev[k]["end"]] = (123000).to_bytes(4, "little")
    st = os.stat(other); open(other, "wb").write(bytes(raw)); os.utime(other, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert m.flp_info(other)["tempo_bpm"] == 123
    arr = m._arrangements(m._load(p), m._events(m._load(p)))
    arr[0]["name"] = "hacked"; arr.clear()
    assert m._arrangements(m._load(p), m._events(m._load(p)))[0]["name"] != "hacked"
    print("flp_info cache ok")

    # ========================================================================= state.json parsed once while unchanged
    m._state({"reload_mode": "auto", "nested": {"a": [1, 2]}})
    old = time.time() - 30
    os.utime(m.STATE, (old, old))
    s1 = m._state(); s1["nested"]["a"].append(99); s1["x"] = 1      # callers own their copy
    assert m._state()["nested"]["a"] == [1, 2] and "x" not in m._state()
    assert m._STATE_MEMO["key"] is not None                          # unchanged and old: memo in use
    v = m._state_get("nested"); v["a"].clear()
    assert m._state_get("nested") == {"a": [1, 2]} and m._state_get("nope", 5) == 5
    # another server replaces the file (new content) - seen at once
    tmpf = m.STATE + ".other"
    json.dump({"reload_mode": "lazy", "nested": {"a": [3]}}, open(tmpf, "w")); os.replace(tmpf, m.STATE)
    assert m._state_get("reload_mode") == "lazy" and m._state()["nested"] == {"a": [3]}
    # an in-place rewrite with the same size inside the same mtime tick: never served from the memo (2 s rule)
    body = json.dumps({"reload_mode": "lazy", "nested": {"a": [4]}})
    open(m.STATE, "w").write(body); st = os.stat(m.STATE)
    assert m._state()["nested"] == {"a": [4]}
    open(m.STATE, "w").write(body.replace("[4]", "[5]")); os.utime(m.STATE, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert m._state()["nested"] == {"a": [5]}
    m._state({"reload_mode": None})                                  # our write: seen at once
    assert m._state_get("reload_mode") is None and m._reload_mode_setting()[0] == "auto"
    real = m._state
    m._state = lambda u=None: {"verify_alert": "from a double"}      # a test double is honoured by _state_get
    try:
        assert m._state_get("verify_alert") == "from a double"
    finally:
        m._state = real
    os.remove(m.STATE)
    assert m._state() == {} and m._state_get("reload_mode") is None
    print("state memo ok")

    # ========================================================================= tool signatures once
    def tool_a(path, bars=4):
        return {"path": path}
    ent = (tool_a, "a", m._schema(path=m._STR("x"), bars=m._INT("b", True)))
    m._SIG_CACHE.clear()
    assert m._check_args("tool_a", {"path": "x", "bars": "3"}, ent) == {"path": "x", "bars": 3}
    assert tool_a in m._SIG_CACHE
    try:
        m._check_args("tool_a", {"path": "x", "barz": 3}, ent); raise SystemExit("unknown argument accepted")
    except ValueError as e:
        assert "bars" in str(e)
    print("signature cache ok")

    # ========================================================================= flh_reload: compiled copy only when it matches
    src_dir = os.path.join(TMP, "srv"); os.makedirs(src_dir)
    f = os.path.join(src_dir, "mini_srv.py")
    open(f, "w").write("X = 1\n")
    code, how = m._reload_code(f)
    assert how == "compiled"; ns = {}; exec(code, ns); assert ns["X"] == 1
    code, how = m._reload_code(f)
    assert how == "code_cache"; ns = {}; exec(code, ns); assert ns["X"] == 1
    open(f, "w").write("X = 2\n")                                    # same size, maybe same second: the hash decides
    code, how = m._reload_code(f)
    assert how == "compiled"; ns = {}; exec(code, ns); assert ns["X"] == 2
    import py_compile
    py_compile.compile(f, doraise=True)                              # Python's own valid copy
    code, how = m._reload_code(f)
    assert how == "pycache"; ns = {}; exec(code, ns); assert ns["X"] == 2
    open(f, "w").write("X = 3  # longer now\n")                     # stale pycache (size differs): not used
    code, how = m._reload_code(f)
    assert how == "compiled"; ns = {}; exec(code, ns); assert ns["X"] == 3
    open(f, "w").write("def broken(:\n")
    try:
        m._reload_code(f); raise SystemExit("a syntax error must raise")
    except SyntaxError:
        pass
    cache_dir = os.path.join(os.path.dirname(m.STATE), "code_cache")
    assert os.listdir(cache_dir) == ["mini_srv.code"], os.listdir(cache_dir)   # one file per module, overwritten
    print("reload code ok")

    # ========================================================================= render-cache fingerprint memo by content
    RC.bind(vars(m)); RC._MEM.clear()
    d = m._load(p)
    f1 = RC.fingerprint(d); f2 = RC.fingerprint(bytes(bytearray(d)))  # a re-read: same content, new object
    assert f1 is f2
    m.flp_set_tempo(p, 131)
    f3 = RC.fingerprint(m._load(p))
    assert f3 is not f1 and f3["G"] != f1["G"]
    print("fingerprint memo ok")
finally:
    shutil.rmtree(TMP, True)
print("ok")
