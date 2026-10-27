"""Shipped data agrees with itself: every genre name the data refers to (data/ear_candy.json, data/templates,
data/flip) is a real genre (data/genres/<name>.json), and every ear-candy / transition kind the genre data and
data/ear_candy.json list is one flh_fx can make (FX_KINDS) or one of the non-audio kinds flh_compose writes itself
(drum transitions, snare build, gap, delay throw). A typo or a made-up name ('edm') silently never matches.
Run: python3 tests/test_data_consistency.py"""
import glob, json, os, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE); sys.path.insert(0, os.path.join(REPO, "src"))
tmp = tempfile.mkdtemp(prefix="flh_datacons_")
os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = os.path.join(tmp, "local")
import fl_headless_mcp as m  # noqa: E402,F401
import flh_compose as CO  # noqa: E402
import flh_fx as FX  # noqa: E402

DATA = os.path.join(REPO, "src", "data")


def load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


GENRES = {os.path.splitext(os.path.basename(p))[0] for p in glob.glob(os.path.join(DATA, "genres", "*.json"))}
assert len(GENRES) >= 10, GENRES
bad = []


def need_genre(name, where):
    if name not in GENRES:
        bad.append(f"{where}: genre '{name}' has no data/genres/{name}.json")


# ---- genre names
ec = load(os.path.join(DATA, "ear_candy.json"))
for kind, k in (ec.get("kinds") or {}).items():
    for g in k.get("genres") or []:
        need_genre(g, f"ear_candy.json kinds.{kind}.genres")
for pn, p in (ec.get("profiles") or {}).items():
    for g in ((p.get("when") or {}).get("genres") or []):
        need_genre(g, f"ear_candy.json profiles.{pn}.when.genres")

for p in sorted(glob.glob(os.path.join(DATA, "templates", "*.json"))):
    name = os.path.splitext(os.path.basename(p))[0]
    t = load(p)
    if name.startswith("_"):                                      # shared defaults / icons: genre lists inside
        def walk(o, path):
            if isinstance(o, dict):
                for kk, v in o.items():
                    if kk == "genres" and isinstance(v, list):
                        for g in v:
                            need_genre(g, f"templates/{name}.json {path}.genres")
                    elif kk == "genre" and isinstance(v, str):
                        need_genre(v, f"templates/{name}.json {path}.genre")
                    else:
                        walk(v, f"{path}.{kk}")
            elif isinstance(o, list):
                for v in o:
                    walk(v, path)
        walk(t, "")
        continue
    need_genre(name, f"templates/{name}.json (file name)")
    if t.get("genre") is not None:
        need_genre(t["genre"], f"templates/{name}.json genre")
        if t["genre"] != name:
            bad.append(f"templates/{name}.json says genre '{t['genre']}'")

flip = load(os.path.join(DATA, "flip", "styles.json"))
styles = set(flip.get("styles") or {})
for g, st in (flip.get("genre_styles") or {}).items():
    need_genre(g, "flip/styles.json genre_styles")
    if st not in styles:
        bad.append(f"flip/styles.json genre_styles.{g}: style '{st}' doesn't exist ({sorted(styles)})")

# ---- FX / transition kinds
NON_AUDIO = set(CO.DRUM_TRANS) | set(CO.EC_MOVES)
KINDS = set(FX.FX_KINDS) | NON_AUDIO
missing_fx = [k for k in CO.FX_AUDIO if k not in FX.FX_KINDS]
assert not missing_fx, f"flh_compose.FX_AUDIO kinds flh_fx can't make: {missing_fx}"


def need_kind(item, where):
    k, _o = CO._trans_item(item)
    if k not in KINDS:
        bad.append(f"{where}: kind '{k}' is neither in flh_fx.FX_KINDS nor a non-audio kind {sorted(NON_AUDIO)}")


for p in sorted(glob.glob(os.path.join(DATA, "genres", "*.json"))):
    name = os.path.splitext(os.path.basename(p))[0]
    g = load(p)
    tr = g.get("transitions") or {}
    for key, v in tr.items():
        if isinstance(v, dict):                                   # into / after: {section: [items]}
            for sec, items in v.items():
                for it in items or []:
                    need_kind(it, f"genres/{name}.json transitions.{key}.{sec}")
        elif isinstance(v, list):                                 # end: [items]
            for it in v:
                need_kind(it, f"genres/{name}.json transitions.{key}")
    for b in ((g.get("ear_candy") or {}).get("beds") or []):
        need_kind(b, f"genres/{name}.json ear_candy.beds")
for kind in (ec.get("kinds") or {}):
    need_kind(kind, "ear_candy.json kinds")
for pn, p in (ec.get("profiles") or {}).items():
    for _place, items in p.get("places") or []:
        for it in items:
            need_kind(it, f"ear_candy.json profiles.{pn}.places")
    if p.get("bed"):
        need_kind(p["bed"], f"ear_candy.json profiles.{pn}.bed")

assert not bad, "\n".join(bad)
print(f"{len(GENRES)} genres, {len(KINDS)} kinds: every reference resolves")
print("ok")
