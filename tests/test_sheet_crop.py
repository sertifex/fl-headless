"""sheet_pages frame tools on synthetic pages drawn with numpy: systems=true (boxes, staff lines, bar lines in a
1000-px page frame, no image), rows=S + bars='a-b' crops between detected bar lines (with the next call), crop=[...]
in frame coordinates (upscaled, inside the pixel budget, the right pixels), save=<.png> (inside FLH_ROOTS only),
frame=, and split=N (page ranges balanced by bars + a filled reader brief each).
Run: python3 tests/test_sheet_crop.py   (prints 'ok')"""
import base64, json, os, shutil, sys, tempfile
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
tmp = tempfile.mkdtemp(); os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = tmp
import atexit; atexit.register(shutil.rmtree, tmp, True)  # noqa: E702
import fl_headless_mcp as m  # noqa: E402
import flh_sheet as SH  # noqa: E402
ns = vars(m)
SH.register(ns)


def call(name, **args):
    r = m.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})
    raw = next(c["text"] for c in r["content"] if c["type"] == "text")
    imgs = [c for c in r["content"] if c["type"] == "image"]
    return ({"_error": raw} if r.get("isError") else json.loads(raw)), imgs


def page(W=2000, H=2600, systems=((2, 4), (2, 5), (1, 3)), sp=16, seed=0):
    """-> (gray page, truth [{tops, bar_x, x0, x1}]): staff lines, a system's opening line, bar lines through the
    system, filled heads with stems, and one solid black marker square per system (to check crop pixels)."""
    rng = np.random.default_rng(seed)
    g = np.full((H, W), 250, np.float64)
    y = 220; x0, x1 = 120, W - 100
    truth = []
    for nst, nb in systems:
        tops = [y + k * 10 * sp for k in range(nst)]
        for top in tops:
            for ln in range(5):
                g[top + ln * sp: top + ln * sp + 2, x0:x1] = 15
        bot = tops[-1] + 4 * sp
        g[tops[0]:bot + 2, x0:x0 + 3] = 15
        bx = [int(b) for b in np.linspace(x0 + 4 * sp, x1 - 1, nb + 1)[1:]]
        for b in bx:
            g[tops[0]:bot + 2, b - 2:b + 1] = 15
        edges = [x0 + 4 * sp] + bx
        for top in tops:
            for a, b in zip(edges[:-1], edges[1:]):
                for xn in np.linspace(a + sp * 1.5, b - sp * 1.5, 3):
                    xn = int(xn); pos = rng.integers(0, 8); yc = top + pos * sp // 2
                    yy, xx = np.ogrid[-sp:sp + 1, -sp:sp + 1]
                    msk = (xx / (0.65 * sp)) ** 2 + (yy / (0.45 * sp)) ** 2 <= 1
                    g[yc - sp:yc + sp + 1, xn - sp:xn + sp + 1][msk] = 10
                    g[yc:yc + int(3.5 * sp), xn - int(0.6 * sp):xn - int(0.6 * sp) + 2] = 10
        truth.append({"tops": tops, "bar_x": [b - 1 for b in bx], "x0": x0, "x1": x1, "bot": bot})
        y = bot + 9 * sp
    g = np.clip(g, 0, 255).astype(np.uint8)
    return g, truth


def write_png(path, g):
    rgb = np.repeat(g[:, :, None], 3, 2)
    with open(path, "wb") as f:
        f.write(m._png_encode(g.shape[1], g.shape[0], np.ascontiguousarray(rgb).tobytes()))


pdir = os.path.join(tmp, "score"); os.makedirs(pdir)
g1, t1 = page(seed=1)
g2, t2 = page(seed=2, systems=((2, 4), (2, 4), (2, 4), (2, 4)))
g3, t3 = page(seed=3, systems=((1, 2),))
for i, g in enumerate((g1, g2, g3)):
    write_png(os.path.join(pdir, f"page{i + 1}.png"), g)
P1 = os.path.join(pdir, "page1.png")

