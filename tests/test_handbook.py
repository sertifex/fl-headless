"""flh_handbook: cards (schema, editions vs flh_catalog, ranking), local manual index (fake HTTP: robots, crawl
delay, ETag/304, resume, host lock), BM25F ranking, synonyms, misspellings, version filtering, fl_help using the
index, explain helper, output size. Run: python3 tests/test_handbook.py (offline, no FL)."""
import json, os, shutil, sys, tempfile, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
tmp = tempfile.mkdtemp(prefix="flh_hb_")
os.environ["FLH_ROOTS"] = os.path.join(tmp, "roots"); os.makedirs(os.environ["FLH_ROOTS"])
os.environ["LOCALAPPDATA"] = os.path.join(tmp, "local")
os.environ.pop("FLH_FL_HELP", None)

import fl_headless_mcp as m  # noqa: E402
import flh_handbook as B, flh_catalog, fl_help  # noqa: E402
ns = vars(m)
B.register(ns)
assert B.HOME == os.path.join(os.path.dirname(ns["STATE"]), "handbook"), B.HOME
assert B.HOME.startswith(tmp)
for t in ("flh_handbook_search", "flh_handbook_card", "flh_handbook_build"):
    assert t in ns["TOOLS"], t
for f in ("handbook_explain", "handbook_search", "handbook_card", "handbook_quirks_text"):
    assert callable(ns[f]), f
ns["_fl_file_version"] = lambda: None          # no FL here (installed_version -> None)

# ============================================================== cards: schema, coverage, editions
C = B.load_cards(force=True)
assert not C["errors"], C["errors"]
cards = C["cards"]
ids = [c["id"] for c in cards]
assert len(ids) == len(set(ids)), "duplicate card ids"
bad = {c["id"]: B.validate_card(c) for c in cards if B.validate_card(c)}
assert not bad, bad
assert len(cards) >= 150, len(cards)
for c in cards:
    for r in c.get("related", []):
        assert r in C["by_id"], (c["id"], r)
kinds = {k: sum(c["kind"] == k for c in cards) for k in B.CARD_KINDS}
assert kinds["plugin"] >= 90 and kinds["workflow"] >= 15 and kinds["shortcuts"] >= 6 and kinds["quirk"] >= 10, kinds
for need in ("workflow-sidechain", "workflow-bussing", "workflow-bounce", "workflow-render-stems", "workflow-record-audio",
             "workflow-record-midi", "workflow-automation", "workflow-slicing", "workflow-pitch-correction",
             "workflow-templates", "workflow-project-organization", "shortcuts-piano-roll", "shortcuts-playlist",
             "menu-export-audio", "quirk-second-instance"):
    assert need in C["by_id"], need
# every plugin in flh_catalog's edition table has a card, and editions agree
plug = {}
for c in cards:
    if c["kind"] == "plugin":
        for n in [c["plugin"]] + c.get("aliases", []):
            plug[n.lower()] = c
missing = [k for k in flh_catalog.NATIVE_MIN if k.lower() not in plug and k not in ("Audio clip", "Automation clip")]
assert not missing, missing
for k, ed in flh_catalog.NATIVE_MIN.items():
    c = plug.get(k.lower())
    if c and c["plugin"].lower() == k.lower():
        assert c["edition"] == ed, (k, c["edition"], ed)
# plugins the catalog doesn't list: 'Fruity ...' counts as every edition there; others must be marked verify
for c in cards:
    if c["kind"] == "plugin" and c["plugin"] not in flh_catalog.NATIVE_MIN:
        assert c["edition"] == "fruity" if c["plugin"].startswith("Fruity ") else c.get("verify"), c["id"]
# schema validator catches mistakes
assert B.validate_card({"id": "Bad Id", "kind": "nope", "title": "x", "summary": "y"})
assert "plugin edition" in " ".join(B.validate_card({"id": "p", "kind": "plugin", "title": "x", "summary": "y",
                                                     "type": "effect", "plugin": "X", "edition": "gold"}))
