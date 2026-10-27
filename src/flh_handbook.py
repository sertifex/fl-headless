"""flh_handbook: fl-headless knows FL Studio - a local manual index built on the user's PC + our own knowledge cards.

Two sources, always labelled apart in results:
- "fl-headless note": cards in data/handbook/cards/*.json and data/handbook/quirks.json. Written by the fl-headless
  authors in their own words (no manual text). Entries marked verify=true are unconfirmed.
- "manual": Image-Line's official FL Studio manual. NOTHING of it ships with fl-headless: flh_handbook_build() fetches
  the pages on the user's PC (or reads FL's local help, when the install has one) into fl-headless's state folder
  and builds a BM25F index there.

Index design: one document per manual section (pages split at their headings). Fields with their own weight and
length normalisation (BM25F): title (page title + TOC titles), heading, crumbs (TOC path), menu (menu paths like
'OPTIONS > MIDI settings' found in the text), body. Title/heading/crumbs/menu also index word bigrams so phrases
('piano roll', 'automation clip') beat scattered words. Query side: FL synonyms (channel rack = step sequencer,
insert = mixer track, render = export...) at reduced weight, common misspellings, and a fuzzy fallback for words
that aren't in the vocabulary. Stored as gzipped JSON postings (pure Python, no numpy needed), loaded once.

Fetching respects robots.txt (and its crawl-delay), goes one page at a time with a delay, sends ETag /
Last-Modified so unchanged pages cost a 304, and keeps a manifest so an interrupted build resumes where it stopped.
Only image-line.com is ever contacted.

Public (in ns): handbook_search(query, k, version), handbook_card(topic), handbook_explain(topic, level),
handbook_quirks_text(), handbook_build(...). fl_help.answer_context uses the index when one is built.
"""
import gzip, hashlib, json, math, os, re, threading, time, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data", "handbook")
HOME = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".cache"),
                    "fl-headless", "handbook")                 # user-local cache; register() moves it next to STATE
H = None                                                        # server namespace (register)
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) fl-headless-handbook/1.0"
ROBOTS_URL = "https://www.image-line.com/robots.txt"
ALLOWED_HOST = "image-line.com"
PAGE_TTL = 30 * 86400                                           # a page fetched more recently is not re-checked
SNIP = 220                                                      # snippet length in search results
FIELDS = {"title": (3.0, 0.3), "heading": (2.5, 0.4), "crumbs": (1.2, 0.3), "menu": (2.0, 0.5), "body": (1.0, 0.75)}
K1 = 1.2
PHRASE_FIELDS = ("title", "heading", "crumbs", "menu")
CARD_FIELDS = {"title": (3.0, 0.3), "tags": (2.0, 0.3), "summary": (1.5, 0.5), "body": (1.0, 0.75)}
_LOCK = threading.Lock()
_STOP = threading.Event()
_MEM = {"index": {}, "cards": None, "pages": {}}                 # caches: index per dir, cards, page texts


# ============================================================================= text
def _help():
    import fl_help
    return fl_help


def _stem(w):
    return _help()._stem(w)


def _stop():
    return _help().STOP


def tokens(text, stop=True):
    st = _stop()
    return [_stem(w) for w in re.findall(r"[a-z0-9]+", str(text or "").lower()) if not (stop and w in st)]


def _bigrams(toks):
    return [f"{a}_{b}" for a, b in zip(toks, toks[1:])]


_SYN = None


def synonyms():
    """{'groups': [[phrase, ...]], 'misspellings': {wrong: right}} from data/handbook/synonyms.json."""
    global _SYN
    if _SYN is None:
        try:
            with open(os.path.join(DATA, "synonyms.json"), encoding="utf-8") as f:
                _SYN = json.load(f)
        except (OSError, ValueError):
            _SYN = {"groups": [], "misspellings": {}}
    return _SYN


def _fix_spelling(q):
    ms = synonyms().get("misspellings", {})
    low = " " + " ".join(re.findall(r"[a-z0-9]+", str(q).lower())) + " "
    for wrong, right in sorted(ms.items(), key=lambda kv: -len(kv[0])):
        low = low.replace(f" {wrong} ", f" {right} ")
    return low.strip()


def query_terms(q, vocab=None, origin=None):
    """{term: weight}: the user's words and their bigrams 1.0; synonyms 0.6 (single words, or the bigrams of
    multi-word synonyms), loose words of multi-word synonyms 0.25; fuzzy corrections of unknown words 0.8.
    origin (dict, optional) gets term -> {concept marks}: 'cN' (the whole concept) or 'cN:word' (one word of a
    phrase concept); concepts = the user's words, with a known phrase ('piano roll') counting as one."""
    q = _fix_spelling(q)
    ql = f" {q} "
    toks = tokens(q)
    org = origin if origin is not None else {}
    concepts, used = [], set()
    for group in synonyms().get("groups", []):
        hit = [p for p in group if " " in p and f" {p} " in ql and not set(tokens(p)) <= used]
        for p in hit:
            concepts.append((tokens(p), group))
            used |= set(tokens(p))
        single = [p for p in group if " " not in p and f" {p} " in ql]
        for p in single:
            if _stem(p) not in used:
                concepts.append(([_stem(p)], group))
                used.add(_stem(p))
    concepts += [([t], None) for t in dict.fromkeys(toks) if t not in used]
    terms = {}

    def add(t, w, mark):
        if w > terms.get(t, 0):
            terms[t] = w
        if mark:
            org.setdefault(t, set()).add(mark)
    for ci, (words, group) in enumerate(concepts):
        cid = f"c{ci}"
        if len(words) == 1:
            add(words[0], 1.0, cid)
        else:
            for w in words:
                add(w, 1.0, f"{cid}:{w}")
            for b in _bigrams(words):
                add(b, 1.0, cid)
        for p in group or ():
            pt = tokens(p)
            if len(pt) == 1:
                add(pt[0], 0.6, cid)
            elif pt:
                for b in _bigrams(pt):
                    add(b, 0.6, cid)
                for w in pt:
                    add(w, 0.25, None)
    for a, b in zip(toks, toks[1:]):
        add(f"{a}_{b}", 1.0, None)
    if vocab:
        import difflib
        for ci, (words, _) in enumerate(concepts):
            if len(words) == 1 and words[0] not in vocab and len(words[0]) >= 5:
                near = difflib.get_close_matches(words[0], vocab, n=1, cutoff=0.84 if len(words[0]) < 8 else 0.8)
                if near:
                    add(near[0], 0.8, f"c{ci}")
    org["_concepts"] = {f"c{ci}": words for ci, (words, _) in enumerate(concepts)}
    return terms