# ============================================================================= systems=true: text only, frame coords
r, imgs = call("sheet_pages", source=pdir, page=1, systems=True)
assert "_error" not in r and not imgs, r
assert r["frame"]["width"] == 1000 and r["frame"]["height"] == 1300, r["frame"]
k = 2000 / 1000
ss = r["systems"]
assert [s["staves"] for s in ss] == [2, 2, 1] and [s["bars_est"] for s in ss] == [4, 5, 3], ss
for s, t in zip(ss, t1):
    assert abs(s["staff_y"][0][0] - t["tops"][0] / k) <= 1.5, (s["staff_y"], t["tops"])
    assert len(s["bar_x"]) == len(t["bar_x"]), (s["bar_x"], t["bar_x"])
    assert all(abs(a - b / k) <= 2.5 for a, b in zip(s["bar_x"], t["bar_x"])), (s["bar_x"], t["bar_x"])
    bx = s["box"]
    assert bx[0] <= t["x0"] / k and bx[2] >= t["x1"] / k - 2 and bx[1] < t["tops"][0] / k < bx[3], (bx, t)
    assert abs(s["staff_space"] - 8) < 0.6, s["staff_space"]
assert ss[1]["first_bar_est"] == 5 and ss[2]["first_bar_est"] == 10, ss
assert "page_systems" not in r and "crop" in r["how"]
r, _ = call("sheet_pages", source=pdir, page=1, systems=True, frame=500)
assert r["frame"]["width"] == 500 and abs(r["systems"][0]["bar_x"][0] - ss[0]["bar_x"][0] / 2) <= 1, r["systems"][0]

# ============================================================================= rows=S + bars='a-b'
r, imgs = call("sheet_pages", source=pdir, page=1, rows=2, bars="2-3")
assert "_error" not in r and len(imgs) == 1, r
sh = r["showing"]
s2 = ss[1]
assert sh["system"] == 2 and sh["bars_in_system"] == "2-3" and sh["bar_numbers_est"] == "6-7", sh
assert s2["bar_x"][0] - 6 <= sh["crop"][0] <= s2["bar_x"][0] and s2["bar_x"][2] <= sh["crop"][2] <= s2["bar_x"][2] + 6, \
    (sh["crop"], s2["bar_x"])
assert sh["crop"][1] <= s2["staff_y"][0][0] and sh["crop"][3] >= s2["staff_y"][-1][1], sh
pix = SH.png_decode(base64.b64decode(imgs[0]["data"]))
assert pix.shape[1] * pix.shape[0] <= SH.IMAGE_BUDGET_PX + 2000 and pix.shape[1] <= 1400, pix.shape
assert sh["upscale"] > 1 and sh["px_per_staff_space"] >= 20, sh            # zoomed in: sharp heads
assert '"bars": "4-5"' in r["next"] and '"rows": 2' in r["next"], r["next"]
r, _ = call("sheet_pages", source=pdir, page=1, rows=2, bars=5)
assert '"rows": 3' in r["next"] and '"bars": "1-1"' in r["next"], r["next"]
r, _ = call("sheet_pages", source=pdir, page=1, rows=3, bars=[2, 3])
assert '"page": 2' in r["next"] and '"systems": true' in r["next"], r["next"]
r, _ = call("sheet_pages", source=pdir, page=1, rows=1, bars=1)
assert r["showing"]["crop"][0] < ss[0]["box"][0] + 25, r["showing"]          # bar 1 keeps the clef area
r, _ = call("sheet_pages", source=pdir, page=1, rows=2, bars="4-9")
assert "_error" in r and "has 5 bars" in r["_error"], r
r, _ = call("sheet_pages", source=pdir, page=1, rows=[1, 2], bars=1)
assert "_error" in r and "one system" in r["_error"], r