assert B.validate_card({"id": "w", "kind": "workflow", "title": "x", "summary": "y"}) == ["workflow without steps"]
assert B.validate_card({"id": "s", "kind": "shortcuts", "title": "x", "summary": "y", "shortcuts": [["F9"]]})
assert B.validate_card({"id": "q", "kind": "quirk", "title": "x", "summary": "y", "do": "z"})  # no verified_on
assert B.validate_card({"id": "v", "kind": "concept", "title": "x", "summary": "y", "verify": "yes"})
assert B.validate_card({"id": "v", "kind": "concept", "title": "x", "summary": "y", "fl_versions": {"min": "abc"}})
# no copied manual text: cards are short, our own words (sanity: no long summaries / steps)
assert all(len(c["summary"]) <= 320 for c in cards)
assert all(len(s) <= 260 for c in cards for s in c.get("steps", []) + c.get("tips", []))

# ============================================================== card ranking, synonyms, misspellings
def top(q, k=3, **kw):
    return [c["id"] for _, c in B.card_hits(q, k, **kw)]


assert top("how do I sidechain a kick")[0] == "workflow-sidechain"
assert top("sidechian the bass")[0] == "workflow-sidechain"            # misspelling map
assert top("export stems")[0] == "workflow-render-stems"
assert top("split mixer tracks")[0] == "workflow-render-stems"          # synonym group
assert top("mixdown my song to mp3")[0] == "menu-export-audio"           # render = export = mixdown
assert top("piano roll shortcuts")[0] == "shortcuts-piano-roll"
assert top("paino roll shortcuts")[0] == "shortcuts-piano-roll"
assert top("autotune my vocals")[0] == "workflow-pitch-correction"
assert top("make a drum bus")[0] == "workflow-bussing"
assert top("record vocals")[0] == "workflow-record-audio"
assert top("Fruity Limiter")[0] == "plugin-fruity-limiter"
assert "concept-swing" in top("step sequencer swing")                   # step sequencer = channel rack
assert top("weather today in paris") == [] and top("the and of") == []
assert top("time signature")[0] == "menu-project-settings" and "plugin-gross-beat" not in top("time signature")
# version filtering of cards
assert "workflow-stem-separation" in top("stem separation isolate vocals", 5)
assert "workflow-stem-separation" not in top("stem separation isolate vocals", 5, version="21.2")
assert B.in_versions({"min": "21.2"}, "2024.1") and not B.in_versions({"min": "2024"}, "21.2")
assert B.in_versions({"max": "21"}, "21.2.3") and not B.in_versions({"max": "21"}, "2024")
assert B.vtuple("FL Studio 2024.2.1") == (2024, 2, 1) and B.vmajor("21.2") == "21" and B.vmajor(None) is None
# card lookup + version-specific shortcuts
card = B.handbook_card("Fruity Limiter")
assert card["id"] == "plugin-fruity-limiter" and card["src"] == "fl-headless note" and card["edition"] == "fruity"
assert B.handbook_card("plugin-fruity-limiter", full=False).get("controls") is None
assert B.handbook_card("sidechain")["id"] == "workflow-sidechain"
nf = B.handbook_card("zzqx blorf")
assert nf["found"] is False and "suggestions" in nf
C["by_id"]["shortcuts-windows"]["shortcuts"].append(["Ctrl+F99", "test-only", {"fl_versions": {"min": "2025"}}])
assert any(s[0] == "Ctrl+F99" for s in B.handbook_card("shortcuts-windows", version="2025.1")["shortcuts"])
assert not any(s[0] == "Ctrl+F99" for s in B.handbook_card("shortcuts-windows", version="21.2")["shortcuts"])
C["by_id"]["shortcuts-windows"]["shortcuts"].pop()
q = B.handbook_card("quirk-reopen-ignored")
assert q["src"].startswith("fl-headless quirk") and q["verified_on"]
qt = B.quirks_text()
assert "second copy" in qt and "500 - n" not in qt and len(qt) < 4000      # instructions=false kept out