def _covered(marks, concepts):
    """How many concepts a document matched (whole, or all words of a phrase)."""
    n = 0
    for cid, words in concepts.items():
        if cid in marks or (len(words) > 1 and all(f"{cid}:{w}" in marks for w in words)):
            n += 1
    return n


def primary(q):
    """The user's own (non-stop) words, after spelling fixes: used for 'does this card really match'."""
    return set(tokens(_fix_spelling(q)))


# ============================================================================= BM25F
def _field_tokens(fields, phrase_fields=PHRASE_FIELDS):
    out = {}
    for f, text in fields.items():
        toks = tokens(text)
        out[f] = toks + (_bigrams(toks) if f in phrase_fields else [])
    return out


def bm25_build(docs, fields=FIELDS, phrase_fields=PHRASE_FIELDS):
    """docs: [{field: text}] -> {"n", "terms": {term: [[doc ids], [saturated weighted tf]]}}."""
    ftoks = [_field_tokens(d, phrase_fields) for d in docs]
    avg = {f: (sum(len(ft.get(f, ())) for ft in ftoks) / max(1, len(ftoks))) or 1.0 for f in fields}
    post = {}
    for i, ft in enumerate(ftoks):
        wtf = {}
        for f, (w, b) in fields.items():
            toks = ft.get(f, ())
            if not toks:
                continue
            norm = 1 - b + b * len(toks) / avg[f]
            counts = {}
            for t in toks:
                counts[t] = counts.get(t, 0) + 1
            for t, c in counts.items():
                wtf[t] = wtf.get(t, 0.0) + w * c / norm
        for t, v in wtf.items():
            p = post.setdefault(t, ([], []))
            p[0].append(i)
            p[1].append(round(v * (K1 + 1) / (v + K1), 3))
    return {"n": len(docs), "terms": {t: [a, b] for t, (a, b) in post.items()}}


def bm25_score(index, qterms, origin=None):
    """-> {doc: [score, set of the user's words matched (only with origin)]}."""
    n, scores = index["n"], {}
    for t, qw in qterms.items():
        p = index["terms"].get(t)
        if not p:
            continue
        idf = math.log(1 + (n - len(p[0]) + 0.5) / (len(p[0]) + 0.5))
        src = origin.get(t, ()) if origin else ()
        for d, s in zip(p[0], p[1]):
            cur = scores.get(d)
            if cur is None:
                cur = scores[d] = [0.0, set()]
            cur[0] += qw * idf * s
            if src:
                cur[1].update(src)
    return scores


# ============================================================================= versions
def vtuple(v):
    """'2024.1.2' -> (2024, 1, 2); '21.2' -> (21, 2); FL 20/21 sort before 2024 naturally."""
    nums = re.findall(r"\d+", str(v or ""))
    return tuple(int(x) for x in nums[:4]) if nums else ()


def vmajor(v):
    t = vtuple(v)
    return str(t[0]) if t else None


def in_versions(spec, version):
    """spec {'min': '21', 'max': '2024.9'} or None; version None = everything matches."""
    if not spec or not version:
        return True
    v = vtuple(version)
    lo, hi = vtuple(spec.get("min")), vtuple(spec.get("max"))
    return (not lo or v >= lo) and (not hi or v[:len(hi)] <= hi)


def installed_version():
    """FL's file version on this PC (Windows), else None. Never guessed."""
    try:
        fn = (H or {}).get("_fl_file_version")
        return fn() if fn else None
    except Exception:  # noqa: BLE001 - no FL, not Windows
        return None


# ============================================================================= cards
CARD_KINDS = {"shortcuts", "menu", "plugin", "workflow", "concept", "quirk"}
PLUGIN_TYPES = {"generator", "effect", "controller", "tool", "visual"}
EDITION_KEYS = ("fruity", "producer", "signature", "all_plugins", "free")
_ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def validate_card(c):
    """-> list of problems (empty = valid)."""
    p = []
    if not isinstance(c, dict):
        return ["not an object"]
    for k in ("id", "kind", "title", "summary"):
        if not isinstance(c.get(k), str) or not c.get(k).strip():
            p.append(f"missing {k}")
    if p:
        return p
    if not _ID_RE.match(c["id"]):
        p.append("id must be kebab-case")
    if c["kind"] not in CARD_KINDS:
        p.append(f"kind {c['kind']!r} not in {sorted(CARD_KINDS)}")
    if len(c["summary"]) > 320:
        p.append("summary over 320 chars (keep cards short)")
    for k in ("aliases", "steps", "tips", "menu", "tags", "related"):
        if k in c and not (isinstance(c[k], list) and all(isinstance(x, str) and x for x in c[k])):
            p.append(f"{k} must be a list of strings")
    if len(c.get("steps", [])) > 12:
        p.append("more than 12 steps")
    for k in ("controls", "settings"):
        if k in c and not (isinstance(c[k], list) and all(isinstance(x, list) and len(x) == 2 and
                                                          all(isinstance(y, str) for y in x) for x in c[k])):
            p.append(f"{k} must be [[name, text], ...]")
    if "shortcuts" in c:
        ok = isinstance(c["shortcuts"], list) and all(
            isinstance(s, list) and 2 <= len(s) <= 3 and isinstance(s[0], str) and isinstance(s[1], str)
            and (len(s) == 2 or isinstance(s[2], dict)) for s in c["shortcuts"])
        if not ok:
            p.append("shortcuts must be [[keys, action], or [keys, action, {verify, fl_versions}]]")
    if c["kind"] == "shortcuts" and not c.get("shortcuts"):
        p.append("shortcuts card without shortcuts")
    if c["kind"] == "plugin":
        if c.get("type") not in PLUGIN_TYPES:
            p.append(f"plugin type must be one of {sorted(PLUGIN_TYPES)}")
        if c.get("edition") not in EDITION_KEYS:
            p.append(f"plugin edition must be one of {EDITION_KEYS}")
        if not isinstance(c.get("plugin"), str):
            p.append("plugin card needs 'plugin' (FL's plugin name)")
    if c["kind"] == "workflow" and not c.get("steps"):
        p.append("workflow without steps")
    if c["kind"] == "quirk":
        if not isinstance(c.get("verified_on"), list):
            p.append("quirk needs verified_on [FL versions]")
        if not isinstance(c.get("do"), str):
            p.append("quirk needs 'do' (what to do about it)")
    if "fl_versions" in c:
        fv = c["fl_versions"]
        if not isinstance(fv, dict) or not set(fv) <= {"min", "max"} or not all(vtuple(x) for x in fv.values()):
            p.append("fl_versions must be {min?, max?} version strings")
    if "notes_by_edition" in c and not (isinstance(c["notes_by_edition"], dict) and
                                        set(c["notes_by_edition"]) <= set(EDITION_KEYS) and
                                        all(isinstance(v, str) for v in c["notes_by_edition"].values())):
        p.append("notes_by_edition must be {edition: text}")
    if "verify" in c and not isinstance(c["verify"], bool):
        p.append("verify must be true/false")
    for k in ("beginner", "pro", "why"):
        if k in c and not isinstance(c[k], str):
            p.append(f"{k} must be a string")
    return p


