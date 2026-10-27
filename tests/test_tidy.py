"""flh_tidy: lists and removes old backups / reports / work files in fl-headless's own folder (dry run by default)."""
import os, shutil, sys, tempfile, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
tmp = tempfile.mkdtemp(prefix="flh_tidy_")
os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = tmp
import fl_headless_mcp as m  # noqa: E402
import flh_tidy  # noqa: E402
ns = vars(m); flh_tidy.register(ns); ns["IS_WINDOWS"] = False
try:
    base = os.path.dirname(m.STATE); old = time.time() - 30 * 86400
    proj = os.path.join(tmp, "song.flp"); open(proj, "wb").write(b"x")
    for h, pj, n in (("aaa", proj, 25), ("bbb", os.path.join(tmp, "gone.flp"), 3)):
        d = os.path.join(base, "backups", h); os.makedirs(d)
        open(os.path.join(d, "project.txt"), "w").write(pj)
        for i in range(n):
            f = os.path.join(d, f"2026{i:04d}-x.flp"); open(f, "wb").write(b"0" * 1000)
            os.utime(f, (old - i, old - i))
    for i in range(5):
        r = os.path.join(base, "pc_check", f"r{i}"); os.makedirs(r); open(os.path.join(r, "report.json"), "w").write("{}")
        os.utime(r, (old + i, old + i))
    w = os.path.join(base, "work", "tmp1"); os.makedirs(w); os.utime(w, (old, old))
    r = flh_tidy.flh_tidy()
    assert r["by_kind"]["backup"]["items"] == 5 + 3 and r["by_kind"]["pc_check"]["items"] == 2, r
    assert r["by_kind"]["work"]["items"] == 1 and "apply=true" in r["next"], r
    assert len(os.listdir(os.path.join(base, "backups", "aaa"))) == 26                     # dry run: nothing gone
    r = flh_tidy.flh_tidy(apply=True)
    assert r["removed"] == 0 and r["not_removed"], r                                       # no bin, not permanent
    r = flh_tidy.flh_tidy(apply=True, permanent=True)
    assert r["removed"] == 11, r
    assert len([f for f in os.listdir(os.path.join(base, "backups", "aaa")) if f.endswith(".flp")]) == 20
    assert len(os.listdir(os.path.join(base, "pc_check"))) == 3 and not os.path.exists(w)
    # songs in the allowed folders: leftover previews always; FL's autosave copies only when asked, newest 3 kept
    song = os.path.join(tmp, "My Projects", "Tune"); os.makedirs(os.path.join(song, "Backup"))
    os.makedirs(os.path.join(song, "_preview")); open(os.path.join(song, "Tune.flp"), "wb").write(b"x")
    pv = os.path.join(song, "_preview", "Tune.wav"); open(pv, "wb").write(b"0" * 10); os.utime(pv, (old, old))
    for i in range(6):
        f = os.path.join(song, "Backup", f"Tune (overwritten at {i}h).flp"); open(f, "wb").write(b"0" * 10)
        os.utime(f, (old - i, old - i))
    keepme = os.path.join(song, "Backup", "Tune notes.flp"); open(keepme, "wb").write(b"0")   # not an FL copy
    os.utime(keepme, (old - 99, old - 99))
    r = flh_tidy.flh_tidy()
    assert r["by_kind"].get("previews", {}).get("items") == 1 and "fl_backups" not in r["by_kind"], r
    r = flh_tidy.flh_tidy(fl_backups=True)
    assert r["by_kind"]["fl_backups"]["items"] == 3, r
    r = flh_tidy.flh_tidy(apply=True, permanent=True, fl_backups=True)
    left = sorted(os.listdir(os.path.join(song, "Backup")))
    assert len(left) == 4 and "Tune notes.flp" in left and not os.path.exists(pv), left
    assert os.path.isfile(os.path.join(song, "Tune.flp"))
    # Windows: everything goes to the Recycle Bin in one shell call per batch (not one per file)
    for i in range(30):
        f = os.path.join(base, "backups", "aaa", f"2025{i:04d}-y.flp"); open(f, "wb").write(b"0")
        os.utime(f, (old - 100 - i, old - 100 - i))
    calls = []

    def shell(paths):
        many = [paths] if isinstance(paths, str) else list(paths)
        calls.append(len(many))
        for x in many:
            os.remove(x) if os.path.isfile(x) else shutil.rmtree(x)
        return 0, False
    ns["IS_WINDOWS"] = True; ns["_recycle_shell"] = shell
    ns["_has_recycle_bin"] = lambda p: (True, "")                 # a fixed disk (no bin: tests/test_bughunt_setup.py)
    r = flh_tidy.flh_tidy(apply=True)
    assert r["removed"] == 30 and calls == [30], (r, calls)
finally:
    shutil.rmtree(tmp, True)
print("tidy ok")