# ============================================================== explain (teaching mode)
e = ns["handbook_explain"]("sidechain")
assert e["level"] == "beginner" and e["card"] == "workflow-sidechain" and "How: 1)" in e["text"], e
assert e["links"] == [] and e["src"] == "fl-headless note"
p = ns["handbook_explain"]("sidechain", level="pro")
assert p["level"] == "pro" and p["text"] != e["text"] and "Settings:" in p["text"]
pl = ns["handbook_explain"]("Gross Beat", level="beginner")
assert "Edition: signature" in pl["text"]
assert ns["handbook_explain"]("zzqx blorf") is None
assert len(e["text"]) < 1200 and len(p["text"]) < 1200

# ============================================================== fake Image-Line site
BASE = "https://www.image-line.com/fl-studio-learning/fl-studio-online-manual/"
M = BASE + "html/"


def page(title, *sections):
    body = "".join(f'<h2 id="{a}">{h}</h2><p>{t}</p>' for a, h, t in sections)
    return f"<html><head><title>{title}</title></head><body><div id='idcontent'><h1>{title}</h1>{body}</div></body></html>"


PAGES = {
    M + "mixer_routing.htm": page("Mixer Routing", ("Sidechain", "Sidechain routing",
                                  "Select the source track, right-click the destination route arrow and choose Sidechain to this track. "
                                  "Effects such as Fruity Limiter read the sidechain input."),
                                  ("Send", "Sends", "Left-click a route arrow to add a send. Send level knobs appear on the cable.")),
    M + "channelrack.htm": page("Channel Rack", ("Steps", "Step sequencer",
                               "The Channel rack holds instrument channels and a step sequencer grid for drums."),
                               ("Swing", "Swing", "The swing knob delays every second step for a shuffle feel.")),
    M + "pianoroll.htm": page("Piano Roll", ("Tools", "Piano roll tools", "Draw, paint, delete, mute, slice and select notes."),
                              ("Ghost", "Ghost channels", "Notes of other channels shown greyed out.")),
    M + "pianoroll_arpeggiate.htm": page("Arpeggiator Tool", ("Arp", "Arpeggiator", "Turns chords into arpeggio patterns. Range, time and gate.")),
    M + "pianoroll_qnt.htm": page("Quantizer Tool", ("Qnt", "Quantize", "Moves note start times to the grid. Open with Alt+Q.")),
    M + "fformats_save_export.htm": page("Exporting Audio & MIDI", ("Wav", "Export WAV",
                                         "Use FILE > Export > Wave file... to render the song. Tick Split mixer tracks to render each track to its own file."),
                                         ("Mp3", "MP3", "Compressed format for sharing. Bitrate settings.")),
    M + "playlist_automationclip.htm": page("Automation Clips", ("Create", "Creating automation clips",
                                            "Right-click a control and choose Create automation clip. Points and curves.")),
    M + "envsettings_audio.htm": page("Audio Settings", ("Asio", "ASIO devices", "Select the ASIO driver and buffer length to reduce latency. OPTIONS > Audio settings.")),
    M + "secret.htm": page("Secret", ("S", "Hidden", "robots.txt says no")),
}
TOC = ("<html><head><title>FL Studio 2025 Reference Manual</title></head><body><ul>"
       + "".join(f'<li><a href="{u}">{h}</a></li>' for u, h in [
           (M + "mixer_routing.htm", "Mixer Routing"), (M + "channelrack.htm", "Channel Rack"),
           (M + "pianoroll.htm", "Piano Roll"), (M + "pianoroll_arpeggiate.htm", "Arpeggiator"),
           (M + "pianoroll_qnt.htm", "Quantizer"), (M + "fformats_save_export.htm", "Audio & MIDI export"),
           (M + "playlist_automationclip.htm", "Automation Clips"),
           (M + "playlist_automationclip.htm#Create", "Creating automation"),
           (M + "envsettings_audio.htm", "Audio Settings"), (M + "secret.htm", "Secret"),
           ("https://support.image-line.com/ticket", "Ticket")])
       + "</ul></body></html>")