def _card_body(c):
    parts = list(c.get("steps", [])) + list(c.get("tips", [])) + list(c.get("menu", []))
    parts += [f"{a} {b}" for a, b in c.get("controls", [])] + [f"{a} {b}" for a, b in c.get("settings", [])]
    parts += [f"{s[0]} {s[1]}" for s in c.get("shortcuts", [])]
    parts += [c.get(k, "") for k in ("why", "beginner", "pro", "do", "plugin")]
    return " ".join(parts)


def load_cards(force=False):
    """All cards (cards/*.json + quirks.json) with their BM25 index; cached."""
    if _MEM["cards"] is not None and not force:
        return _MEM["cards"]
    cards, errors = [], {}
    files = []
    cdir = os.path.join(DATA, "cards")
    if os.path.isdir(cdir):
        files = [os.path.join(cdir, f) for f in sorted(os.listdir(cdir)) if f.endswith(".json")]
    files.append(os.path.join(DATA, "quirks.json"))
    for fp in files:
        try:
            with open(fp, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError) as e:
            errors[os.path.basename(fp)] = str(e)
            continue
        for c in d.get("quirks", []):
            c.setdefault("kind", "quirk")
        for c in d.get("cards", []) + d.get("quirks", []):
            c["_file"] = os.path.basename(fp)
            cards.append(c)
    docs = [{"title": " ".join([c.get("title", "")] + c.get("aliases", [])),
             "tags": " ".join(c.get("tags", []) + [c.get("plugin", ""), c.get("kind", "")]),
             "summary": c.get("summary", ""), "body": _card_body(c)} for c in cards]
    idx = bm25_build(docs, CARD_FIELDS, tuple(CARD_FIELDS))
    _MEM["cards"] = {"cards": cards, "index": idx, "by_id": {c.get("id"): c for c in cards}, "errors": errors}
    return _MEM["cards"]


def _card_versions_ok(c, version):
    return in_versions(c.get("fl_versions"), version)


def card_hits(query, k=3, version=None):
    """Best-matching cards -> [(score, card)]; needs half the user's words to match (no noise on unrelated
    questions)."""
    C = load_cards()
    prim = primary(query)
    if not prim:
        return []
    vocab = C["index"]["terms"].keys()
    origin = {}
    qt = query_terms(query, vocab, origin)
    concepts = origin.pop("_concepts")
    sc = bm25_score(C["index"], qt, origin)
    need = max(1, math.ceil(len(concepts) * 0.6))
    hits = sorted(((s, i) for i, (s, m) in sc.items() if _covered(m, concepts) >= need), reverse=True)
    if not hits:
        return []
    top = hits[0][0]
    out = []
    for s, i in hits:
        c = C["cards"][i]
        if s < 0.35 * top or not _card_versions_ok(c, version):
            continue
        out.append((round(s, 2), c))
        if len(out) >= k:
            break
    return out


def _snip(text, terms, n=SNIP):
    text = " ".join(str(text or "").split())
    if len(text) <= n:
        return text
    low = text.lower()
    best, pos = -1, 0
    starts = sorted({m.start() for t in terms if "_" not in t and len(t) > 2 for m in re.finditer(re.escape(t), low)})
    for s in starts[:200]:
        dens = sum(1 for t in terms if "_" not in t and t in low[max(0, s - 20):s + n])
        if dens > best:
            best, pos = dens, s
    a = max(0, pos - 40)
    while a > 0 and text[a - 1] not in " .;:":
        a -= 1
    return ("..." if a else "") + text[a:a + n].rstrip() + "..."


def _card_snippet(c, terms):
    body = c.get("summary", "")
    for k in ("steps", "menu"):
        if c.get(k):
            body += " | " + "; ".join(c[k][:3])
            break
    if c.get("shortcuts"):
        body += " | " + "; ".join(f"{s[0]}: {s[1]}" for s in c["shortcuts"][:4])
    body = " ".join(body.split())
    return body if len(body) <= 260 else body[:257].rstrip() + "..."


def card_result(score, c, terms):
    r = {"src": "fl-headless note", "card": c["id"], "title": c["title"], "snippet": _card_snippet(c, terms)}
    if c.get("verify"):
        r["verify"] = True
    return r


def find_card(topic):
    C = load_cards()
    t = str(topic or "").strip().lower()
    for c in C["cards"]:
        if t == c["id"] or t == c["title"].lower() or t in [a.lower() for a in c.get("aliases", [])] or \
                t == str(c.get("plugin", "")).lower():
            return c
    hits = card_hits(topic, 1)
    return hits[0][1] if hits else None


def _public(c, full=True):
    out = {k: v for k, v in c.items() if not k.startswith("_") and v not in (None, "", [], {})}
    if not full:
        for k in ("pro", "beginner", "settings", "controls"):
            out.pop(k, None)
    out["src"] = "fl-headless note" if c.get("kind") != "quirk" else "fl-headless quirk (FL behaviour seen by fl-headless)"
    return out


def handbook_card(topic, version=None, full=True):
    """One knowledge card by id/title/alias/plugin name or best match; suggestions when nothing fits."""
    c = find_card(topic)
    if not c:
        C = load_cards()
        sug = sorted(((s[0], i) for i, s in bm25_score(C["index"], query_terms(topic)).items()), reverse=True)[:5]
        return {"found": False, "topic": topic, "suggestions": [{"card": C["cards"][i]["id"], "title":
                                                                  C["cards"][i]["title"]} for _, i in sug],
                "try": "flh_handbook_search for the official manual"}
    out = _public(c, full)
    if version and not _card_versions_ok(c, version):
        out["version_note"] = f"this card is for FL {c['fl_versions']}, not {version}"
    if version and c.get("shortcuts"):
        out["shortcuts"] = [s for s in c["shortcuts"] if in_versions((s[2] if len(s) > 2 else {}).get("fl_versions"),
                                                                     version)]
    return out


