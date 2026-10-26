"""flh_tidy: keep fl-headless's own folder (%LOCALAPPDATA%\\fl-headless) small.

Every file edit keeps a backup, pc_check keeps its reports and bug_report its bundles, so the folder grows. flh_tidy
lists what it would remove and how much space that frees (a dry run unless apply=true):
  backups      per project: the newest `keep` backups and every backup younger than `days` stay; a project that no longer
               exists keeps only backups younger than 2 days
  pc_check     the newest 3 reports stay
  bug_reports  the newest 5 stay
  work         temporary files older than a day
  previews     fl_preview WAVs left in a song's _preview folder (older than an hour)
  fl_backups   only with fl_backups=true: FL's own autosave / 'overwritten at' copies in the songs' Backup folders
               (inside the allowed folders), older than `days`; the newest 3 per song always stay
Removed items go to the Recycle Bin (restorable); a folder without one is skipped unless permanent=true.
"""
import os
import re
import shutil
import time

H = None


def _size(p):
    if os.path.isfile(p):
        return os.path.getsize(p)
    t = 0
    for root, _, files in os.walk(p):
        for f in files:
            try:
                t += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return t


def _mtime(p):
    try:
        return os.path.getmtime(p)
    except OSError:
        return 0


def _drive_online(path):
    """The drive / share root of `path` is reachable (False for an unplugged USB drive or an offline network share,
    where a missing project says nothing about the project being deleted)."""
    m = re.match(r"^([A-Za-z]:)[\\/]|^([\\/]{2}[^\\/]+[\\/]+[^\\/]+)", str(path))
    if not m:
        return True                                               # a POSIX path: its root is always there
    return os.path.isdir((m.group(1) + os.sep) if m.group(1) else m.group(2))


FL_BACKUP_KEEP = 3
SCAN_CAP = 30000


def _song_folders():
    """Folders (up to 3 levels into the allowed folders) that hold a Backup or _preview subfolder."""
    roots = H["_root_entries"](os.environ.get("FLH_ROOTS", "")) if H.get("_root_entries") else []
    out, seen = [], 0
    for r in roots:
        for dirpath, dirnames, _ in os.walk(r):
            depth = dirpath[len(r):].count(os.sep)
            seen += len(dirnames)
            if any(d.lower() in ("backup", "_preview") for d in dirnames):
                out.append(dirpath)
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and d.lower() not in
                           ("backup", "_preview", "stems", "samples")] if depth < 3 else []
            if seen > SCAN_CAP:
                break
    return out


def _plan(keep=20, days=14, fl_backups=False):
    base = os.path.dirname(H["STATE"])
    now = time.time(); out = []
    bdir = os.path.join(base, "backups")
    if os.path.isdir(bdir):
        for h in sorted(os.listdir(bdir)):
            hp = os.path.join(bdir, h)
            if not os.path.isdir(hp):
                continue
            proj = None
            try:
                with open(os.path.join(hp, "project.txt"), encoding="utf-8") as f:
                    proj = f.read().strip()
            except OSError:
                pass
            alive = bool(proj and os.path.isfile(proj))
            if proj and not alive and not _drive_online(proj):   # an unplugged drive / offline share: not deleted
                alive = True
            files = sorted((f for f in os.listdir(hp) if f.lower().endswith(".flp")),
                           key=lambda f: _mtime(os.path.join(hp, f)), reverse=True)
            for i, f in enumerate(files):
                p = os.path.join(hp, f); age = (now - _mtime(p)) / 86400
                if alive and (i < keep or age < days):
                    continue
                if not alive and age < 2:
                    continue
                out.append({"kind": "backup", "path": p, "project": proj, "project_exists": alive,
                            "age_days": round(age, 1)})
    for sub, k in (("pc_check", 3), ("bug_reports", 5)):
        d = os.path.join(base, sub)
        if os.path.isdir(d):
            items = sorted((os.path.join(d, x) for x in os.listdir(d)), key=_mtime, reverse=True)
            for p in items[k:]:
                out.append({"kind": sub, "path": p, "age_days": round((now - _mtime(p)) / 86400, 1)})
    for sf in _song_folders():
        pv = os.path.join(sf, "_preview")
        if os.path.isdir(pv):
            for x in os.listdir(pv):
                p = os.path.join(pv, x)
                if x.lower().endswith(".wav") and now - _mtime(p) > 3600:
                    out.append({"kind": "previews", "path": p, "age_days": round((now - _mtime(p)) / 86400, 1)})
        bd = os.path.join(sf, "Backup")
        if fl_backups and os.path.isdir(bd):
            copies = sorted((os.path.join(bd, x) for x in os.listdir(bd) if x.lower().endswith(".flp") and
                             H["_BACKUP_NAME"].search(x)), key=_mtime, reverse=True)   # FL's own names only
            for p in copies[FL_BACKUP_KEEP:]:
                age = (now - _mtime(p)) / 86400
                if age >= days:
                    out.append({"kind": "fl_backups", "path": p, "age_days": round(age, 1)})
    w = os.path.join(base, "work")
    if os.path.isdir(w):
        for x in os.listdir(w):
            p = os.path.join(w, x)
            if now - _mtime(p) > 86400:
                out.append({"kind": "work", "path": p, "age_days": round((now - _mtime(p)) / 86400, 1)})
    for it in out:
        it["bytes"] = _size(it["path"])
    return base, out