ROBOTS = "User-agent: *\nDisallow: /fl-studio-learning/fl-studio-online-manual/html/secret.htm\nCrawl-delay: 2\n"
calls, sleeps, clock = [], [], [1000.0]
mode = {"fail_after": None, "n": 0, "rate_limit_once": set()}


def fake_http(url, headers):
    calls.append((url, dict(headers)))
    mode["n"] += 1
    if mode["fail_after"] is not None and mode["n"] > mode["fail_after"]:
        raise OSError("network down")
    if url in mode["rate_limit_once"]:
        mode["rate_limit_once"].discard(url)
        return 429, {"retry-after": "3"}, b""
    if url == B.ROBOTS_URL:
        return 200, {"content-type": "text/plain"}, ROBOTS.encode()
    html = TOC if url == BASE + "Index_Frame_Left.html" else PAGES.get(url)
    if html is None:
        return 404, {}, b""
    etag = '"%x"' % (hash(html) & 0xffffff)
    if headers.get("If-None-Match") == etag:
        return 304, {"etag": etag}, b""
    return 200, {"etag": etag, "last-modified": "Mon, 01 Jan 2024 00:00:00 GMT",
                 "content-type": "text/html; charset=utf-8"}, html.encode()


def fake_sleep(s):
    sleeps.append(s)
    clock[0] += s


def fake_clock():
    return clock[0]


def run(**kw):
    return B.build(http=fake_http, sleep=fake_sleep, clock=fake_clock, **kw)


# fetch layer: host lock, robots, crawl delay, 429 backoff
fx = B.Fetcher(fake_http, delay=0.5, sleep=fake_sleep, clock=fake_clock)
try:
    fx.get("https://evil.example.com/x")
    raise AssertionError("non image-line host fetched")
except ValueError:
    pass
assert fx.allowed(M + "mixer_routing.htm") and not fx.allowed(M + "secret.htm")
assert fx.delay == 2.0                                                    # crawl-delay wins over 0.5
mode["rate_limit_once"].add(M + "pianoroll.htm")
st, hd, txt = fx.get(M + "pianoroll.htm")
assert st == 200 and "Piano roll tools" in txt and 3.0 in sleeps          # Retry-After honoured

# --- interrupted build: network dies after a few requests -> stops after 5 failures, keeps what it got
calls.clear(); sleeps.clear(); mode.update(fail_after=5, n=0)
r1 = run()
assert r1.get("stopped") and r1["fetched"] == 3 and not r1["complete"], r1   # robots + toc + 3 pages
assert r1["failed"] == 5 and "resume" in r1
assert all(s >= 2.0 for s in sleeps if s != 0) and len([s for s in sleeps if s == 2.0]) >= 3   # polite spacing
# --- resume: only the missing pages are requested
calls.clear(); mode.update(fail_after=None, n=0)
r2 = run()
page_calls = [u for u, _ in calls if u in PAGES]
assert r2["complete"] and r2["cached"] == 3 and r2["fetched"] == 5 and r2["robots_blocked"] == 1, r2
assert len(page_calls) == 5 and M + "secret.htm" not in page_calls, page_calls
assert r2["manual_version"] == "2025" and r2["pages_in_toc"] == 9 and r2["docs"] >= 10, r2
assert not any("support.image-line.com" in u for u, _ in calls)
assert os.path.isfile(os.path.join(B.HOME, "online", "index.json.gz"))
assert r2["folder"].startswith(B.HOME) and "steps_s" in r2
# --- third run: everything fresh -> no page requests at all
calls.clear()
r3 = run()
assert r3["cached"] == 8 and not [u for u, _ in calls if u in PAGES], calls
# --- refresh: conditional GETs -> 304s, nothing re-downloaded
calls.clear()
r4 = run(refresh=True)
assert r4["not_modified"] == 8 and r4["fetched"] == 0, r4
assert all("If-None-Match" in h for u, h in calls if u in PAGES)
# --- stop flag / lock
assert B.flh_handbook_build(action="stop")["stopping"] is False
B._STOP.clear()