def quirks_text():
    """Compact lines of the quirks file for the server INSTRUCTIONS (one per quirk)."""
    C = load_cards()
    return "\n".join(f"- {c['summary']} {c['do']}".strip() for c in C["cards"] if c.get("kind") == "quirk"
                     and c.get("instructions", True))


# ============================================================================= storage
def _atomic_json(path, data, gz=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    raw = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    with open(tmp, "wb") as f:
        f.write(gzip.compress(raw, 6) if gz else raw)
    os.replace(tmp, path)


def _read_json(path, gz=False):
    try:
        with open(path, "rb") as f:
            raw = f.read()
        return json.loads(gzip.decompress(raw) if gz else raw)
    except (OSError, ValueError, EOFError):
        return None


def _dir_size(d):
    tot = 0
    for root, _, files in os.walk(d):
        for f in files:
            try:
                tot += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return tot


def indexes():
    """Built indexes: [{key, dir, manual_version, built, pages, docs, complete, source}] newest manual first."""
    out = []
    if not os.path.isdir(HOME):
        return out
    for key in sorted(os.listdir(HOME)):
        d = os.path.join(HOME, key)
        man = _read_json(os.path.join(d, "manifest.json"))
        if not man or not os.path.isfile(os.path.join(d, "index.json.gz")):
            continue
        out.append({"key": key, "dir": d, "manual_version": man.get("manual_version"), "built": man.get("indexed"),
                    "pages": man.get("indexed_pages"), "docs": man.get("docs"), "complete": man.get("complete"),
                    "source": man.get("source"), "base": man.get("base")})
    out.sort(key=lambda x: vtuple(x["manual_version"]) or (0,), reverse=True)
    return out


def index_ready():
    return bool(indexes())


def pick_index(version=None):
    """-> (index info, note). version: the user's FL version (default: installed) - same major preferred."""
    all_ = indexes()
    if not all_:
        return None, None
    want = vmajor(version or installed_version())
    if want:
        same = [x for x in all_ if vmajor(x["manual_version"]) == want]
        if same:
            return same[0], None
        return all_[0], f"no manual index for FL {want}; using the FL {all_[0]['manual_version']} manual"
    return all_[0], None


def _load_index(info):
    d = info["dir"]
    mt = os.path.getmtime(os.path.join(d, "index.json.gz"))
    hit = _MEM["index"].get(d)
    if hit and hit[0] == mt:
        return hit[1]
    data = _read_json(os.path.join(d, "index.json.gz"), gz=True)
    if data:
        data["vocab"] = set(data["terms"])
        _MEM["index"][d] = (mt, data)
    return data


def _page_text(d, file):
    key = os.path.join(d, "pages", file)
    if key in _MEM["pages"]:
        return _MEM["pages"][key]
    rec = _read_json(key) or {}
    if len(_MEM["pages"]) > 64:
        _MEM["pages"].clear()
    _MEM["pages"][key] = rec
    return rec


def stored_page(url):
    """A manual page from the local store (any built index) -> {title, text, anchors} or None."""
    u = str(url).split("#")[0]
    for info in indexes():
        ix = _load_index(info)
        for p in (ix or {}).get("pages", []):
            if p[0] == u:
                rec = _page_text(info["dir"], p[3])
                if rec.get("text"):
                    return {"title": rec.get("title") or p[1], "text": rec["text"], "anchors": rec.get("anchors", {})}
    return None


# ============================================================================= manual search
def search_manual(query, k=5, version=None, per_page=2):
    """BM25F over the local index -> ([{src, title, section, url, score, snippet, page_url}], info, note)."""
    info, note = pick_index(version)
    if not info:
        return [], None, None
    ix = _load_index(info)
    if not ix:
        return [], info, f"index unreadable; rebuild with {_call('flh_handbook_build', {'action': 'index'})}"
    qt = query_terms(query, ix["vocab"])
    prim = primary(query)
    sc = bm25_score(ix, qt)
    qbig = set(_bigrams(tokens(_fix_spelling(query))))
    ranked = sorted(sc.items(), key=lambda kv: -kv[1][0])
    out, per = [], {}
    for di, (s, m) in ranked:
        page_i, heading, a, b, anchor = ix["docs"][di]
        if per.get(page_i, 0) >= per_page:
            continue
        p = ix["pages"][page_i]
        title_toks = tokens(p[1])
        if prim and set(title_toks) == prim:
            s += 3.0                                             # the page *about* exactly this
        if qbig and qbig & set(_bigrams(title_toks)):
            s += 1.0
        rec = _page_text(info["dir"], p[3])
        text = (rec.get("text") or "")[a:b]
        body = _body(text)
        url = p[0] + (f"#{anchor}" if anchor else "")
        out.append({"src": "manual", "title": p[1], "section": heading if heading != p[1] else None, "url": url,
                    "score": round(s, 2), "snippet": _snip(body, list(qt), SNIP), "_doc": di})
        per[page_i] = per.get(page_i, 0) + 1
        if len(out) >= k * 3:
            break
    out.sort(key=lambda r: -r["score"])
    out = out[:k]
    for r in out:
        if not r["section"]:
            r.pop("section")
    return out, info, note


def section_text(hit, info, max_chars=3000):
    ix = _load_index(info)
    page_i, heading, a, b, anchor = ix["docs"][hit["_doc"]]
    p = ix["pages"][page_i]
    rec = _page_text(info["dir"], p[3])
    text = rec.get("text") or ""
    # the section plus what follows on the page, up to max_chars (sections are often short)
    t = text[a:a + max_chars]
    out = {"title": p[1], "url": hit["url"], "text": t}
    if len(text) - a > max_chars:
        out["truncated"] = True
    return out


def handbook_search(query, k=5, version=None, online_fallback=True):
    """Cards first (fl-headless notes), then the official manual (local index, else the online TOC search)."""
    t0 = time.time()
    k = max(1, min(int(k or 5), 12))
    terms = query_terms(query)
    ncard = 2 if k < 6 else 3
    cards = [card_result(s, c, terms) for s, c in card_hits(query, min(ncard, k), version)]
    manual, info, note = search_manual(query, k, version)
    res = {"query": query, "results": cards}
    if info:
        for r in manual[:max(0, k - len(cards))]:
            r.pop("_doc", None)
            res["results"].append(r)
        res["manual"] = {"version": info["manual_version"], "built": info["built"], "pages": info["pages"]}
        if not info.get("complete"):
            res["manual"]["partial"] = True
        if note:
            res["note"] = note
    else:
        res["manual"] = f"no local index yet: {_call('flh_handbook_build')} builds it on this PC (background job)"
        if online_fallback:
            try:
                for r in _help().search(query, max(0, k - len(cards))):
                    if "error" in r:
                        res["manual_error"] = r["error"]
                        break
                    res["results"].append({"src": "manual", "title": r["title"], "url": r["url"],
                                           "snippet": _snip(r.get("snippet", ""), list(terms), SNIP)})
            except Exception as e:  # noqa: BLE001
                res["manual_error"] = str(e)
    res["labels"] = "fl-headless note = our own notes (verify=true: unconfirmed); manual = Image-Line's FL Studio manual"
    res["took_s"] = round(time.time() - t0, 3)
    return res


# ============================================================================= explain (teaching mode)
def handbook_explain(topic, level="beginner", version=None):
    """Short 'explain like a producer' text from a card (+ manual links when an index exists) ->
    {topic, level, text, links, src, card?, verify?}; a manual excerpt when there's no card; None when nothing
    is known (so teaching mode falls back to its own notes)."""
    level = "pro" if str(level).lower().startswith("pro") else "beginner"
    c = find_card(topic)
    links, man = [], []
    try:
        man, info, _ = search_manual(topic, 2, version)
        links = [{"title": m["title"] + (f" - {m['section']}" if m.get("section") else ""), "url": m["url"]}
                 for m in man]
    except Exception:  # noqa: BLE001
        pass
    if not c:
        if not links:
            return None                                   # nothing known: the caller uses its own fallback
        m0 = man[0]
        return {"topic": topic, "level": level, "text": m0["snippet"], "links": links, "src": "manual",
                "note": "no fl-headless note on this; excerpt from Image-Line's manual"}
    lines = []
    if level == "beginner":
        lines.append(c.get("beginner") or c["summary"])
        if c.get("why"):
            lines.append("Why: " + c["why"])
        if c.get("steps"):
            lines.append("How: " + " ".join(f"{i}) {s}" for i, s in enumerate(c["steps"][:5], 1)))
        elif c.get("controls"):
            lines.append("Main controls: " + "; ".join(f"{a} - {b}" for a, b in c["controls"][:3]))
        if c.get("tips"):
            lines.append("Tip: " + c["tips"][0])
    else:
        lines.append(c.get("pro") or c["summary"])
        if c.get("menu"):
            lines.append("Menu: " + "; ".join(c["menu"][:3]))
        sc = [s for s in c.get("shortcuts", []) if in_versions((s[2] if len(s) > 2 else {}).get("fl_versions"),
                                                               version)]
        if sc:
            lines.append("Keys: " + "; ".join(f"{s[0]} {s[1]}" for s in sc[:6]))
        if c.get("settings"):
            lines.append("Settings: " + "; ".join(f"{a}: {b}" for a, b in c["settings"][:4]))
        if c.get("steps") and not c.get("pro"):
            lines.append("Steps: " + " / ".join(c["steps"][:6]))
        if len(c.get("tips", [])) > 0:
            lines.append("Pro tip: " + c["tips"][-1])
    if c.get("kind") == "plugin":
        lines.append(f"Edition: {c['edition'].replace('_', ' ')}" + (" (unconfirmed)" if c.get("verify") else ""))
    out = {"topic": topic, "level": level, "card": c["id"], "title": c["title"], "text": "\n".join(lines),
           "links": links, "src": "fl-headless note" + (" + manual links" if links else "")}
    if c.get("verify"):
        out["verify"] = True
    return out


# ============================================================================= fetching (user's PC only)
def _default_http(url, headers, timeout=20):
    """GET -> (status, {lower header: value}, body bytes). 304 / 4xx / 5xx return their status (no raise).
    https image-line.com only; redirects must stay there; body capped (also after gzip); total deadline."""
    import fl_help
    st, hd, body, _ = fl_help.safe_get(url, dict({"User-Agent": UA}, **headers), timeout=timeout)
    return st, hd, body


def _decode(body, headers):
    m = re.search(r"charset=([\w-]+)", headers.get("content-type", ""), re.I)
    for enc in [m.group(1) if m else "utf-8", "utf-8", "cp1252"]:
        try:
            return body.decode(enc)
        except (UnicodeDecodeError, LookupError):
            pass
    return body.decode("utf-8", "replace")


class Fetcher:
    """Polite sequential GETs to image-line.com: robots.txt, crawl delay, conditional requests, backoff."""

    def __init__(self, http=None, delay=1.0, sleep=None, clock=None):
        self.http, self.delay = http or _default_http, float(delay)
        self.sleep, self.clock = sleep or time.sleep, clock or time.time
        self.last, self.robots, self.requests = 0.0, None, 0

    def _wait(self):
        gap = self.delay - (self.clock() - self.last)
        if self.last and gap > 0:
            self.sleep(gap)
        self.last = self.clock()

    def _raw(self, url, headers):
        u = urllib.parse.urlsplit(url); host = (u.hostname or "").lower()
        if u.scheme != "https" or not (host == ALLOWED_HOST or host.endswith("." + ALLOWED_HOST)):
            raise ValueError(f"fl-headless only fetches https pages from {ALLOWED_HOST}: {url[:120]}")
        last = None
        for attempt in range(3):
            self._wait()
            self.requests += 1
            try:
                st, hd, body = self.http(url, headers)
            except Exception as e:  # noqa: BLE001 - network error
                last = e
                self.sleep(2 + 3 * attempt)
                continue
            if st in (429, 500, 502, 503, 504):
                ra = hd.get("retry-after", "")
                self.sleep(min(60.0, float(ra)) if ra.isdigit() else 2 + 3 * attempt)
                last = f"HTTP {st}"
                continue
            return st, hd, body
        raise RuntimeError(f"fetch failed for {url}: {last}")

    def load_robots(self):
        import urllib.robotparser                                       # lazy: pulls in http/ssl (~20 ms)
        rp = urllib.robotparser.RobotFileParser()
        try:
            st, hd, body = self._raw(ROBOTS_URL, {})
        except RuntimeError:
            raise RuntimeError("can't reach image-line.com (robots.txt)")
        if st == 200:
            rp.parse(_decode(body, hd).splitlines())
        elif st in (401, 403):
            rp.disallow_all = True
        else:
            rp.allow_all = True                                   # no robots.txt: allowed
        cd = rp.crawl_delay(UA) or rp.crawl_delay("*")
        if cd:
            self.delay = max(self.delay, float(cd))
        self.robots = rp
        return rp

    def allowed(self, url):
        if self.robots is None:
            self.load_robots()
        return self.robots.can_fetch(UA, url)

    def get(self, url, etag=None, last_modified=None):
        """-> (status, headers, text or None). 304 = unchanged (text None)."""
        if not self.allowed(url):
            return 999, {}, None                                  # robots.txt says no
        hdr = {}
        if etag:
            hdr["If-None-Match"] = etag
        if last_modified:
            hdr["If-Modified-Since"] = last_modified
        st, hd, body = self._raw(url, hdr)
        return st, hd, (_decode(body, hd) if st == 200 else None)


def sources():
    d = _read_json(os.path.join(DATA, "sources.json")) or {}
    d.setdefault("latest", _help().BASE)
    d.setdefault("by_major", {})
    return d


def _detect_manual_version(*texts):
    for t in texts:
        m = re.search(r"FL Studio\s+(20[2-9]\d(?:\.\d+)?|2[0-9](?:\.\d+)?)\b", t or "")
        if m:
            return m.group(1)
    return None


def _menus(text):
    """Menu paths written like 'OPTIONS > MIDI settings' or 'File > Export > Wave file...'."""
    pat = r"\b([A-Za-z][\w&'.()-]*(?: [\w&'.()-]+){0,3}(?: ?> ?[A-Za-z][\w&'.()/-]*(?: [\w&'.()/-]+){0,4}){1,5})"
    return [m.strip(" .") for m in re.findall(pat, text) if ">" in m][:40]


_CONTAINER_IDS = {"idcontent", "innerdiv", "content", "main-content", "topic", "article"}


def _sections(rec):
    """Split a page's text at '## ' headings -> [(heading, start, end, anchor)]; a lone title line merges into the
    first section, tiny unheaded bits merge backwards. anchor = the HTML id/name right at the section's heading."""
    text, title = rec.get("text") or "", rec.get("title") or ""
    cuts = [m.start() for m in re.finditer(r"^## ", text, re.M)]
    if not cuts or cuts[0] != 0:
        cuts = [0] + cuts
    spans = []                                                    # [heading, start, end, heading position]
    for i, a in enumerate(cuts):
        b = cuts[i + 1] if i + 1 < len(cuts) else len(text)
        m = re.match(r"## (.+)", text[a:b])
        head = m.group(1).strip() if m else title
        if spans and b - a < 120 and not m:
            spans[-1][2] = b
            continue
        spans.append([head, a, b, a])
    if len(spans) > 1 and spans[0][2] - spans[0][1] < 40:      # a lone title line before the first heading
        spans[1][1] = spans[0][1]
        spans.pop(0)
    anchors = sorted((off, name) for name, off in (rec.get("anchors") or {}).items()
                     if name.lower() not in _CONTAINER_IDS)
    out = []
    for head, a, b, hpos in spans:
        anc = next((name for off, name in anchors if hpos - 3 <= off <= hpos + 3), None)
        out.append((head, a, b, anc))
    return out


def _body(sec):
    return re.sub(r"^## .*$\n?", "", sec, flags=re.M).strip()


def build_index(d):
    """(Re)build index.json.gz in folder d from the stored pages."""
    t0 = time.time()
    man = _read_json(os.path.join(d, "manifest.json")) or {}
    pages, docs, meta = [], [], []
    for url, e in sorted((man.get("pages") or {}).items()):
        if not e.get("file"):
            continue
        rec = _read_json(os.path.join(d, "pages", e["file"]))
        if not rec or not rec.get("text"):
            continue
        pi = len(pages)
        pages.append([url, rec.get("title") or e.get("title") or url, e.get("crumbs", ""), e["file"]])
        title = " ".join([rec.get("title") or ""] + e.get("toc_titles", []))
        for head, a, b, anc in _sections(rec):
            sec = rec["text"][a:b]
            body = _body(sec)
            docs.append({"title": title, "heading": head, "crumbs": e.get("crumbs", ""),
                         "menu": " ; ".join(_menus(sec)), "body": body})
            meta.append([pi, head, a, b, anc])
    ix = bm25_build(docs)
    ix.update(docs=meta, pages=pages, version=man.get("manual_version"), built=time.strftime("%Y-%m-%d %H:%M"))
    _atomic_json(os.path.join(d, "index.json.gz"), ix, gz=True)
    man.update(indexed=ix["built"], indexed_pages=len(pages), docs=len(meta))
    _atomic_json(os.path.join(d, "manifest.json"), man)
    _MEM["index"].pop(d, None)
    return {"pages": len(pages), "docs": len(meta), "terms": len(ix["terms"]), "s": round(time.time() - t0, 2)}


def _progress(**kw):
    fn = (H or {}).get("_job_progress")
    if fn:
        try:
            fn(**kw)
        except Exception:  # noqa: BLE001
            pass


def local_help_dirs():
    """Folders with FL's local help pages (HTML), if the install has any.
    # PC-VERIFY: check which FL versions ship a Help folder (FL install dir / Shared / user data) and its layout."""
    cands = []
    if os.environ.get("FLH_FL_HELP"):
        cands.append(os.environ["FLH_FL_HELP"])
    try:
        exe = (H or {}).get("fl_exe")
        if exe:
            base = os.path.dirname(exe())
            cands += [os.path.join(base, "Help"), os.path.join(base, "System", "Help"), os.path.join(base, "Help", "html")]
    except Exception:  # noqa: BLE001 - FL not found / not Windows
        pass
    found = []
    for c in cands:
        if not os.path.isdir(c):
            continue
        n = 0
        for root, _, files in os.walk(c):
            n += sum(f.lower().endswith((".htm", ".html")) for f in files)
            if n >= 10:
                break
        if n >= 10:
            found.append(c)
    return found


def _page_file(url):
    return hashlib.sha1(url.encode()).hexdigest()[:16] + ".json"


def build(version=None, source="auto", max_pages=None, refresh=False, delay=None, http=None, sleep=None,
          clock=None):
    """Fetch (or read locally) the manual for the user's FL version and index it. Resumable; see module doc."""
    if not _LOCK.acquire(blocking=False):
        return {"error": f"a handbook build is already running ({_call('flh_handbook_build', {'action': 'status'})} "
                         "or action='stop')"}
    _STOP.clear()
    try:
        return _build(version, source, max_pages, refresh, delay, http, sleep, clock)
    finally:
        _LOCK.release()


def _build(version, source, max_pages, refresh, delay, http, sleep, clock):
    t0, steps = time.time(), {}
    inst = version or installed_version()
    major = vmajor(inst)
    if source in ("auto", "local"):
        dirs = local_help_dirs()
        if dirs:
            return _build_local(dirs[0], inst, t0)
        if source == "local":
            return {"error": "no local FL help pages found (FL's help is online for recent versions); "
                             "use source='online' or set FLH_FL_HELP to a folder of help .htm files"}
    src = sources()
    base = src["by_major"].get(major) if major else None
    key = f"online-{major}" if base else "online"
    base = base or src["latest"]
    d = os.path.join(HOME, key)
    os.makedirs(os.path.join(d, "pages"), exist_ok=True)
    man = _read_json(os.path.join(d, "manifest.json")) or {}
    man.update(base=base, source="online", key=key)
    man.setdefault("pages", {})
    fx = Fetcher(http, delay if delay is not None else float(src.get("delay_s", 1.0)), sleep, clock)
    H_ = _help()
    # PC-VERIFY: the real manual still has its TOC at <base>Index_Frame_Left.html, robots.txt allows it, the
    # server answers If-None-Match / If-Modified-Since with 304, and the TOC/title page names the FL version.
    toc_url = base + "Index_Frame_Left.html"
    _progress(step="robots + table of contents", pct=1)
    try:
        fx.load_robots()
        if not fx.allowed(toc_url):
            return {"error": "robots.txt disallows the manual's table of contents; not fetching", "url": toc_url}
        toc_meta = man.get("toc") or {}
        st, hd, html = fx.get(toc_url, None if refresh else toc_meta.get("etag"),
                              None if refresh else toc_meta.get("last_modified"))
    except RuntimeError as e:
        return {"error": str(e), "hint": "check the internet connection; a partial build resumes next time"}
    if st == 304 and toc_meta.get("entries"):
        entries = toc_meta["entries"]
    elif st == 200:
        p = H_._TocParser(toc_url)
        p.feed(html)
        entries = p.entries
        if not entries:
            return {"error": "manual table of contents parsed to 0 links (page layout changed?)", "url": toc_url}
        mv = _detect_manual_version(html)
        man["toc"] = {"etag": hd.get("etag"), "last_modified": hd.get("last-modified"), "entries": entries}
        if mv:
            man["manual_version"] = mv
    else:
        return {"error": f"table of contents: HTTP {st}", "url": toc_url}
    steps["toc"] = round(time.time() - t0, 2)
    # pages (without #fragments), with TOC titles + crumbs
    pages = {}
    for e in entries:
        u = e["url"].split("#")[0]
        if not H_._in_manual(u, urllib.parse.urlsplit(base).path):
            continue
        p = pages.setdefault(u, {"title": e["title"], "crumbs": e["section"], "toc_titles": []})
        if e["title"] != p["title"] and e["title"] not in p["toc_titles"]:
            p["toc_titles"].append(e["title"])
    order = list(pages)
    if max_pages:
        order = order[:int(max_pages)]
    stats = {"fetched": 0, "not_modified": 0, "cached": 0, "failed": 0, "robots_blocked": 0}
    fails, stopped, now = 0, False, (clock or time.time)
    run_started = now()
    for i, url in enumerate(order):
        if _STOP.is_set():
            stopped = True
            break
        ent = man["pages"].setdefault(url, {})
        ent.update(title=pages[url]["title"], crumbs=pages[url]["crumbs"], toc_titles=pages[url]["toc_titles"])
        fresh = ent.get("file") and now() - ent.get("checked", 0) < PAGE_TTL
        if fresh and not refresh:
            stats["cached"] += 1
            continue
        try:
            st, hd, html = fx.get(url, ent.get("etag") if ent.get("file") else None,
                                  ent.get("last_modified") if ent.get("file") else None)
        except RuntimeError as e:
            stats["failed"] += 1
            fails += 1
            ent["error"] = str(e)[:200]
            if fails >= 5:
                stopped = True
                man["stopped_reason"] = "5 failures in a row (network?) - run again to resume"
                break
            continue
        fails = 0
        if st == 304 and ent.get("file"):
            stats["not_modified"] += 1
            ent["checked"] = now()
        elif st == 200:
            rec = H_._extract(html)
            if not rec.get("title"):
                rec["title"] = pages[url]["title"]
            rec.update(url=url, version=man.get("manual_version"))
            ent["file"] = _page_file(url)
            _atomic_json(os.path.join(d, "pages", ent["file"]), rec)
            ent.update(etag=hd.get("etag"), last_modified=hd.get("last-modified"), checked=now())
            ent.pop("error", None)
            stats["fetched"] += 1
            if i < 3 and not man.get("manual_version"):
                mv = _detect_manual_version(rec.get("title", ""), rec.get("text", "")[:2000])
                if mv:
                    man["manual_version"] = mv
        elif st == 999:
            stats["robots_blocked"] += 1
        else:
            stats["failed"] += 1
            ent["error"] = f"HTTP {st}"
        if i % 10 == 0:
            _atomic_json(os.path.join(d, "manifest.json"), man)
            _progress(step="fetching manual pages", pct=round(100 * (i + 1) / max(1, len(order))), done=i + 1,
                      total=len(order), **stats)
    steps["pages"] = round(time.time() - t0 - steps["toc"], 2)
    have = sum(1 for u in pages if man["pages"].get(u, {}).get("file"))
    man["complete"] = not stopped and have >= len(pages) - stats["robots_blocked"] - stats["failed"] and \
        not max_pages
    man.setdefault("manual_version", vmajor(inst) or "latest")
    man["built_for_fl"] = inst
    _atomic_json(os.path.join(d, "manifest.json"), man)
    _progress(step="indexing", pct=99)
    ix = build_index(d)
    steps["index"] = ix["s"]
    out = {"index": key, "manual_version": man["manual_version"], "installed_fl": inst, "pages_in_toc": len(pages),
           "pages_stored": have, **stats, "requests": fx.requests, "docs": ix["docs"], "complete": man["complete"],
           "size_mb": round(_dir_size(d) / 1e6, 1), "folder": d, "steps_s": steps}
    if inst and vmajor(man["manual_version"]) not in (None, vmajor(inst)) and man["manual_version"] != "latest":
        out["note"] = (f"Image-Line's online manual is for FL {man['manual_version']}; you have {inst}. Menu names "
                       "can differ slightly in older versions.")
    if stopped:
        out["stopped"] = man.get("stopped_reason") or "stopped on request"
        out["resume"] = f"run {_call('flh_handbook_build')} again: it continues where it stopped"
    return out


def _build_local(folder, inst, t0):
    """Index FL's local help pages (no network)."""
    H_ = _help()
    major = vmajor(inst) or "local"
    key = f"local-{major}"
    d = os.path.join(HOME, key)
    os.makedirs(os.path.join(d, "pages"), exist_ok=True)
    man = {"base": folder, "source": "local", "key": key, "pages": {}, "manual_version": inst or "local"}
    files = []
    for root, _, fs in os.walk(folder):
        files += [os.path.join(root, f) for f in fs if f.lower().endswith((".htm", ".html"))]
    for i, fp in enumerate(sorted(files)):
        try:
            with open(fp, "rb") as f:
                html = _decode(f.read(), {})
        except OSError:
            continue
        rec = H_._extract(html)
        if len(rec.get("text", "")) < 40:
            continue
        rel = os.path.relpath(fp, folder).replace(os.sep, "/")
        url = H_.BASE + ("html/" + os.path.basename(rel) if "/" not in rel or rel.startswith("html/") else rel)
        rec.update(url=url, local=fp)
        file = _page_file(url)
        _atomic_json(os.path.join(d, "pages", file), rec)
        man["pages"][url] = {"file": file, "title": rec.get("title") or os.path.basename(fp), "crumbs": "",
                             "toc_titles": [], "checked": time.time()}
        if i % 50 == 0:
            _progress(step="reading local help", done=i + 1, total=len(files))
    man["complete"] = True
    _atomic_json(os.path.join(d, "manifest.json"), man)
    ix = build_index(d)
    return {"index": key, "source": "local", "folder_read": folder, "pages_stored": ix["pages"], "docs": ix["docs"],
            "size_mb": round(_dir_size(d) / 1e6, 1), "steps_s": {"all": round(time.time() - t0, 2)},
            "note": "links point at the matching online manual page (local file names); "
                    "# PC-VERIFY: local help file names match the online html/ names"}


def status():
    out = {"indexes": [{k: v for k, v in x.items() if k != "dir"} for x in indexes()], "folder": HOME,
           "building": _LOCK.locked(), "installed_fl": installed_version()}
    if os.path.isdir(HOME):
        out["size_mb"] = round(_dir_size(HOME) / 1e6, 1)
    for x in out["indexes"]:
        x.pop("base", None)
    cards = load_cards()
    out["cards"] = len(cards["cards"])
    if cards["errors"]:
        out["card_errors"] = cards["errors"]
    if not out["indexes"]:
        out["next"] = f"{_call('flh_handbook_build')} (about 20-30 min in the background for the full manual)"
    return out


# ============================================================================= tools
def flh_handbook_build(version=None, action="build", source="auto", max_pages=None, refresh=False, background=True):
    action = str(action or "build").lower()
    if action == "status":
        return status()
    if action == "stop":
        _STOP.set()
        return {"stopping": _LOCK.locked(), "note": "the build stops after the current page; run again to resume"}
    if action == "index":
        info = [x for x in indexes()] or [{"dir": os.path.join(HOME, k)} for k in
                                         (os.listdir(HOME) if os.path.isdir(HOME) else [])]
        return {"rebuilt": [dict(build_index(x["dir"]), key=os.path.basename(x["dir"])) for x in info
                            if os.path.isfile(os.path.join(x["dir"], "manifest.json"))]}
    if action != "build":
        raise ValueError("action: build | status | stop | index")
    kw = dict(version=version, source=source, max_pages=max_pages, refresh=bool(refresh))
    if background and H and H.get("_start_job"):
        r = H["_start_job"]("handbook", build, **kw)
        n = int(max_pages) if max_pages else 1500
        r["estimate_min"] = round(n * float(sources().get("delay_s", 1.0)) / 60 + 1) if not indexes() else \
            "a few minutes (unchanged pages answer 304)"
        r["note"] = "fetches Image-Line's manual for your FL version into fl-headless's state folder (never " \
                    "shipped), one page at a time; resumable; fl_job shows progress"
        return r
    return build(**kw)


def flh_handbook_search(query, k=5, version=None):
    return handbook_search(str(query), int(k or 5), version)


def flh_handbook_card(topic, version=None, full=True):
    return handbook_card(str(topic), version, full if full is not None else True)


def _call(name, args=None):
    """A hint naming a tool: direct when the client lists it, else flh_call("name", {...})."""
    import flh_common
    return flh_common.tool_call(H, name, args)


def register(ns):
    global H, HOME
    H = ns
    if ns.get("STATE"):
        HOME = os.path.join(os.path.dirname(ns["STATE"]), "handbook")
    S, P, STR, INT, BOOL = ns["_schema"], ns["_p"], ns["_STR"], ns["_INT"], ns["_BOOL"]
    ns["TOOLS"]["flh_handbook_search"] = (
        flh_handbook_search, "Search what fl-headless knows about FL Studio: our own notes (shortcuts, menu paths, "
        "every stock plugin, workflows like sidechain/bussing/stems, FL quirks) ranked first, then Image-Line's "
        "official manual (local index; build it once with flh_handbook_build). Compact snippets with links; "
        "'src' says which is which. Use flh_handbook_card / fl_manual_page for the full text.",
        S(query=STR("e.g. 'sidechain kick to bass', 'render stems', 'piano roll shortcuts'"),
          k=INT("results (default 5, max 12)", True),
          version=STR("FL version to answer for (default: the installed one)", True)))
    ns["TOOLS"]["flh_handbook_card"] = (
        flh_handbook_card, "One fl-headless knowledge card: a plugin ('Fruity Limiter'), workflow ('sidechain'), "
        "shortcut set ('piano roll shortcuts'), menu path ('export stems') or quirk. Our own notes, not the manual; "
        "verify=true marks unconfirmed facts.",
        S(topic=STR("card id, title, plugin name or a few words"),
          version=STR("FL version (filters version-specific shortcuts)", True),
          full=BOOL("include controls/settings/pro notes (default true)", True)))
    ns["TOOLS"]["flh_handbook_build"] = (
        flh_handbook_build, "Build the local FL Studio manual index on this PC (background job, resumable): reads "
        "FL's local help if the install has it, else fetches Image-Line's online manual politely (robots.txt, "
        "1 page/s, ETag caching) into fl-headless's state folder. Then fl_help and flh_handbook_search search full "
        "page text offline. action=status|stop|index (re-index stored pages).",
        S(version=STR("FL version (default: installed)", True), action=STR("build | status | stop | index", True),
          source=STR("auto | online | local", True), max_pages=INT("limit pages (testing)", True),
          refresh=BOOL("re-check every page (conditional GET) even if fresh", True),
          background=BOOL("run as a job (default true)", True)))
    ns["handbook_search"] = handbook_search
    ns["handbook_card"] = handbook_card
    ns["handbook_explain"] = handbook_explain
    ns["handbook_quirks_text"] = quirks_text
    ns["handbook_build"] = build
