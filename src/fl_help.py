"""FL Studio manual lookup: search Image-Line's official online manual and pull page text.

Stdlib only. The TOC is the manual's left frame (Index_Frame_Left.html): a nested list of
absolute <a href=".../html/xxx.htm"> links. Pages are static HTML (not JS-rendered).
Public API: search(), page(), answer_context(). Network failures return error dicts.
"""
import hashlib, json, math, os, re, time, urllib.error, urllib.parse, urllib.request, zlib
from html.parser import HTMLParser

BASE = "https://www.image-line.com/fl-studio-learning/fl-studio-online-manual/"
TOC_URL = BASE + "Index_Frame_Left.html"
TOC_TTL, PAGE_TTL = 14 * 86400, 30 * 86400
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) fl-headless-help/1.0"
CACHE_DIR = os.path.join(os.environ["LOCALAPPDATA"], "fl-headless", "manual") if os.environ.get(
    "LOCALAPPDATA") else os.path.join(os.path.expanduser("~"), ".cache", "fl-headless", "manual")
_TOC_MEM = None  # (loaded_at, entries)

STOP = set("""a an the and or of to in on for with by from at as is are be can do does did how i my me
we you your it its this that what which when where why who into onto use using used want make get way
there their them then than so if not no yes just about please fl studio flstudio""".split())
# FL vocabulary -> manual wording. Multi-word keys are matched as phrases.
SYNONYMS = {
    "channel rack": ["channelrack", "channel"], "step sequencer": ["channelrack", "step"],
    "piano roll": ["pianoroll"], "automation clip": ["automationclip", "automation", "clip"],
    "time signature": ["songsettings", "time", "project", "settings"], "mixer track": ["mixer", "track"],
    "audio interface": ["asio", "audio", "settings"], "project settings": ["songsettings", "project"],
    "fx": ["effect", "plugin"], "insert": ["mixer", "track"], "render": ["export"],
    "bounce": ["render", "export"], "mixdown": ["export"], "mp3": ["export"], "wav": ["export", "wave"],
    "sidechain": ["sidechain", "routing", "mixer"], "vst": ["plugin", "wrapper"], "bpm": ["tempo"],
    "tempo": ["bpm"], "record": ["recording"], "latency": ["asio", "buffer", "audio"],
    "shortcut": ["shortcuts", "keyboard"], "hotkey": ["shortcuts", "keyboard"], "quantize": ["quantizer"],
    "arp": ["arpeggiator"], "eq": ["equalizer", "parametric"], "controller": ["midi"],
    "options": ["envsettings", "settings"], "sample": ["sampler", "audio"],
}


def _stem(w):
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    return w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w


def _words(text):
    return [_stem(w) for w in re.findall(r"[a-z0-9]+", (text or "").lower())]


def _query_terms(q):
    """{term: weight}: the user's words 1.0, synonym expansions 0.6."""
    ql, terms = " %s " % " ".join(re.findall(r"[a-z0-9]+", q.lower())), {}
    for phrase, extra in SYNONYMS.items():
        if " " in phrase and f" {phrase} " in ql:
            terms.update({_stem(e): 0.6 for e in extra if _stem(e) not in terms})
    for w in (w for w in _words(q) if w not in STOP):
        terms[w] = 1.0
        terms.update({_stem(e): 0.6 for e in SYNONYMS.get(w, []) if _stem(e) not in terms})
    return terms


def _match(term, words):
    if term in words:
        return 1.0
    for w in words:  # partial credit for shared stems: arpeggiator ~ arpeggiate, export ~ exporting
        n = len(os.path.commonprefix([term, w]))
        if n >= 5 and n >= min(len(term), len(w)) - 2:
            return 0.5
    return 0.0


ALLOWED_HOST = "image-line.com"
MAX_BYTES = 5_000_000                                    # a manual page is ~50-300 kB; anything bigger is refused
DEADLINE_S = 45.0                                        # whole request, not just each socket read