# ============================================================== manual search on the index
def murls(q, **kw):
    return [r["url"] for r in B.search_manual(q, 5, **kw)[0]]


assert murls("sidechain to this track")[0] == M + "mixer_routing.htm#Sidechain"
assert murls("arpeggiator")[0] == M + "pianoroll_arpeggiate.htm#Arp"
assert murls("arpegiator")[0].startswith(M + "pianoroll_arpeggiate.htm")              # misspelling map
assert murls("quantizr notes")[0].startswith(M + "pianoroll_qnt.htm")                  # fuzzy vocabulary match
assert murls("step sequencer")[0].startswith(M + "channelrack.htm")
assert murls("piano roll")[0].startswith(M + "pianoroll.htm")                          # exact title beats children
assert murls("render my song to mp3")[0].startswith(M + "fformats_save_export.htm")   # render = export
assert murls("bounce stems split mixer tracks")[0] == M + "fformats_save_export.htm#Wav"
assert murls("asio buffer latency")[0].startswith(M + "envsettings_audio.htm")
assert murls("FILE > Export > Wave file")[0].startswith(M + "fformats_save_export.htm")  # menu field
assert murls("automation clip")[0].startswith(M + "playlist_automationclip.htm")
assert not any("secret" in u for u in murls("hidden secret robots"))
assert murls("zebra giraffe") == []
res = B.handbook_search("how do I sidechain a kick", 5)
assert res["results"][0]["src"] == "fl-headless note" and res["results"][0]["card"] == "workflow-sidechain"
srcs = [r["src"] for r in res["results"]]
assert "manual" in srcs and srcs.index("manual") > srcs.index("fl-headless note")
assert any(r["src"] == "manual" and r["url"].startswith(M + "mixer_routing.htm") for r in res["results"])
assert res["manual"]["version"] == "2025" and "labels" in res
for q in ("how do I sidechain a kick", "export stems", "piano roll shortcuts", "automation"):
    r = B.handbook_search(q, 5)
    assert len(r["results"]) <= 5 and len(json.dumps(r)) < 3000, (q, len(json.dumps(r)))
    assert all(len(x["snippet"]) <= 270 for x in r["results"]), q
assert len(json.dumps(B.handbook_search("mixer routing send sidechain", 12))) < 6000
assert B.handbook_search("x", 50)["results"] is not None                                # k clamped
tool = ns["TOOLS"]["flh_handbook_search"][0]("render stems", k=3)
assert len(tool["results"]) <= 3
# explain now has manual links
ex = B.handbook_explain("sidechain")
assert ex["links"] and ex["links"][0]["url"].startswith(M + "mixer_routing.htm") and "manual links" in ex["src"]
ghost = B.handbook_explain("greyed out")       # no card -> manual excerpt
assert ghost["src"] == "manual" and ghost["links"][0]["url"].startswith(M + "pianoroll.htm"), ghost

# ============================================================== version-tagged indexes
B21 = "https://www.image-line.com/fl-studio-learning/fl-studio-manual-21/"
orig_sources = B.sources
B.sources = lambda: {"latest": BASE, "by_major": {"21": B21}, "delay_s": 1.0}
TOC21 = TOC.replace("2025", "21.2").replace(BASE, B21)
_old_http = fake_http


def http21(url, headers):
    if url.startswith(B21):
        calls.append((url, dict(headers)))
        if url == B21 + "Index_Frame_Left.html":
            return 200, {}, TOC21.encode()
        old = PAGES.get(url.replace(B21, BASE))
        if old is None:
            return 404, {}, b""
        return 200, {}, old.replace("Sidechain to this track", "Sidechain to this track OLDMENU21").encode()
    return _old_http(url, headers)


