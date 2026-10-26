"""Offline tests for fl_help (FL Studio manual search). Run: python tests/test_help.py"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fl_help as H  # noqa: E402
import flh_handbook as HB  # noqa: E402
_REAL_FETCH = H._fetch

HB.HOME = tempfile.mkdtemp(prefix="flhelp_hb_")  # no local manual index -> fl_help's online path (fallback)

M = "https://www.image-line.com/fl-studio-learning/fl-studio-online-manual/html/"
# Miniature of the real TOC frame (Index_Frame_Left.html): nested lists, absolute hrefs,
# unlinked folder headings, fragment links, one non-manual link, a duplicate entry.
TOC = f"""<html><head><title>FL Studio Reference Manual</title></head><body><ul>
<li><a href="http://support.image-line.com/action/ticketing" title="Submit a support request">Ticket</a></li>
<li><a href="{M}title.htm">Title Page</a></li>
<li><b>Options &amp; Settings</b><ul>
  <li><a href="{M}envsettings_midi.htm">MIDI Settings</a></li>
  <li><a href="{M}songsettings_settings.htm">Project General Settings</a></li></ul></li>
<li><a href="{M}channelrack.htm"><b>Channel Rack</b></a><ul>
  <li><a href="{M}chansettings.htm">Channel Settings</a><ul>
    <li><a href="{M}playlist_automationclip.htm#AutomationClip_ChannelSettings">Automation Clip Setting</a></li></ul></li></ul></li>
<li><a href="{M}pianoroll.htm">Piano Roll</a><ul>
  <li><a href="{M}pianoroll_arpeggiate.htm">Arpeggiator Tool</a></li>
  <li><a href="{M}pianoroll_qnt.htm">Quantizer Tool</a></li></ul></li>
<li><a href="{M}playlist.htm">Playlist</a><ul>
  <li><a href="{M}playlist_automationclip.htm">Automation Clips</a></li></ul></li>
<li><a href="{M}effects_plugins.htm">Effect Plugins</a></li>
<li><a href="{M}mixer_index.htm">Mixer &amp; Mixing</a><ul>
  <li><a href="{M}mixer.htm">Mixer Functions</a></li>
  <li><a href="{M}mixer_plugin.htm">Plugin Effects</a></li>
  <li><a href="{M}mixer_trackprops.htm">Mixer Track Properties</a></li></ul></li>
<li><b>Load &amp; Save File Formats</b><ul><li><b>Save &amp; Export File Formats</b><ul>
  <li><a href="{M}fformats_save_export.htm">Audio &amp; MIDI</a></li></ul></li></ul></li>
<li><a href="{M}playlist_automationclip.htm">Automation Clips</a></li>
</ul></body></html>"""

EXPORT = """<html><head><title>Exporting Audio &amp; MIDI</title><style>p{color:red}</style>
<script>var tracking = "SECRET_SCRIPT";</script></head><body>
<header>SITE HEADER LOGIN</header><nav><a href="/">Home</a> NAVTEXT</nav>
<div id="idcontent"><h1>SAVE/EXPORT FILE FORMATS</h1>
<p>Export <b>.wav</b> or <b>.mp3</b> audio files to be played in a media player.</p>
<p>Use <b>FILE</b> &gt; <b>Export</b> &gt; <b>Wave file...</b> to render the final mix.</p>
<a name="ExportDialog"></a><h2>Export Project Dialog</h2>
<ul><li>Project type - <i>Full song</i></li><li>Output format</li><li>   </li></ul>
<table><tr><td>Tail</td><td>Leave remainder</td></tr></table>
<script>document.write("INLINE_SCRIPT")</script>
<h2 id="Quality">Quality</h2><p>Resampling quality for   the
   rendered   file.</p></div>