def host_ok(url):
    """Only https://image-line.com or https://<sub>.image-line.com (default port, no user:pass@)."""
    try:
        u = urllib.parse.urlsplit(url)
        host = (u.hostname or "").lower().rstrip(".")
        return (u.scheme == "https" and not u.username and not u.password and u.port in (None, 443)
                and (host == ALLOWED_HOST or host.endswith("." + ALLOWED_HOST)))
    except ValueError:
        return False


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    """Redirects are followed only while they stay on https image-line.com."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        newurl = urllib.parse.urljoin(req.full_url, newurl)
        if not host_ok(newurl):
            raise urllib.error.URLError(f"refused a redirect off image-line.com: {newurl[:120]}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_SafeRedirect)


def safe_get(url, headers=None, timeout=15, max_bytes=MAX_BYTES, deadline_s=DEADLINE_S):
    """GET an image-line.com https URL -> (status, {lower header: value}, body bytes, final url). Refuses other hosts
    and off-host redirects, caps the body (and a gzip body after decompression) at max_bytes, and gives up after
    deadline_s in total. 304 / 4xx / 5xx return their status (no raise)."""
    if not host_ok(url):
        raise ValueError(f"fl-headless only fetches https pages from {ALLOWED_HOST}: {str(url)[:120]}")
    req = urllib.request.Request(urllib.parse.quote(url, safe=":/?#=&%~+,;@!$'()*"),
                                 headers=dict({"User-Agent": UA, "Accept-Encoding": "gzip"}, **(headers or {})))
    end = time.time() + float(deadline_s)
    try:
        r = _OPENER.open(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        if not host_ok(e.geturl() or url):
            raise ValueError("refused a response from off image-line.com")
        return e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, b"", url
    with r:
        final = r.geturl() or url
        if not host_ok(final):
            raise ValueError(f"refused a response from off image-line.com: {final[:120]}")
        hd = {k.lower(): v for k, v in r.headers.items()}
        if int(hd.get("content-length") or 0) > max_bytes:
            raise ValueError(f"response too large ({hd['content-length']} bytes > {max_bytes})")
        gz = hd.get("content-encoding", "").lower() == "gzip"
        dec = zlib.decompressobj(16 + zlib.MAX_WBITS) if gz else None
        out, size = [], 0
        while True:
            if time.time() > end:
                raise TimeoutError(f"download took longer than {deadline_s:g} s")
            chunk = r.read(65536)
            if not chunk:
                break
            if dec is not None:
                chunk = dec.decompress(chunk, max_bytes + 1 - size)
                if dec.unconsumed_tail:
                    raise ValueError(f"response too large after decompression (> {max_bytes} bytes)")
            size += len(chunk)
            if size > max_bytes:
                raise ValueError(f"response too large (> {max_bytes} bytes)")
            out.append(chunk)
        return r.status, hd, b"".join(out), final


def _fetch(url):
    """GET url (https image-line.com only) -> str, one retry. Raises on failure; callers turn that into error dicts."""
    last = None
    if not host_ok(url):
        raise ValueError(f"fl-headless only fetches https pages from {ALLOWED_HOST}")
    for attempt in range(2):
        try:
            st, hd, raw, _ = safe_get(url)
            if st != 200:
                raise RuntimeError(f"HTTP {st}")
            m = re.search(r"charset=([\w-]+)", hd.get("content-type", ""), re.I)
            cs = m.group(1) if m else None
            for enc in [cs or "utf-8", "utf-8", "cp1252"]:
                try:
                    return raw.decode(enc)
                except (UnicodeDecodeError, LookupError):
                    pass
            return raw.decode("utf-8", "replace")
        except ValueError as e:  # refused (host, size): no retry
            last = e; break
        except Exception as e:  # noqa: BLE001 - any network/HTTP error
            last = e
            if not attempt:
                time.sleep(1)
    raise RuntimeError(f"fetch failed for {url}: {last}")


def _cache_path(kind, key):
    return os.path.join(CACHE_DIR, f"{kind}_{hashlib.sha1(key.encode()).hexdigest()}.json")


def _cache_get(kind, key, ttl):
    """-> (data, is_fresh); (None, False) when missing/corrupt."""
    try:
        with open(_cache_path(kind, key), encoding="utf-8") as f:
            d = json.load(f)
        return d["data"], time.time() - d["t"] < ttl
    except (OSError, ValueError, KeyError):
        return None, False


def _cache_put(kind, key, data):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        path = _cache_path(kind, key)
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump({"t": time.time(), "data": data}, f)
        os.replace(path + ".tmp", path)
    except OSError:
        pass


def _in_manual(url, prefix_path):
    """url is an Image-Line page under prefix_path (scheme and www. don't matter)."""
    u = urllib.parse.urlsplit(url)
    host = (u.hostname or "").lower()
    return (host == "image-line.com" or host.endswith(".image-line.com")) and u.path.startswith(prefix_path)


class _TocParser(HTMLParser):
    """Manual links plus a breadcrumb built from the labels of enclosing <li>s."""

    def __init__(self, base=None):
        super().__init__()
        self.depth, self.crumbs, self.entries, self.href, self.buf, self.li = 0, {}, [], None, [], False
        self.base = base or TOC_URL
        self.prefix = urllib.parse.urlsplit(urllib.parse.urljoin(self.base, "./")).path  # only this manual's pages

    def handle_starttag(self, tag, attrs):
        if tag in ("ul", "ol"):
            self.depth += 1
        elif tag == "li":
            self.li, self.crumbs = True, {k: v for k, v in self.crumbs.items() if k < self.depth}
        elif tag == "a":
            self.href, self.buf = dict(attrs).get("href") or "", []

    def handle_endtag(self, tag):
        if tag in ("ul", "ol"):
            self.depth = max(0, self.depth - 1)
        elif tag == "a" and self.href is not None:
            text, url = " ".join("".join(self.buf).split()), urllib.parse.urljoin(self.base, self.href.strip())
            self.crumbs.setdefault(self.depth, text)
            if text and _in_manual(url, self.prefix) and re.search(r"\.html?(#|$)", url):
                sec = " > ".join(v for k, v in sorted(self.crumbs.items()) if k < self.depth)
                self.entries.append({"title": text, "url": url, "section": sec})
            self.href, self.li = None, False

    def handle_data(self, data):
        if self.href is not None:
            self.buf.append(data)
        elif self.li and data.strip():  # unlinked folder label, e.g. <li><b>System Settings</b>
            self.crumbs.setdefault(self.depth, " ".join(data.split()))
            self.li = False


class _TextParser(HTMLParser):
    SKIP = {"script", "style", "nav", "header", "footer", "noscript", "iframe", "svg", "form", "button",
            "select", "template", "head"}
    BLOCK = {"p", "div", "br", "tr", "table", "ul", "ol", "dl", "dt", "dd", "blockquote", "pre", "section"}
    MAIN_IDS = {"idcontent", "innerdiv", "content", "main-content", "topic", "article"}

    def __init__(self):
        super().__init__()
        self.skip, self.main, self.main_tag, self.title, self.in_title, self.all, self.pref = \
            0, 0, None, "", False, [], []

    def _out(self, s):
        self.all.append(s)
        if self.main:
            self.pref.append(s)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        self.in_title |= tag == "title"
        self.skip += tag in self.SKIP
        if tag == self.main_tag:
            self.main += 1
        elif not self.main and (tag in ("main", "article") or (a.get("id") or "").lower() in self.MAIN_IDS):
            self.main_tag, self.main = tag, 1
        if self.skip:
            return
        for anchor in filter(None, (a.get("id"), a.get("name"))):
            self._out(f"\x00{anchor}\x00")  # anchor marker, resolved to an offset in _clean
        is_h = re.fullmatch(r"h[1-6]", tag)
        self._out("\n\n## " if is_h else "\n- " if tag == "li" else " | " if tag in ("td", "th")
                  else "\n" if tag in self.BLOCK else "")

    def handle_endtag(self, tag):
        self.in_title &= tag != "title"
        self.skip = max(0, self.skip - (tag in self.SKIP))
        if tag == self.main_tag and self.main:
            self.main -= 1
        if not self.skip and (re.fullmatch(r"h[1-6]", tag) or tag in ("p", "li", "tr", "table")):
            self._out("\n")

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        elif not self.skip:
            self._out(re.sub(r"\s+", " ", data))  # source newlines are not line breaks


def _clean(raw):
    """Collapse whitespace, drop empty bullets/cells; -> (text, {anchor: offset})."""
    mk, lines, pending = r"(?:\x00[^\x00]*\x00)", [], ""
    for line in raw.split("\n"):
        line = re.sub(r"^(" + mk + r"*)\|\s*", r"\1", re.sub(r"[ \t\r\f\v\xa0]+", " ", line).strip())
        if re.sub(mk, "", line).strip() in ("", "-", "##", "|"):
            pending += "".join(re.findall(mk, line))  # keep anchors; attach them to the next real line
            continue
        lines.append(pending + line)
        pending = ""
    text, anchors, out, pos = re.sub(r"\n(?=" + mk + r"*## )", "\n\n", "\n".join(lines)), {}, [], 0
    for m in re.finditer(mk, text):
        out.append(text[pos:m.start()])
        anchors.setdefault(m.group()[1:-1], sum(map(len, out)))
        pos = m.end()
    out.append(text[pos:])
    return "".join(out).strip(), anchors


def _extract(html):
    p = _TextParser()
    p.feed(html)
    p.close()
    pref = _clean("".join(p.pref))
    text, anchors = pref if len(pref[0]) > 200 else _clean("".join(p.all))
    title = re.sub(r"\s+[-|]\s+FL Studio$", "", " ".join(p.title.split()))
    m = None if title else re.search(r"^## (.+)$", text, re.M)
    return {"title": m.group(1) if m else title, "text": text, "anchors": anchors}


def _toc():
    """TOC entries (memory -> disk -> network; stale disk copy if the network fails)."""
    global _TOC_MEM
    if _TOC_MEM and time.time() - _TOC_MEM[0] < TOC_TTL:
        return _TOC_MEM[1]
    data, fresh = _cache_get("toc", TOC_URL, TOC_TTL)
    if not fresh:
        try:
            p = _TocParser()
            p.feed(_fetch(TOC_URL))
            if not p.entries:
                raise RuntimeError("manual TOC parsed to 0 links (page layout changed?)")
            data = p.entries
            _cache_put("toc", TOC_URL, data)
        except Exception:
            if not data:
                raise
    _TOC_MEM = (time.time(), data)
    return data


def search(query, limit=5):
    """Rank manual pages for a question -> [{title, url, score, snippet}] or [{"error": ...}]."""
    try:
        entries = _toc()
    except Exception as e:  # noqa: BLE001
        return [{"error": f"FL Studio manual index unavailable: {e}", "toc_url": TOC_URL}]
    terms, seen, docs = _query_terms(query), set(), []
    for e in entries:  # dedupe repeated TOC entries
        if e["url"] not in seen:
            seen.add(e["url"])
            name = urllib.parse.urlsplit(e["url"]).path.rsplit("/", 1)[-1].replace("_", " ")
            docs.append((e, set(_words(e["title"])), set(_words(name)), set(_words(e["section"]))))
    # rarer words count more ("arpeggiator" beats "piano", which every Piano Roll child mentions)
    idf = {t: math.log(1 + len(docs) / (1 + sum(t in d[1] | d[2] | d[3] for d in docs))) for t in terms}
    qnorm, scored = " ".join(w for w in _words(query) if w not in STOP), []
    for i, (e, tw, uw, sw) in enumerate(docs):
        body = _cache_get("page", e["url"].split("#")[0], float("inf"))[0]
        bw = _words(body["text"]) if body else []
        s = sum(w * idf[t] * (10 * _match(t, tw) + 4 * _match(t, uw) + 4 * _match(t, sw)
                              + 0.3 * min(bw.count(t), 5)) for t, w in terms.items())
        if s and qnorm and qnorm in " ".join(_words(e["title"])):
            s += 8
        if s > 0:
            snip = e["section"]
            if body:
                low = body["text"].lower()
                hit = min([low.find(t) for t in terms if t in low] or [0])
                snip = body["text"][max(0, hit - 60):hit + 160].replace("\n", " ")
            scored.append((-s, len(e["title"]), i, {"title": e["title"], "url": e["url"],
                                                    "score": round(s, 2), "snippet": snip}))
    return [x[3] for x in sorted(scored, key=lambda x: x[:3])[:limit]]


def page(url_or_title, max_chars=6000):
    """Main text of a manual page -> {title, url, text[, truncated]} or {"error": ...}.
    Accepts a full URL (a #fragment starts the text at that section), a file name like
    'mixer.htm', or a title/question (best search hit)."""
    ref = str(url_or_title or "").strip()
    if re.match(r"(?i)https?://", ref):
        if re.match(r"(?i)http://", ref):
            ref = "https://" + ref[7:]                     # Image-Line serves the manual over https
        if not (host_ok(ref) and _in_manual(ref, "/fl-studio-learning/")):
            return {"error": "fl_manual_page only opens FL Studio manual pages on https://www.image-line.com "
                             "(fl-studio-learning/...); give a manual URL, a page name like 'mixer.htm', or a question"}
    elif re.match(r"(?i)[a-z][a-z0-9+.-]*:", ref) and not re.fullmatch(r"[\w/ .-]+\.html?(#[\w-]+)?", ref):
        return {"error": f"unsupported link {ref[:60]!r}: only https://www.image-line.com manual pages"}
    if not re.match(r"https://", ref):
        if re.fullmatch(r"[\w/ .-]+\.html?(#[\w-]+)?", ref):
            ref = urllib.parse.urljoin(BASE + "html/", ref)
        else:
            hits = search(ref, 1)
            if not hits or "error" in hits[0]:
                return {"error": hits[0]["error"] if hits else f"no manual page matches {ref!r}"}
            ref = hits[0]["url"]
    url, _, frag = ref.partition("#")
    data, fresh = _cache_get("page", url, PAGE_TTL)
    hb = _handbook()
    if not fresh and hb:
        try:
            local = hb.stored_page(url)
            if local:
                data, fresh = local, True
        except Exception:  # noqa: BLE001 - a broken local index never blocks the online path
            pass
    if not fresh:
        try:
            data = _extract(_fetch(url))
            _cache_put("page", url, data)
        except Exception as e:  # noqa: BLE001
            if not data:
                return {"error": f"could not load manual page: {e}", "url": ref}
    text = data["text"]
    if frag in data["anchors"]:
        text = f"## {data['title']} (section #{frag})\n\n" + text[data["anchors"][frag]:].lstrip()
    out = {"title": data["title"], "url": ref, "text": text[:max_chars]}
    if len(text) > max_chars:
        out["truncated"] = True
    return out


def _handbook():
    """flh_handbook (local manual index + fl-headless cards) when importable, else None."""
    try:
        import flh_handbook
        return flh_handbook
    except Exception:  # noqa: BLE001
        return None


def _notes(hb, question, n=2):
    try:
        terms = hb.query_terms(question)
        return [hb.card_result(s, c, terms) for s, c in hb.card_hits(question, n)]
    except Exception:  # noqa: BLE001
        return []


NOTE = ("Excerpts from Image-Line's official FL Studio manual. Quote menu paths exactly as written (e.g. "
        "'OPTIONS > MIDI settings') and cite the page url. 'notes' are fl-headless's own notes (not the manual).")


def answer_context(question, pages=3, max_chars=9000, version=None):
    """Search + the top pages' text (max_chars split evenly) for the assistant to read.
    Uses the local manual index (flh_handbook_build) when one is built, else the online TOC search."""
    hb = _handbook()
    notes = _notes(hb, question) if hb else []
    if hb:
        try:
            hits, info, vnote = hb.search_manual(question, max(pages, 5), version)
        except Exception:  # noqa: BLE001 - fall back to the online path
            hits, info, vnote = [], None, None
        if info and hits:
            per, seen, got = max_chars // max(pages, 1), set(), []
            for h in hits:
                if len(got) >= pages or h["url"].split("#")[0] in seen:
                    continue
                seen.add(h["url"].split("#")[0])
                got.append(hb.section_text(h, info, per))
            res = [{k: v for k, v in h.items() if k != "_doc"} for h in hits]
            out = {"question": question, "results": res, "pages": got, "notes": notes,
                   "source": f"local manual index (FL {info['manual_version']}, built {info['built']})", "note": NOTE}
            if vnote:
                out["version_note"] = vnote
            if not notes:
                out.pop("notes")
            return out
    hits = search(question, max(pages, 5))
    if hits and "error" in hits[0]:
        out = {"question": question, "error": hits[0]["error"], "results": [], "pages": []}
        if notes:
            out["notes"] = notes
        return out
    got = [p for p in (page(h["url"], max_chars // max(pages, 1)) for h in hits[:pages]) if "error" not in p]
    out = {"question": question, "results": hits, "pages": got, "note": NOTE}
    if notes:
        out["notes"] = notes
    if hb:
        try:
            import flh_common
            build = flh_common.tool_call(getattr(hb, "H", None), "flh_handbook_build")
        except Exception:  # noqa: BLE001
            build = "flh_handbook_build()"
        out["tip"] = f"{build} makes a local index of the manual (full-text search, works offline)"
    return out