# ============================================================================= crop=[x0, y0, x1, y1] in the frame
# a black square drawn into page 1 at known page pixels shows up where the crop says it is
g1b = g1.copy(); g1b[2400:2440, 1600:1680] = 0
P1B = os.path.join(tmp, "marked.png"); write_png(P1B, g1b)
r, imgs = call("sheet_pages", source=P1B, crop=[780, 1180, 860, 1240], marks=False)
assert "_error" not in r and len(imgs) == 1, r
pix = SH.png_decode(base64.b64decode(imgs[0]["data"])).astype(float)
sc = r["showing"]["upscale"]
assert abs(sc - 4.0) < 1e-6, r["showing"]                                  # small crop: capped upscale
assert r["showing"]["crop"] == [780.0, 1180.0, 860.0, 1240.0]
# page px 1600..1680 x 2400..2440 -> frame 800..840 x 1200..1220 -> crop px (800-780)*2*4 = 160 ...
blk = pix[int(20 * 2 * sc) + 4:int(40 * 2 * sc) - 4, int(20 * 2 * sc) + 4:int(60 * 2 * sc) - 4]
out = pix[:int(20 * 2 * sc) - 6, :int(20 * 2 * sc) - 6]
assert blk.mean() < 30 and out.mean() > 200, (blk.mean(), out.mean())
r, imgs = call("sheet_pages", source=P1, crop=[0, 0, 1000, 1300])           # the whole page: inside the budget
pix = SH.png_decode(base64.b64decode(imgs[0]["data"]))
assert pix.shape[0] * pix.shape[1] <= SH.IMAGE_BUDGET_PX + 2000 and r["showing"]["upscale"] < 1, r["showing"]
r, _ = call("sheet_pages", source=P1, crop=[1200, 0, 1300, 50])
assert "_error" in r and "outside the page" in r["_error"], r
r, _ = call("sheet_pages", source=P1, crop=[1, 2, 3])
assert "_error" in r and "crop" in r["_error"], r
# save= writes the same PNG (inside FLH_ROOTS only)
sp = os.path.join(tmp, "crops", "s2.png")
r, imgs = call("sheet_pages", source=P1, rows=2, bars="1-2", save=sp)
assert r["saved"] == sp and open(sp, "rb").read() == base64.b64decode(imgs[0]["data"]), r
r, _ = call("sheet_pages", source=P1, rows=2, bars="1-2", save=os.path.join(tempfile.gettempdir(), "outside.png"))
assert "_error" in r, r
r, _ = call("sheet_pages", source=P1, rows=2, bars="1-2", save=os.path.join(tmp, "x.jpg"))
assert "_error" in r and ".png" in r["_error"], r
# the ordinary system view still works after the frame calls (cached layout)
r, imgs = call("sheet_pages", source=pdir, page=1, rows=1)
assert "showing" in r and r["showing"]["systems"][0]["system"] == 1 and len(imgs) == 1, r

# ============================================================================= split=N: page ranges + briefs
r, imgs = call("sheet_pages", source=pdir, split=2, parts=["mel", "piano_rh", "piano_lh"])
assert "_error" not in r and not imgs, r
rd = r["readers"]
assert len(rd) == 2 and [x["pages"] for x in rd] == ["1", "2-3"], [x["pages"] for x in rd]   # 12 vs 16+2 bars
assert rd[0]["bars_est"] == "~1-12" and rd[1]["bars_est"] == "~13-30", [x["bars_est"] for x in rd]
assert [p["bars_est"] for p in r["page_info"]] == [12, 16, 2]
for x in rd:
    assert "<<" not in x["brief"] and pdir in x["brief"] and x["output"] in x["brief"], x["brief"][:200]
    assert "piano_rh" in x["brief"] and f"pages {x['pages']}" in x["brief"]
assert rd[1]["output"].endswith("score_p2-3.json") and "sheet_merge" in r["then"][0], r
r, _ = call("sheet_pages", source=pdir, split=5, first_bar=0, save=os.path.join(tmp, "readers"))
assert len(r["readers"]) == 3 and r["readers"][0]["bars_est"].startswith("~0-") and "only 3" in r["note"], r
assert r["readers"][0]["output"].startswith(os.path.join(tmp, "readers")), r["readers"][0]
assert "one per staff" in r["readers"][0]["brief"]                         # no parts given: agree on names
# a user's own brief (state folder) replaces the shipped one
ud = os.path.join(os.path.dirname(m.STATE), "sheet"); os.makedirs(ud, exist_ok=True)
with open(os.path.join(ud, "reader_brief.md"), "w") as f:
    f.write("MY BRIEF <<pages>> -> <<output>>")
r, _ = call("sheet_pages", source=pdir, split=1)
assert r["readers"][0]["brief"].startswith("MY BRIEF 1-3 -> "), r["readers"][0]["brief"]
os.remove(os.path.join(ud, "reader_brief.md"))
assert os.path.isfile(os.path.join(os.path.dirname(HERE), "data", "sheet", "reader_brief.md"))
print("ok")