<footer>FOOTER COPYRIGHT</footer></body></html>"""

SETTINGS = """<html><head><title>Project Settings</title></head><body><table><tr><td>
<h1>PROJECT SETTINGS</h1><p>Open with <b>OPTIONS &gt; Project general settings</b> (F11).</p>
<h3>Time Settings</h3><p>Time signature: numerator is beats per bar. Polymeter quirk zebrafish.</p>
</td></tr></table></body></html>"""

PAGES = {M + "fformats_save_export.htm": EXPORT, M + "songsettings_settings.htm": SETTINGS}
calls = []


def fake_fetch(url):
    calls.append(url)
    if url == H.TOC_URL:
        return TOC
    if url in PAGES:
        return PAGES[url]
    raise RuntimeError("404 " + url)


def boom(url):
    calls.append(url)
    raise RuntimeError("network down")


def fresh(fetch):
    H.CACHE_DIR = tempfile.mkdtemp(prefix="flhelp_")
    H._TOC_MEM = None
    H._fetch = fetch
    calls.clear()
    return H.CACHE_DIR


tmp = [fresh(fake_fetch)]

# --- search ranking
r = H.search("how do I use the arpeggiator in the piano roll")
assert r[0]["url"] == M + "pianoroll_arpeggiate.htm", r
assert all(set(x) == {"title", "url", "score", "snippet"} for x in r)
assert r[0]["score"] > r[1]["score"]
assert H.search("piano roll")[0]["title"] == "Piano Roll"  # exact title beats its children
assert H.search("render my song to mp3")[0]["url"] == M + "fformats_save_export.htm"  # synonym render->export
assert H.search("how do I bounce my beat")[0]["url"] == M + "fformats_save_export.htm"
assert H.search("add fx to an insert")[0]["url"] == M + "mixer_plugin.htm"
assert H.search("change the time signature")[0]["url"] == M + "songsettings_settings.htm"
assert H.search("channel rack")[0]["url"] == M + "channelrack.htm"
assert not any("support.image-line.com" in x["url"] for x in H.search("ticket support request", 10))
ac = H.search("automation clip", 10)
assert [x["url"] for x in ac].count(M + "playlist_automationclip.htm") == 1  # duplicates collapsed
assert H.search("the of and") == []
assert H._toc()[1]["section"] == "Options & Settings", H._toc()[1]
assert len(calls) == 1  # TOC fetched once across all searches

# --- page text extraction
p = H.page(M + "fformats_save_export.htm")
t = p["text"]
assert p["title"] == "Exporting Audio & MIDI" and p["url"] == M + "fformats_save_export.htm"
assert t.startswith("## SAVE/EXPORT FILE FORMATS"), t
assert "\n\n## Export Project Dialog\n" in t and "## Quality" in t
assert "- Project type - Full song" in t and "- Output format" in t and "\n- \n" not in t
assert "FILE > Export > Wave file..." in t  # menu path intact
assert "Tail | Leave remainder" in t
assert "Resampling quality for the rendered file." in t  # whitespace collapsed
for junk in ("SECRET_SCRIPT", "INLINE_SCRIPT", "NAVTEXT", "SITE HEADER", "FOOTER", "color:red", "\x00"):
    assert junk not in t, junk
s = H.page(M + "songsettings_settings.htm")  # no content container -> whole body
assert "OPTIONS > Project general settings" in s["text"] and "## Time Settings" in s["text"]
frag = H.page(M + "fformats_save_export.htm#Quality")
assert frag["text"].startswith("## Exporting Audio & MIDI (section #Quality)\n\n## Quality"), frag["text"]
assert H.page(M + "fformats_save_export.htm#ExportDialog")["text"].split("\n\n")[1].startswith("## Export Project Dialog\n- Project type")
short = H.page(M + "fformats_save_export.htm", max_chars=50)
assert len(short["text"]) == 50 and short["truncated"]
assert H.page("songsettings_settings.htm")["title"] == "Project Settings"  # bare filename
assert H.page("time signature")["url"] == M + "songsettings_settings.htm"  # title/query lookup

# --- caching: pages and TOC come from disk, even with memory cache dropped
n = len(calls)
H.page(M + "fformats_save_export.htm")
H._TOC_MEM = None
H.search("mixer")
assert len(calls) == n, calls[n:]
assert any(f.startswith("toc_") for f in os.listdir(H.CACHE_DIR))
# cached body text is scored lightly: a word only in the settings page body
z = H.search("zebrafish polymeter")
assert z and z[0]["url"] == M + "songsettings_settings.htm" and "zebrafish" in z[0]["snippet"], z

# --- answer_context
ctx = H.answer_context("how do I export my song as wav", pages=2, max_chars=400)
assert ctx["results"][0]["url"] == M + "fformats_save_export.htm"
assert ctx["pages"] and ctx["pages"][0]["title"] == "Exporting Audio & MIDI"
assert all(len(x["text"]) <= 200 for x in ctx["pages"])
# fallback path (no local index): fl-headless notes ride along, labelled, plus a tip to build the index
assert ctx["notes"] and ctx["notes"][0]["src"] == "fl-headless note", ctx.get("notes")
assert ctx["notes"][0]["card"] == "menu-export-audio" and "flh_handbook_build" in ctx["tip"]
assert "source" not in ctx                                       # 'source' is only set for the local index
assert "notes" not in H.answer_context("zebrafish polymeter", pages=1)  # unrelated: no notes noise
# TOC parser with another manual base: only that manual's pages (any scheme / www.), crumbs kept
B2 = "https://www.image-line.com/fl-studio-learning/fl-studio-manual-21/"
tp = H._TocParser(B2 + "Index_Frame_Left.html")
tp.feed(f'<ul><li><a href="html/mixer.htm">Mixer</a></li><li><a href="http://image-line.com/fl-studio-learning/'
        f'fl-studio-manual-21/html/pr.htm">PR</a></li><li><a href="{M}other.htm">Other manual</a></li></ul>')
assert [e["url"] for e in tp.entries] == [B2 + "html/mixer.htm",
                                          "http://image-line.com/fl-studio-learning/fl-studio-manual-21/html/pr.htm"]

# --- error paths: no network, no cache
tmp.append(fresh(boom))
H.time.sleep = lambda s: None  # skip retry delay
e = H.search("mixer")
assert len(e) == 1 and "error" in e[0] and "network down" in e[0]["error"], e
assert "error" in H.page(M + "mixer.htm") and "error" in H.answer_context("mixer")
assert len(calls) >= 2  # each failed fetch attempted (retry lives inside the real _fetch)
# stale cache is used when the network fails
tmp.append(fresh(fake_fetch))
H.search("mixer")
H._TOC_MEM, H._fetch, H.TOC_TTL = None, boom, -1
assert "error" not in H.search("mixer")[0]

tmp.append(HB.HOME)
for d in tmp:
    shutil.rmtree(d, ignore_errors=True)
# ---- H2/M9: only https image-line.com, no off-host redirects, size cap, deadline (no network: fake opener)
import gzip as _gz, io as _io, urllib.error as _ue, urllib.request as _ur  # noqa: E402
for bad in ("http://169.254.169.254/latest/meta-data", "https://evil.example.com/a", "https://image-line.com.evil.io/x",
            "https://user:pw@www.image-line.com/fl-studio-learning/x.htm", "file:///etc/passwd",
            "https://www.image-line.com:8443/fl-studio-learning/x.htm", "https://www.image-line.com/shop/cart"):
    r = H.page(bad)
    assert "error" in r and "text" not in r, (bad, r)
    if "/shop/" in bad:
        continue                                                # right host, not the manual: page() refuses it
    try:
        _REAL_FETCH(bad); raise SystemExit(f"fetched {bad}")
    except ValueError:
        pass
assert H.host_ok("https://www.image-line.com/x") and H.host_ok("https://IMAGE-LINE.com/x")
assert not H.host_ok("http://www.image-line.com/x") and not H.host_ok("https://notimage-line.com/x")
rd = H._SafeRedirect()
req = _ur.Request("https://www.image-line.com/fl-studio-learning/a.htm")
for off in ("http://localhost/other", "https://evil.example.com/", "//evil.example.com/x"):
    try:
        rd.redirect_request(req, None, 302, "Found", {}, off); raise SystemExit(f"followed {off}")
    except _ue.URLError:
        pass
assert rd.redirect_request(req, None, 302, "Found", {}, "/fl-studio-learning/b.htm").full_url.startswith("https://www.image-line.com/")


class _Resp:
    def __init__(self, body, headers, url="https://www.image-line.com/fl-studio-learning/a.htm", slow=False):
        self.b = _io.BytesIO(body); self.headers = headers; self.url = url; self.status = 200; self.slow = slow
    def read(self, n=-1):
        if self.slow:
            import time as _t; _e = _t.perf_counter() + 0.05
            while _t.perf_counter() < _e:
                pass
        return self.b.read(n)
    def geturl(self):
        return self.url
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


class _Hd(dict):
    def items(self):
        return dict.items(self)


class _Opener:
    def __init__(self, resp):
        self.resp = resp; self.calls = 0
    def open(self, req, timeout=None):
        self.calls += 1; return self.resp


real_opener = H._OPENER
H._OPENER = _Opener(_Resp(b"x" * (H.MAX_BYTES + 10), _Hd()))
try:
    H.safe_get("https://www.image-line.com/a"); raise SystemExit("no size cap")
except ValueError as e:
    assert "too large" in str(e)
bomb = _gz.compress(b"\0" * (H.MAX_BYTES * 3))                       # ~15 KB that inflates to 15 MB
H._OPENER = _Opener(_Resp(bomb, _Hd({"Content-Encoding": "gzip"})))
try:
    H.safe_get("https://www.image-line.com/a"); raise SystemExit("gzip bomb not capped")
except ValueError as e:
    assert "decompression" in str(e)
H._OPENER = _Opener(_Resp(_gz.compress(b"<html>ok</html>"), _Hd({"Content-Encoding": "gzip"})))
assert H.safe_get("https://www.image-line.com/a")[2] == b"<html>ok</html>"
H._OPENER = _Opener(_Resp(b"x" * 10, _Hd(), url="https://evil.example.com/"))
try:
    H.safe_get("https://www.image-line.com/a"); raise SystemExit("off-host final url accepted")
except ValueError:
    pass
H._OPENER = _Opener(_Resp(b"y" * 70000 * 4, _Hd(), slow=True))
try:
    H.safe_get("https://www.image-line.com/a", deadline_s=0.08); raise SystemExit("no deadline")
except TimeoutError:
    pass
H._OPENER = _Opener(_Resp(b"z" * 10, _Hd()))
st, hd, body = HB._default_http("https://www.image-line.com/robots.txt", {})     # handbook goes through it too
assert body == b"z" * 10
try:
    HB._default_http("http://127.0.0.1:9/start", {}); raise SystemExit("handbook fetched localhost")
except ValueError:
    pass
H._OPENER = real_opener
print("help ok")
