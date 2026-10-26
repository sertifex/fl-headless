"""Copy the FL parameter names a PC verified (fl_plugin_maps_check / fl_pc_check mix.map_names) back into the
shipped plugin maps.

The check writes user maps to <state folder>/plugin_maps/<plugin>.json with `names_verified` on every control
whose live FL name + index it confirmed. This script compares them with data/plugin_maps/*.json and prints a
unified diff; --write applies it (only fl_name, index and names_verified change: curves, units, ranges and
`verified` stay as shipped).

Usage (in a repo checkout):
  python3 tools/plugin_maps_names.py                     diff against the user maps in the default state folder
  python3 tools/plugin_maps_names.py --from DIR          user maps from DIR (e.g. a copy sent from a PC)
  python3 tools/plugin_maps_names.py --write             write the corrected files into data/plugin_maps
"""
import argparse
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)


def default_state_maps():
    base = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
    return os.path.join(base, "fl-headless", "plugin_maps")


def run(user_dir, ship_dir, write=False, out=sys.stdout):
    """-> {shipped file: unified diff} for every shipped map a user map corrects (written when write=True)."""
    import flh_mixeng as X
    shipped = {}
    for f in sorted(glob.glob(os.path.join(ship_dir, "*.json"))):
        with open(f, encoding="utf-8") as fh:
            m = json.load(fh)
        names = [X._norm_name(m.get("plugin"))] + [X._norm_name(a) for a in m.get("aliases", [])]
        shipped[f] = (m, names)
    changed = {}
    for uf in sorted(glob.glob(os.path.join(user_dir, "*.json"))):
        try:
            with open(uf, encoding="utf-8") as fh:
                u = json.load(fh)
        except (OSError, ValueError):
            continue
        if not u.get("names_verified"):
            continue
        want = X._norm_name(u.get("plugin"))
        hit = next((f for f, (_, names) in shipped.items() if want in names), None)
        if not hit:
            print(f"# no shipped map for {u.get('plugin')} ({os.path.basename(uf)})", file=out)
            continue
        d = X.shipped_diff(hit, u)
        if not d:
            continue
        changed[hit] = d
        out.write(d if d.endswith("\n") else d + "\n")
        if write:
            new = X.shipped_fix(shipped[hit][0], u)
            tmp = hit + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(new, fh, indent=1, ensure_ascii=False)
            os.replace(tmp, hit)
    print(f"# {len(changed)} shipped map(s) {'corrected' if write else 'would change (--write to apply)'}", file=out)
    return changed


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--from", dest="src", default=default_state_maps(), help="folder with the checked user maps")
    ap.add_argument("--ship", default=os.path.join(REPO, "data", "plugin_maps"), help="shipped maps folder")
    ap.add_argument("--write", action="store_true", help="write the corrected shipped maps")
    a = ap.parse_args(argv)
    if not os.path.isdir(a.src):
        print(f"no user maps folder: {a.src}", file=sys.stderr)
        return 1
    run(a.src, a.ship, a.write)
    return 0


if __name__ == "__main__":
    sys.exit(main())