def flh_tidy(apply=False, keep=20, days=14, permanent=False, fl_backups=False):
    """Tidy fl-headless's own folder: old backups, pc_check reports, bug bundles, temp work files, leftover previews
    (and, with fl_backups=true, FL's own old autosave copies). Dry run by default."""
    keep, days = int(keep), float(days)
    if keep < 1 or days < 0:
        raise ValueError("keep: 1 or more backups per project; days: 0 or more")
    base, items = _plan(keep, days, bool(fl_backups))
    by = {}
    for it in items:
        b = by.setdefault(it["kind"], {"items": 0, "mb": 0.0})
        b["items"] += 1; b["mb"] = round(b["mb"] + it["bytes"] / 1e6, 1)
    res = {"folder": base, "would_free_mb" if not apply else "freed_mb": round(sum(i["bytes"] for i in items) / 1e6, 1),
           "by_kind": by}
    gone_proj = sorted({i["project"] for i in items if i["kind"] == "backup" and not i["project_exists"] and i["project"]})
    if gone_proj:
        res["backups_of_deleted_projects"] = gone_proj[:20]
    if not apply:
        res["next"] = "flh_tidy(apply=true) moves them to the Recycle Bin (restorable)"
        return res
    done, failed = 0, []
    if H.get("IS_WINDOWS") and not permanent and H.get("_has_recycle_bin"):   # no bin there (USB stick, share):
        keep = []                                                 # Windows would delete for good - skipped
        for it in items:
            try:
                ok, why = H["_has_recycle_bin"](it["path"])
            except Exception as e:                                # noqa: BLE001
                ok, why = False, f"{type(e).__name__}: {e}"
            if ok:
                keep.append(it)
            else:
                failed.append(f"{os.path.basename(it['path'])}: no Recycle Bin there ({str(why)[:60]}); "
                              "permanent=true deletes it")
        items = keep
    if H.get("IS_WINDOWS") and items:                             # in batches: one shell call per 200 items
        left = []
        for i in range(0, len(items), 200):
            chunk = [it["path"] for it in items[i:i + 200]]
            try:
                H["_recycle_shell"](chunk)
            except Exception:                                     # noqa: BLE001 - checked file by file below
                pass
            left += [it for it in items[i:i + 200] if os.path.exists(it["path"])]
        done = len(items) - len(left)
        items = left                                              # what's still there: one by one, with the reason
    for it in items:
        p = it["path"]
        try:
            if H.get("IS_WINDOWS"):
                code, aborted = H["_recycle_shell"](p)
                if os.path.exists(p) or code or aborted:
                    raise RuntimeError(f"shell code {code}")
            elif permanent:
                shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
            else:
                raise RuntimeError("no Recycle Bin here: pass permanent=true to delete")
            done += 1
        except Exception as e:                                         # noqa: BLE001 - reported
            if permanent and os.path.exists(p):
                try:
                    shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
                    done += 1
                    continue
                except OSError:
                    pass
            failed.append(f"{os.path.basename(p)}: {str(e)[:60]}")
    res["removed"] = done
    if failed:
        res["not_removed"] = failed[:10]
    return res


def register(ns):
    global H
    H = ns
    S = ns["_schema"]
    ns["TOOLS"]["flh_tidy"] = (
        flh_tidy, "Keep fl-headless's own folder small: old backups (keeps the newest `keep` per project and anything "
        "younger than `days`; deleted projects' backups go after 2 days), old pc_check reports and bug bundles, temp "
        "work files, leftover fl_preview WAVs; fl_backups=true adds FL's own old autosave copies in the songs' Backup "
        "folders (newest 3 per song stay). Dry run with sizes unless apply=true (to the Recycle Bin).",
        S(apply=ns["_BOOL"]("remove them (default: just list)", True),
          keep=ns["_INT"]("backups kept per project (default 20)", True),
          days=ns["_NUM"]("backups younger than this always stay (default 14)", True),
          permanent=ns["_BOOL"]("delete where there's no Recycle Bin (default false)", True),
          fl_backups=ns["_BOOL"]("also FL's own autosave copies older than `days` (newest 3 per song stay)", True)))
    ns.setdefault("TOOL_GROUPS", {})["flh_tidy"] = "files"