r21 = B.build(version="21.2.3", http=http21, sleep=fake_sleep, clock=fake_clock)
assert r21["index"] == "online-21" and r21["manual_version"] == "21.2" and r21["complete"], r21
assert {x["key"] for x in B.indexes()} == {"online", "online-21"}
assert B.indexes()[0]["manual_version"] == "2025"                                       # newest first
h21 = B.search_manual("sidechain to this track", 3, version="21.0")
assert h21[1]["manual_version"] == "21.2" and "OLDMENU21" in json.dumps(h21[0]) and h21[2] is None
h25 = B.search_manual("sidechain to this track", 3, version="2025.1")
assert h25[1]["manual_version"] == "2025" and "OLDMENU21" not in json.dumps(h25[0])
h20 = B.search_manual("sidechain", 3, version="20.8")
assert h20[1]["manual_version"] == "2025" and "no manual index for FL 20" in h20[2]
ns["_fl_file_version"] = lambda: "21.2.3.4000"                                           # installed FL picks it
assert B.search_manual("sidechain", 3)[1]["manual_version"] == "21.2"
ns["_fl_file_version"] = lambda: None
B.sources = orig_sources
st = B.flh_handbook_build(action="status")
assert len(st["indexes"]) == 2 and st["cards"] == len(cards) and st["size_mb"] >= 0 and not st["building"]
assert all("dir" not in x and "base" not in x for x in st["indexes"])
re_ix = B.flh_handbook_build(action="index")
assert len(re_ix["rebuilt"]) == 2 and all(x["docs"] > 0 for x in re_ix["rebuilt"])

# ============================================================== fl_help uses the index (no network at all)
def boom(url):
    raise RuntimeError("network must not be used")


fl_help._fetch = boom
fl_help.CACHE_DIR = os.path.join(tmp, "flhelp_cache")
fl_help._TOC_MEM = None
ctx = fl_help.answer_context("how do I sidechain a kick", pages=2, max_chars=1000)
assert ctx["source"].startswith("local manual index") and ctx["pages"], ctx
assert ctx["results"][0]["url"].startswith(M + "mixer_routing.htm") and "_doc" not in ctx["results"][0]
assert ctx["notes"][0]["card"] == "workflow-sidechain"
assert all(len(p["text"]) <= 500 for p in ctx["pages"])
pg = fl_help.page(M + "pianoroll.htm#Ghost")                                            # from the local store
assert "error" not in pg and "Ghost channels" in pg["text"] and pg["text"].startswith("## Piano Roll (section #Ghost)"), pg
assert "error" in fl_help.page(M + "not_stored.htm")                                    # not stored, no network
assert ns["fl_help"]("export split mixer tracks", pages=1)["source"].startswith("local manual index")

# ============================================================== local help folder (no network)
helpdir = os.path.join(tmp, "FL", "Help", "html")
os.makedirs(helpdir)
for i in range(11):
    with open(os.path.join(helpdir, f"topic{i}.htm"), "w") as f:
        f.write(page(f"Local Topic {i}", (f"a{i}", f"Heading {i}", f"Local help body number {i} about wobblefrog{i}.")))
os.environ["FLH_FL_HELP"] = os.path.join(tmp, "FL", "Help")
calls.clear()
rl = B.build(version="2024.2", http=fake_http, sleep=fake_sleep, clock=fake_clock)
assert rl["source"] == "local" and rl["pages_stored"] == 11 and not calls, rl
assert B.search_manual("wobblefrog7", 1, version="2024.1")[0][0]["title"] == "Local Topic 7"
os.environ.pop("FLH_FL_HELP")
assert "error" in B.build(source="local")                                               # nothing local now

# ============================================================== background job + tool wrapper
ns["_start_job"] = m._start_job
B.H["_start_job"] = m._start_job
orig_build = B.build
B.build = lambda **kw: orig_build(http=fake_http, sleep=fake_sleep, clock=fake_clock, **kw)
j = B.flh_handbook_build(max_pages=2)
assert j["status"] == "running" and "estimate_min" in j, j
done = m.fl_job(j["job"], wait_s=20)
assert done["status"] == "done" and done["result"]["index"] == "online", done
B.build = orig_build
# a second concurrent build is refused
B._LOCK.acquire()
assert "already running" in B.build(http=fake_http)["error"]
B._LOCK.release()

shutil.rmtree(tmp, ignore_errors=True)
print("handbook ok")
