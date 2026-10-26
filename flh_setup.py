"""fl-headless first-run setup: one guided checklist for a new user, a neutral starting profile and fl-headless's look.

    flh_setup(action="check")                       the checklist, in order (read only): 1 FL Studio, 2 song folders,
                                                    3 connection to FL, 4 FL edition, 5 the fl-headless look (FL
                                                    theme), 6 free extras (informational), 7 ready - each done /
                                                    needs you / optional with ONE plain next action, plus the exact
                                                    machine fields and next_steps
    flh_setup(action="apply", fl_edition=..., collection=..., finish=..., roots=...)
                                                    everything safe in one call: store the FL edition, create the
                                                    collection profile from neutral values (-14 LUFS, -1 dBTP, 24-bit)
                                                    unless given, put the bridge script into FL when missing, install
                                                    fl-headless's FL theme and make it FL's theme once (the user's
                                                    theme saved first as the visible preset 'My theme before
                                                    fl-headless')
    flh_setup(action="theme", apply=True, restore=False)
                                                    install / switch to the theme (FL closed), or restore the old one

Nothing here guesses the FL edition. check() takes it from the profile / this PC's stored answer / FLH_FL_EDITION, or
from the profile's owned + trial plugin lists when they pin exactly one edition; otherwise next_steps asks the user
ONE question with the four editions (FL's registry hint and the plugin lists narrow it), and apply(fl_edition=...)
records the answer ('unknown' records "not sure", so it isn't asked again). With the edition known, check() lists
what it unlocks beyond Fruity (flh_catalog.NATIVE_MIN).
Writable folders (FLH_ROOTS) are never changed by a tool call - they come from the MCP client's config (the
installer writes it) or, for the Claude Desktop extension (FLH_INSTALLED_BY=mcpb, see mcpb/launch.py), from the
folders picked in Claude's extension settings; apply(roots=...) only checks them and says how to set them. In the
extension nobody runs install.py, so 'set me up' (apply) does the FL side: the bridge script, the MIDI connection
over Windows' loopback when there is one (else the one step, in plain words), the edition question.
Extension contract: see flh_store.py.
"""
import os
import re
import sys
import time

import flh_store

H = {}
MIN_FL = (20, 8)                                         # oldest FL believed to work (MIDI scripting API)
TESTED_FL = "2026.1"


def _win():
    return bool(H.get("IS_WINDOWS", os.name == "nt"))


_ERR = object()


def _try(fn, default=_ERR):
    """fn() or, if it raises, `default` (when given) / {"error": ...}."""
    try:
        return fn()
    except Exception as e:                               # noqa: BLE001
        return {"error": f"{type(e).__name__}: {str(e)[:160]}"} if default is _ERR else default


def _cat():
    return sys.modules.get("flh_catalog")


def _fl_label(ver):
    """'26.1.4.5589' -> 'FL Studio 2026.1.4'; '21.2.3' -> 'FL Studio 21.2.3'."""
    if not ver:
        return None
    parts = str(ver).split(".")
    try:
        major = int(parts[0])
    except ValueError:
        return f"FL Studio {ver}"
    name = str(2000 + major) if 24 <= major < 100 else str(major)
    return "FL Studio " + ".".join([name] + parts[1:3])


def _fl_too_old(ver):
    try:
        p = [int(x) for x in str(ver).split(".")[:2]]
    except ValueError:
        return False
    return tuple(p) < MIN_FL


def _edition_hint():
    """An FL edition name found in FL's own registry values (FL writes the registered edition in some versions).
    Only a hint: shown to the user, never applied by itself."""
    if not _win():
        return None
    cat = _cat()
    if not cat:
        return None
    seen = {}
    dump = H["_reg_dump"]()
    for rel, vals in dump.items():
        for name, (typ, val) in vals.items():
            if typ not in (1, 2) or not isinstance(val, str) or len(val) > 200:
                continue
            m = re.search(r"(fruity|producer|signature|all plugins)\s+(edition|bundle)", val, re.I)
            if m:
                key = cat.edition_key(m.group(0))
                if key:
                    seen.setdefault(key, f"HKCU\\{H['REG_BASE']}\\{rel}\\{name} = {val[:80]}")
    if len(seen) == 1:
        k, ev = next(iter(seen.items()))
        return {"edition": k, "evidence": ev}
    return {"ambiguous": seen} if seen else None


EDITION_UNSURE = ("unknown", "not sure", "unsure", "dont know", "don't know", "idk", "skip", "none")


def _edition_report(win):
    """(the fl_edition part of check(), the one next-step question or None). Sources, in order: the profile /
    this PC's stored answer / FLH_FL_EDITION, the profile's owned + trial plugin lists when they pin one edition
    (flh_catalog.edition_from_plugins), else ONE question with the four editions (FL's registry hint and the plugin
    evidence narrow it, they never decide alone). An answer of 'not sure' is recorded, so it's asked once."""
    cat = _cat()
    if not cat:
        return {"edition": None, "source": None, "note": "flh_catalog isn't loaded"}, None
    ed, src = cat.fl_edition()
    rep = {"edition": ed, "name": cat.EDITION_NAMES.get(ed) if ed else None, "source": src}
    evid = _try(cat.edition_from_plugins, None)
    if src == "plugins" and evid:
        rep["evidence"] = evid["evidence"]
    if ed:
        u = cat.edition_unlocks(ed)
        rep["unlocks"] = ({"instruments": u["instruments"], "effects": u["effects"]} if ed != "fruity" else
                          "stock plugins only (no Sytrus / Edison / Gross Beat ...): new songs use FL Keys, BooBass, "
                          "3x Osc, Plucked!, DX10, FL's samples and free plugins (FLEX, LABS)")
        if u.get("not_included"):
            rep["trial_here"] = (f"{u['not_included']} higher-tier FL plugins stay demos: never used in songs "
                                 "(fl_sound_catalog(include_trial=true) lists them)")
        if ed == "fruity":
            rep["note"] = ("Fruity Edition can't hold audio clips in the playlist (verify for your FL version): "
                           "flp_add_audio_clip and fl_selftest's audio check may not apply")
        return rep, None
    hint = _try(_edition_hint, None) if win else None
    if hint:
        rep["registry_hint"] = hint
    if evid:
        rep["evidence"] = {k: evid[k] for k in ("at_least", "at_most", "evidence", "conflict") if k in evid}
    st = _try(lambda: H["_state"](), {}) or {}
    if st.get("fl_edition_unsure"):
        rep["note"] = ("the user wasn't sure (recorded): only plugins every edition has (plus free ones) are used; "
                       "flh_setup(action='apply', fl_edition=...) any time unlocks the rest")
        return rep, None
    opts = []
    for k in cat.EDITIONS:
        if evid and not evid.get("conflict") and not (cat.EDITIONS.index(evid["at_least"]) <= cat.EDITIONS.index(k)
                                                      <= cat.EDITIONS.index(evid["at_most"])):
            continue                                       # ruled out by the user's own plugin lists
        a = cat.edition_adds(k)
        adds = (a["instruments"] + a["effects"])[:5]
        opts.append(f"{k} = {cat.EDITION_NAMES[k]}" + (f" (adds {', '.join(adds)}...)" if adds else " (stock plugins)"))
    rep["ask"] = {"question": "Which FL Studio edition do you have? (FL: Help > About)",
                  "options": opts + ["unknown = not sure (only plugins every edition has are used)"],
                  "then": "flh_setup(action='apply', fl_edition='<option>')"}
    h = (hint or {}).get("edition")
    ask = ("ask the user once which FL edition they have (Help > About in FL): " + ", ".join(
        o.split(" =")[0] for o in opts) + " or not sure (fl_edition.ask has the question)"
        + (f"; FL's registry suggests {cat.EDITION_NAMES[h]}" if h else "")
        + (f"; their plugin lists say {cat.EDITION_NAMES[evid['at_least']]} to {cat.EDITION_NAMES[evid['at_most']]}"
           if evid and not evid.get("conflict") else "")
        + " - then flh_setup(action='apply', fl_edition='producer' ...) records it ('unknown' = not sure: not asked "
          "again)")
    return rep, ask


def _uninstall_names():
    out = []
    if not _win():
        return out
    import winreg
    un = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
    for wow in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
        for hive in ("HKEY_LOCAL_MACHINE", "HKEY_CURRENT_USER"):
            for sub in H["_reg_subkeys"](hive, un, wow):
                out.append(str(H["_reg_values"](hive, un + "\\" + sub, wow).get("DisplayName") or sub))
    return out


def _midi_check():
    """Ports Windows sees, which bridge pair would be used, loopMIDI / Windows MIDI Services installed."""
    r = {}
    ports = H["BRIDGE"].ports()
    pick = H["BRIDGE"].choose(ports)
    loop = H["_loopback_ports"](ports)
    r["ports"] = {"midi_out": ports["midi_out"], "midi_in": ports["midi_in"]}
    r["bridge_pair"] = list(pick) if pick else None
    r["loopmidi_ports"] = "Flapi Request" in ports["midi_out"] and "Flapi Response" in ports["midi_in"]
    r["windows_loopback_ports"] = loop
    names = " | ".join(_uninstall_names()).lower()
    pf86 = os.environ.get("ProgramFiles(x86)") or r"C:\Program Files (x86)"
    pf = os.environ.get("ProgramW6432") or os.environ.get("ProgramFiles") or r"C:\Program Files"
    r["loopmidi_installed"] = "loopmidi" in names or any(os.path.isfile(os.path.join(b, "Tobias Erichsen", "loopMIDI",
                                                                                     "loopMIDI.exe")) for b in (pf86, pf))
    r["windows_midi_services"] = ("windows midi services" in names or os.path.isdir(os.path.join(pf, "Windows MIDI Services"))
                                  or bool(H["_reg_values"]("HKEY_LOCAL_MACHINE", r"SYSTEM\CurrentControlSet\Services\MidiSrv")))
    return r


def _fl_midi_script_assigned():
    """FL MIDI inputs that load the fl-headless script (from FL's settings), newest FL version's key."""
    fk = H["_fl_key"]()
    d = H["_reg_dump"](fk + "\\Devices")
    out = []
    for k, vals in d.items():
        if "\\MIDI input\\" in k and str((vals.get("ScriptFolder") or [None, ""])[1]).lower() == "fl-headless":
            out.append({"port": k.rsplit("\\", 1)[-1], "enabled": str((vals.get("Enabled") or [None, ""])[1]) == "1",
                        "port_number": (vals.get("Port") or [None, None])[1]})
    return {"fl_settings_key": fk, "inputs": out}


def _ext():
    """Installed as a Claude Desktop extension (fl-headless.mcpb: mcpb/manifest.json sets FLH_INSTALLED_BY=mcpb), not
    with install.bat: the song folders and FL's path are then set in Claude's settings, not in a config file."""
    return os.environ.get("FLH_INSTALLED_BY", "").strip().lower() == "mcpb"


EXT_SETTINGS = "Claude's Settings > Extensions > fl-headless"     # PC-VERIFY: the menu names in Claude Desktop


def _claude_configs():
    """Claude Desktop's claude_desktop_config.json files on this PC (read only; the same places install.py uses)."""
    import glob
    roaming, local = os.environ.get("APPDATA", ""), os.environ.get("LOCALAPPDATA", "")
    c = ([os.path.join(roaming, "Claude", "claude_desktop_config.json")] if roaming else []) + (
        glob.glob(os.path.join(local, "Claude-Data", "*", "claude_desktop_config.json"))
        + glob.glob(os.path.join(local, "Packages", "Claude_*", "LocalCache", "Roaming", "Claude",
                                 "claude_desktop_config.json")) if local else [])
    return [p for p in dict.fromkeys(c) if os.path.isfile(p)]


def _double_install():
    """The config files that still start the manually installed fl-headless next to this extension (two copies
    would both drive FL), [] when there's none."""
    import json
    out = []
    for p in _claude_configs():
        try:
            with open(p, encoding="utf-8-sig") as f:
                m = (json.load(f) or {}).get("mcpServers") or {}
        except (OSError, ValueError, AttributeError):
            continue
        if any(isinstance(m.get(k), dict) and not m[k].get("disabled")       # install.py's name now, and the
               for k in ("fl-headless-manual", "fl-headless")):             # older one
            out.append(p)
    return out


def _script_stale():
    """Installed bridge scripts whose text differs from the one this version ships (after an update): [paths]."""
    out = []
    for path, first in H["_installed_scripts"]():
        try:
            with open(path, encoding="utf-8", newline="") as f:
                have = f.read()
            if have != H["_script_body"](first if first.startswith("# name=") else None):
                out.append(path)
        except (OSError, KeyError, TypeError):
            continue
    return out


def _connect(restart_fl=None):
    """The live connection to FL, done as far as it can be without the user: {status, say, do?, done?}.
    status: connected | set (FL connects when it starts) | needs your ok (FL must close and reopen once) | needs you.
    Windows' MIDI loopback present -> fl_bridge_setup(use_loopback) sets FL's MIDI settings itself (FL closed: just
    written; FL open: only with restart_fl=true - FL is saved, closed and reopened). Otherwise the one step, plainly."""
    st = _try(lambda: H["_state"](), {}) or {}
    if (st.get("bridge_input") or {}).get("port"):
        return {"status": "set", "say": f"Set up through your MIDI input '{st['bridge_input']['port']}'."}
    midi = _try(_midi_check)
    if not isinstance(midi, dict) or "error" in midi:
        return {"status": "needs you", "say": "Windows' MIDI isn't answering: restart the computer, then say "
                                              "'set me up' again."}
    running = _try(H["fl_running"], False) is True
    assigned = _try(_fl_midi_script_assigned)              # FL loads the script on an input: only then can it answer
    if isinstance(assigned, dict) and [i for i in assigned.get("inputs", []) if i.get("enabled")]:
        stt = _try(lambda: H["fl_bridge_setup"]("status")) if running else None
        if isinstance(stt, dict) and stt.get("fl_answers") is True:
            return {"status": "connected", "say": "Connected: I can work in FL live."}
        return {"status": "set", "say": "Set up: I connect when FL is open." + (
            " FL is open but doesn't answer yet - say 'check FL' and I'll find out why." if running else "")}
    if midi.get("loopmidi_ports"):
        return {"status": "needs you", "say": LOOPMIDI_CLICK}
    loop = midi.get("windows_loopback_ports") or []
    if loop:
        if running and restart_fl is not True:
            return {"status": "needs your ok", "do": "flh_setup(action='apply', restart_fl=true)",
                    "say": "To connect, FL closes and reopens once (your song is saved first). Say OK and I'll do it."}
        r = _try(lambda: H["fl_bridge_setup"]("use_loopback", restart_fl=bool(restart_fl)))
        if isinstance(r, dict) and r.get("done"):
            return {"status": "connected" if running else "set", "done": True,
                    "say": ("Connected to FL through Windows' MIDI loopback." if running else
                            "Connected FL to me through Windows' MIDI loopback: it connects the next time FL starts "
                            "(nothing to click in FL).")}
        why = str((r or {}).get("error") or (r or {}).get("why") or (r or {}).get("note") or "") if isinstance(r, dict) else ""
        if "hasn't listed" in why:
            return {"status": "needs you", "why": why[:200],
                    "say": "Open FL Studio once and close it again (so it sees Windows' MIDI loopback), then say "
                           "'set me up' again."}
        return {"status": "needs you", "why": why[:200],
                "say": "Connecting through Windows' MIDI loopback didn't work yet: say 'check FL' and I'll find out why."}
    keys = [n for n in midi.get("ports", {}).get("midi_in", []) if n not in loop]
    if midi.get("loopmidi_installed"):
        return {"status": "needs you", "say": "Open loopMIDI and add two ports named exactly 'Flapi Request' and "
                                              "'Flapi Response', then say 'set me up' again."}
    if midi.get("windows_midi_services"):
        return {"status": "needs you", "say": "Open Windows' 'MIDI Settings' app, add the default loopback, then say "
                                              "'set me up' again - I do the rest."}
    return {"status": "needs you", "keyboards": keys,
            "say": "One thing for the live connection: on Windows 11, open the 'MIDI Settings' app (Windows MIDI "
                   "Services) and add the default loopback - or install the free loopMIDI and add two ports named "
                   "'Flapi Request' and 'Flapi Response'"
                   + (f" - or I can use your keyboard '{keys[0]}' (its notes still play)" if keys else
                      " - or plug in a MIDI keyboard") + ". Then say 'set me up' again. Song files work without it."}


LOOPMIDI_CLICK = ("In FL: Options > MIDI settings > Input: pick 'Flapi Request', enable it, set Controller type to "
                  "'fl-headless' and a port number; give Output 'Flapi Response' the same number")


def _user_data_report():
    """Which FL user data folder won, why, and the other candidates (with their evidence)."""
    try:
        pick = H["_userdata_pick"](refresh=True)
    except Exception as e:                                   # noqa: BLE001
        return {"path": None, "error": f"{type(e).__name__}: {e}"}
    key = lambda p: os.path.normcase(os.path.abspath(p))
    win = pick.get("path") or ""
    rep = {"path": win, "exists": bool(win) and os.path.isdir(win), "layer": pick.get("layer"), "why": pick.get("why"),
           "others": [{k: c[k] for k in ("path", "exists", "found_via", "evidence") if k in c}
                      for c in pick.get("candidates", []) if key(c["path"]) != key(win)]}
    try:
        mine = [p for p, _ in H["_installed_scripts"]()]
        elsewhere = []
        for c in rep["others"]:
            if not c.get("exists"):
                continue
            for settings in (os.path.join(c["path"], "Settings"), os.path.join(c["path"], "FL Studio", "Settings")):
                for f in __import__("glob").glob(os.path.join(settings, "Hardware", "*", "device_*.py")):
                    try:
                        with open(f, encoding="utf-8", errors="replace") as fh:
                            if "fl-headless bridge" in fh.read(4000):
                                elsewhere.append(c["path"])
                    except OSError:
                        pass
        if elsewhere and not mine:
            rep["script_elsewhere"] = elsewhere[0]
    except Exception:                                        # noqa: BLE001
        pass
    return rep


STEP_NAMES = ("FL Studio", "Song folders", "Connection to FL", "FL edition", "fl-headless look", "Free extras", "Ready")
SAY_GO = "Say 'set it up' and I'll do this for you"
TRY_FIRST = ("make me a lofi beat", "set up a trap template in F minor", "what sounds do I have?")


def _extras():
    """Free extras found (informational): LABS / Splice INSTRUMENT packs, FLEX presets, BBC SO Discover, other
    installed plugins - from flh_catalog's cached sound index (no new scan inside one build)."""
    cat = _cat()
    if not cat:
        return {}
    b = _try(cat.building, {}) or {}
    if isinstance(b, dict) and "catalog" in b:               # the first scan is running (start-up warm-up): the
        return {"indexing": b["catalog"]}                     # checklist never waits up to 2 minutes for it
    c = cat._catalog()
    src = c.get("sources") or {}
    n = {}
    for e in c.get("entries", []):
        n[e["source"]] = n.get(e["source"], 0) + 1
    labs = src.get("LABS") or {}
    flex = src.get("FLEX") or {}
    out = {"labs_packs": labs.get("packs", 0) if labs.get("found") else 0,
           "instrument": bool(labs.get("found")),
           "flex_presets": flex.get("presets") or (n.get("FLEX", 0) if flex.get("found") else 0),
           "bbc": bool((src.get("BBC") or {}).get("found"))}
    vst = _try(cat.installed_vsts, set())
    out["other_plugins"] = len(vst) if isinstance(vst, (set, list)) else 0
    return out


def _extras_say(x):
    if x.get("indexing"):
        pct = (x["indexing"] or {}).get("pct") if isinstance(x["indexing"], dict) else None
        return ("Still scanning your sounds for the first time" + (f" ({pct}%)" if pct is not None else "")
                + " - ask again in a minute. None are required.")
    have = []
    if x.get("instrument"):
        have.append(f"Splice INSTRUMENT with {x['labs_packs']} LABS pack(s)" if x.get("labs_packs") else
                    "Splice INSTRUMENT")
    if x.get("flex_presets"):
        have.append(f"FLEX ({x['flex_presets']} presets)")
    if x.get("bbc"):
        have.append("BBC SO Discover")
    if x.get("other_plugins"):
        have.append(f"{x['other_plugins']} other plugin(s)")
    if have:
        return "Found: " + ", ".join(have) + ". None are required."
    return ("None found - none are required. Free ones you can add any time: FLEX (in FL's plugin list), LABS "
            "(Spitfire, through Splice INSTRUMENT).")


def _steps_out(S, win, edition, nxt):
    """The ordered checklist: [{n, step, status (done | needs you | optional), say, next?, do?, auto?}]."""
    pend = [S[i] for i in range(1, 6) if S[i]["status"] == "needs you"]
    r = S[7]
    ex = ", ".join(f"'{t}'" for t in TRY_FIRST[:2])
    if not pend and not nxt:
        r["say"] = (f"You're set. Try: {ex}." if win else
                    f"Ready for song files and audio here (live FL control needs Windows). Try: {ex}.")
    else:
        r["status"] = "needs you"
        mine = [s for s in pend if not s.get("auto")]
        first = (mine or pend or [None])[0]
        auto = len(pend) - len(mine)
        parts = ([f"{len(mine)} step{'s' if len(mine) > 1 else ''} above need{'' if len(mine) > 1 else 's'} you"]
                 if mine else []) + ([f"{auto} I can do for you"] if auto else [])
        r["say"] = ("; ".join(parts) or "One more thing above") + f". You can already ask: {ex}."
        if first is None:
            r["next"] = nxt[0]
        elif not mine:
            r["next"] = SAY_GO.replace("this", "the rest"); r["do"] = "flh_setup(action='apply')"; r["auto"] = True
        else:
            r["next"] = f"{first['step']}: {first['next']}"
            if first.get("do"):
                r["do"] = first["do"]
    return [{k: v for k, v in S[i].items() if v not in ("", None)} for i in range(1, 8)]


def check():
    """Everything a new user needs, as one ordered checklist (`steps`, plain words, ONE next action per step that
    needs the user; `do` = the call that does it, `auto` = flh_setup(action='apply') does it) plus the detailed
    machine fields and `next_steps` (exact, for the AI)."""
    win = _win(); nxt = []; ext = _ext()
    rep = {"fl_headless": H["SERVER"]["version"], "platform": sys.platform, "python": sys.version.split()[0],
           "installed_as": "claude_extension" if ext else "manual"}
    S = {i + 1: {"n": i + 1, "step": name, "status": "done", "say": ""} for i, name in enumerate(STEP_NAMES)}

    def need(i, machine, user=None, do=None, auto=False):
        if machine:
            nxt.append(machine)
        s = S[i]
        if s["status"] != "needs you":
            s["status"] = "needs you"; s["next"] = user or machine
            if do:
                s["do"] = do
            if auto:
                s["auto"] = True

    if sys.version_info < (3, 10):
        need(1, "Python 3.10 or newer is needed: install it from python.org and point the MCP config at it",
             "Install Python 3.10 or newer (python.org), then run the fl-headless installer again")
    try:
        import numpy
        rep["numpy"] = numpy.__version__
    except ImportError:
        rep["numpy"] = None
        if ext:                                          # uv installs numpy for the extension: its setup broke
            need(1, "numpy is missing although the extension's pyproject.toml asks uv for it: the extension's Python "
                    f"setup didn't finish - remove and reinstall fl-headless in {EXT_SETTINGS}",
                 f"Reinstall fl-headless: in {EXT_SETTINGS}, remove it, then install fl-headless.mcpb again "
                 "(its Python setup didn't finish)")
        else:
            need(1, f"install numpy (audio tools need it): \"{sys.executable}\" -m pip install numpy",
                 f"Install numpy (fl-headless needs it): run  \"{sys.executable}\" -m pip install numpy  in a terminal")
    dup = _try(_double_install, []) if ext and win else []
    if dup:
        rep["also_installed_manually"] = dup
        need(1, f"fl-headless is installed twice: this extension and a manual install in {dup[0]} (two servers would "
                "both drive FL) - run the old folder's install.bat --uninstall, or delete the 'fl-headless' entry "
                "from that file, then fully quit and reopen Claude",
             "fl-headless is installed twice (also the older way): in your old fl-headless folder run "
             "'install.bat --uninstall', then fully quit and reopen Claude")
    rep["fl_edition"], ask = _edition_report(win)
    ed = rep["fl_edition"]
    if not win:
        rep["fl"] = {"supported": False, "note": "FL-facing tools need Windows; FL Studio on macOS isn't supported. "
                     "File (flp_*) and audio (audio_*) tools work here."}
        for i in (1, 3, 5):
            if S[i]["status"] == "done":
                S[i]["status"] = "optional"
        S[1]["say"] = S[1]["say"] or ("Live control of FL Studio needs Windows. Here I can still write and edit FL "
                                      "song files (.flp) and work with audio.")
        S[3]["say"] = "Needs Windows."
        S[5]["say"] = "FL themes are set up on Windows."
    # FL itself
    if win:
        fl = {}
        installs = _try(H["fl_installs"], [])
        fl["installs"] = [{"exe": p, "found_via": s} for p, s in installs] if isinstance(installs, list) else installs
        try:
            exe = H["fl_exe"](); fl["exe"] = exe
            ver = _try(lambda: H["_fl_file_version"](), None)
            fl["version"] = ver if isinstance(ver, str) else None
            fl["name"] = _fl_label(fl["version"])
            S[1]["say"] = (fl["name"] or "FL Studio") + " found" + (f" ({ed['name']})" if ed.get("name") else "")
            if fl["version"] and _fl_too_old(fl["version"]):
                need(1, f"{fl['name']} is older than FL 20.8: the live bridge needs FL's MIDI scripting (20.8+); "
                        "update FL, or use only the file/audio tools",
                     "Update FL Studio to 20.8 or newer for live control (song files work either way)")
            elif fl["version"] and not str(fl["version"]).startswith("26."):
                fl["note"] = f"fl-headless is tested on FL Studio {TESTED_FL}: run fl_selftest once (FL closed) to check this version"
                S[1]["say"] += f" - tested on {TESTED_FL}, a quick self-test is suggested"
        except FileNotFoundError as e:
            fl["exe"] = None
            if ext:
                need(1, f"FL Studio not found ({e}). If it's installed somewhere unusual, the user picks FL64.exe as "
                        f"'FL Studio program' in {EXT_SETTINGS}",
                     f"Install FL Studio - or, if it's installed somewhere unusual, pick FL64.exe as 'FL Studio "
                     f"program' in {EXT_SETTINGS}")
            else:
                need(1, f"FL Studio not found ({e}). If it's installed somewhere unusual, set FLH_FL_EXE to FL64.exe in "
                        "the fl-headless entry of the MCP config (\"env\")",
                     "Install FL Studio - or, if it's installed somewhere unusual, tell me where FL64.exe is",
                     do="set FLH_FL_EXE in the fl-headless entry of the MCP config")
        fl["running"] = _try(H["fl_running"], False)
        blank = _try(H["_blank_project"], None)
        fl["blank_template"] = blank if isinstance(blank, str) else None
        if not fl["blank_template"]:
            fl["blank_template_note"] = ("no empty FL template found: refreshing the open project still works, but its "
                                         "slow fallback needs one - set FLH_BLANK to an empty .flp")
        ud = _user_data_report()
        fl["user_data"] = ud
        if not ud.get("path") or not ud.get("exists"):
            need(1, "FL's user data folder (Documents\\Image-Line\\FL Studio) wasn't found: start FL once; if you "
                    "moved it, flh_setup(action='apply', user_data='<the folder FL shows in Options > File settings>')",
                 "Start FL Studio once (it creates its settings folder), then check again")
        elif ud.get("layer") in ("activity", "first") and len([c for c in ud.get("others", []) if c.get("exists")]) >= 1:
            need(1, f"several FL user data folders exist and fl-headless had to guess ({ud['path']}): check FL's "
                    "Options > File settings > User data folder and confirm it with flh_setup(action='apply', "
                    "user_data='...')",
                 "Tell me the folder FL shows in Options > File settings > User data folder",
                 do="flh_setup(action='apply', user_data='<that folder>')")
        if ud.get("script_elsewhere"):
            need(3, f"the bridge script is installed in {ud['script_elsewhere']} but FL uses {ud['path']}: run "
                    "fl_bridge_setup(action='update_script')", SAY_GO, do="fl_bridge_setup(action='update_script')",
                 auto=True)
        rep["fl"] = fl
    # writable folders + the collection profile
    rts = H["roots"](); bad = [r for r in rts if not os.path.isdir(r)]
    rel = H["relative_roots"]() if "relative_roots" in H else []
    rep["writable_roots"] = {"FLH_ROOTS": rts, "missing": bad}
    pick = (f"In {EXT_SETTINGS}, pick the folder(s) for your songs (Song folders) and save" if ext else None)
    if rel:
        rep["writable_roots"]["ignored_not_absolute"] = rel
        need(2, f"these FLH_ROOTS entries are not absolute paths, so they're ignored: {rel} - write each folder in "
                "full (like D:\\FL Projects) in the MCP config, or run the installer again",
             pick or "Run the fl-headless installer again and give each song folder in full (like D:\\FL Projects)")
    if not rts:
        need(2, "FLH_ROOTS isn't set, so nothing can be written: run the installer again (install.bat) and give your "
                "music / FL projects folders, or add \"FLH_ROOTS\": \"C:\\\\Users\\\\you\\\\Music;D:\\\\FL Projects\" to the "
                "fl-headless entry's env in the MCP config, then restart the MCP client",
             pick or "Run the fl-headless installer again (install.bat) and pick the folder(s) for your songs, then "
             "restart Claude")
    elif bad:
        need(2, f"these FLH_ROOTS folders don't exist: {bad} - create them or fix the MCP config"
                + (f" (extension: {EXT_SETTINGS}, Song folders)" if ext else ""),
             f"Create {bad[0]}" + (f" (+{len(bad) - 1} more)" if len(bad) > 1 else "") +
             (f" - or pick folders that exist in {EXT_SETTINGS}" if ext else
              " - or run the installer again and pick folders that exist"))
    prof = _try(flh_store.profile_get, {})
    rep["profile"] = {"active": flh_store.profile_name(), "exists": bool(prof) and "error" not in prof,
                      "finish": flh_store.song_target(prof if isinstance(prof, dict) and "error" not in prof else {}, None)}
    good = [r for r in rts if r not in bad]
    fin = rep["profile"]["finish"] or {}
    fin = fin if isinstance(fin.get("lufs"), (int, float)) else {}
    S[2]["say"] = (("Songs go in " + good[0] + (f" (+{len(good) - 1} more)" if len(good) > 1 else "") + ". ")
                   if good else "") + (f"Finished songs: {fin.get('lufs'):g} LUFS, {fin.get('bits')}-bit"
                                       + (f" ('{rep['profile']['active']}' settings)" if rep["profile"]["exists"] else "")
                                       + "." if fin else "")
    # the connection to FL (live bridge)
    if win:
        midi = _try(_midi_check)
        rep["midi"] = midi
        if isinstance(midi, dict) and "error" in midi:
            need(3, f"couldn't list MIDI ports ({midi['error']}): live tools need Windows' MIDI (winmm) to work",
                 "Windows' MIDI isn't answering: restart the computer, then check again")
        on_input = _try(lambda: (H["_state"]().get("bridge_input") or {}).get("port"), None)
        if on_input:
            rep["bridge_on_input"] = on_input                 # no virtual port needed: the mailbox carries it all
        if isinstance(midi, dict) and "error" not in midi and not midi.get("bridge_pair") and not on_input:
            if midi.get("windows_loopback_ports"):
                need(3, f"a Windows MIDI loopback port is there ({midi['windows_loopback_ports'][0]}): run "
                        "fl_bridge_setup(action='use_loopback') - it sets FL's MIDI settings and restarts FL once",
                     "Say OK and I'll connect to FL (FL restarts once)", do="fl_bridge_setup(action='use_loopback')")
            elif midi.get("loopmidi_installed"):
                need(3, "loopMIDI is installed but its ports are missing: open loopMIDI and add two ports named exactly "
                        "'Flapi Request' and 'Flapi Response' (it remembers them)",
                     "Open loopMIDI and add two ports named exactly 'Flapi Request' and 'Flapi Response'")
            elif midi.get("windows_midi_services"):
                need(3, "Windows MIDI Services is installed: open its MIDI Settings app, create the default loopback "
                        "endpoints, then run fl_bridge_setup(action='use_loopback')",
                     "Open Windows' MIDI Settings app, add the default loopback, then tell me",
                     do="fl_bridge_setup(action='use_loopback')")
            else:
                need(3, "live tools need a virtual MIDI port: install loopMIDI (tobias-erichsen.de) and create ports "
                        "'Flapi Request' and 'Flapi Response' - or on Windows 11 with Windows MIDI Services, add its "
                        "loopback and run fl_bridge_setup(action='use_loopback') - or, with a MIDI keyboard "
                        "connected, fl_bridge_setup(action='use_input') runs the bridge on its input (no extra app)",
                     "Install the free app loopMIDI (tobias-erichsen.de) and add two ports named 'Flapi Request' and "
                     "'Flapi Response' - or plug in a MIDI keyboard and tell me",
                     do="with a keyboard: fl_bridge_setup(action='use_input')")
        scripts = _try(lambda: [p for p, _ in H["_installed_scripts"]()], [])
        rep["fl_script"] = {"installed": scripts}
        bridge_steps = []                                   # only shown when the bridge doesn't answer
        if not scripts:
            bridge_steps.append(("the bridge script isn't in FL yet: run fl_bridge_setup(action='update_script') (or the "
                                 "installer)", SAY_GO, "fl_bridge_setup(action='update_script')", True))
        assigned = _try(_fl_midi_script_assigned)
        rep["fl_script"]["midi_inputs"] = assigned
        if isinstance(assigned, dict) and "error" not in assigned and not [i for i in assigned["inputs"] if i["enabled"]]:
            loop_ = midi.get("windows_loopback_ports") if isinstance(midi, dict) else None
            if loop_ and not midi.get("loopmidi_ports") and not on_input:
                if rep["fl"].get("running"):              # FL must close for its MIDI settings: the user's OK first
                    bridge_steps.append((f"FL's MIDI settings don't load the script on '{loop_[0]}' yet: ask the user, "
                                         "then flh_setup(action='apply', restart_fl=true) (FL is saved, closed and "
                                         "reopened once)",
                                         "Say OK and I'll connect to FL: it closes and reopens once (your song is "
                                         "saved first)", "flh_setup(action='apply', restart_fl=true)", False))
                else:
                    bridge_steps.append((f"FL's MIDI settings don't load the script on '{loop_[0]}' yet: "
                                         "flh_setup(action='apply') sets them (FL is closed)", SAY_GO,
                                         "flh_setup(action='apply')", True))
            if isinstance(midi, dict) and midi.get("loopmidi_ports"):
                bridge_steps.append(("in FL: Options > MIDI settings > Input: select 'Flapi Request', enable it, set "
                                     "Controller type to 'fl-headless' and a port number; give Output 'Flapi Response' "
                                     "the same port number",
                                     "In FL: Options > MIDI settings > Input: pick 'Flapi Request', enable it, set "
                                     "Controller type to 'fl-headless' and a port number; give Output 'Flapi "
                                     "Response' the same number", None, False))
        answers = False
        if rep["fl"].get("running"):
            st = _try(lambda: H["fl_bridge_setup"]("status"))
            rep["bridge"] = {k: st.get(k) for k in ("transport", "ports", "fl_answers")} if isinstance(st, dict) else st
            if isinstance(st, dict) and st.get("fl_answers") is not True and "error" not in st and not bridge_steps:
                need(3, "FL is open but the bridge doesn't answer: fl_watchdog() says why (often a popup, or the "
                        "script not picked in MIDI settings)",
                     "FL is open but doesn't answer yet - say 'check FL' and I'll find out why (often a popup)",
                     do="fl_watchdog()")
            if isinstance(st, dict) and st.get("fl_answers") is True:
                answers = True
                bridge_steps = []                           # it works, whatever script name/port it uses
                if H.get("_note_script_location") and H["_note_script_location"]():   # proof of FL's user data
                    rep["fl"]["user_data"] = _user_data_report()                       # folder: decide again
                rep["fl_script"]["note"] = "the bridge answers: script and MIDI settings are fine"
        for b in bridge_steps:
            need(3, *b[:2], do=b[2], auto=b[3])
        S[3]["say"] = ("Connected: I can work in FL live." if answers else
                       "Set up" + (f" (through your MIDI input '{on_input}')" if on_input else "") +
                       ": I connect when FL is open." if S[3]["status"] == "done" else
                       "Lets me play, change and listen to your song in FL while it's open.")
        cap = _try(lambda: __import__("fl_capture").available(), (False, "fl_capture missing"))
        rep["listening"] = {"ok": bool(cap[0]), "why": cap[1]} if isinstance(cap, tuple) else cap
    # the FL edition (one question)
    if ask:
        names = [o.split(" =")[0] for o in (ed.get("ask") or {}).get("options", [])]
        plain = [_cat().EDITION_NAMES.get(k, k).replace(" Edition", "") if _cat() else k for k in names
                 if k != "unknown"]
        need(4, ask, "Which FL Studio edition do you have? (FL: Help > About) " + ", ".join(plain) + " - or 'not sure'",
             do="flh_setup(action='apply', fl_edition='<answer>')")
    elif ed.get("edition"):
        S[4]["say"] = f"{ed['name']}: I use its plugins, never demos."
    elif "wasn't sure" in str(ed.get("note")):
        S[4]["say"] = "Not sure (fine): I use only plugins every edition has."
    else:
        S[4]["status"] = "optional"; S[4]["say"] = ed.get("note") or "Unknown."
    # the fl-headless look (FL theme)
    if win and _theme_fl_too_old():
        S[5]["status"] = "optional"; S[5]["say"] = "FL's themes need FL Studio 21 or newer - nothing to set up here."
    if _theme_supported():
        th = _try(_theme_status)
        rep["theme"] = th
        S[5]["status"] = "done"
        if not isinstance(th, dict) or "error" in th:
            S[5]["status"] = "optional"; S[5]["say"] = "Couldn't check the FL theme folder - nothing changed."
        else:
            back = _theme_back_line(th)
            if not th.get("folder"):
                need(5, "FL's user themes folder wasn't found: start FL once, then flh_setup(action='apply')",
                     "Start FL Studio once (it creates its settings folder), then say 'set it up'")
            elif th.get("active"):
                S[5]["say"] = "On. " + back
                if th.get("installed") == "outdated":
                    need(5, "fl-headless's FL theme has an update: flh_setup(action='apply')", SAY_GO,
                         do="flh_setup(action='apply')", auto=True)
            elif th.get("pending"):
                need(5, "FL was open during setup: the user picks fl-headless in FL's Options > Theme (one click)"
                        + ("; it also switches by itself the next time fl-headless starts FL" if _start_hook() else
                           "; or flh_setup(action='apply') with FL closed"),
                     "In FL: Options > Theme > pick 'fl-headless' (one click)", auto=False)
                S[5]["say"] = "Installed. " + back
            elif th.get("user_choice"):
                S[5]["say"] = "You picked another theme in FL - kept. 'fl-headless' stays in Options > Theme."
            else:
                need(5, "fl-headless's FL theme isn't set up yet: flh_setup(action='apply') installs it and makes it "
                        "FL's theme (the current one is saved as a preset first)",
                     SAY_GO + " (your current theme is saved as a preset first, so you can switch back)",
                     do="flh_setup(action='apply')", auto=True)
    # profile (after the theme: setup does both in one go)
    if not rep["profile"]["exists"]:
        need(2, "no collection profile yet: flh_setup(action='apply', collection='My Songs') makes a neutral one "
                "(-14 LUFS, -1 dBTP, 24-bit FLAC finals); change it any time with flh_profile",
             SAY_GO + " (default song settings, change any time)", do="flh_setup(action='apply')", auto=True)
    # free extras (informational, never required)
    S[6]["status"] = "optional"
    x = _try(_extras, {})
    if isinstance(x, dict) and "error" not in x:
        rep["extras"] = x
        S[6]["say"] = _extras_say(x)
    else:
        S[6]["say"] = "Not checked (the sound index isn't loaded). None are required."
    rep["ready"] = not nxt
    rep["next_steps"] = nxt or ["all set: try fl_status, then fl_sound_catalog() to see your sounds"]
    steps = _steps_out(S, win, ed, nxt)
    return dict({"steps": steps, "ready": rep["ready"]}, **rep)


def _set_user_data(path):
    """Store FL's user data folder for this PC (wins over FL's registry and every guess; FLH_FL_USERDATA still wins)."""
    p = os.path.normpath(os.path.expandvars(os.path.expanduser(str(path).strip().strip('"'))))
    if not os.path.isdir(p):
        raise ValueError(f"user_data: {p} doesn't exist (give the folder FL shows in Options > File settings > User "
                         "data folder)")
    norm = H["_ud_norm"](p)
    if not any(os.path.isdir(os.path.join(norm, *s)) for s in (("Settings",), ("FL Studio", "Settings"))):
        raise ValueError(f"user_data: {p} doesn't look like FL's user data folder (no 'Settings' inside "
                         f"{norm}); FL's setting names the 'Image-Line' folder or its 'FL Studio' subfolder")
    H["_state"]({"fl_userdata": norm})
    H["_DOCS_CACHE"].clear()
    return norm


def apply(roots=None, fl_edition=None, collection=None, finish=None, user_data=None, theme=None, restart_fl=None):
    """Everything that's safe, in one call ('set me up'): store the FL edition (and FL's user data folder) when given,
    create / fill the collection profile with neutral values (never overwrites a value the profile already has
    unless it's given here), put the bridge script into FL when it's missing or older than this version's, connect
    FL when Windows' MIDI loopback is there (FL closed: its MIDI settings are written; FL open: only with
    restart_fl=true, FL is saved, closed and reopened once), install fl-headless's FL theme and make it FL's theme
    once (the user's theme saved as a visible preset first). Returns the checklist after it (`steps`) and
    `connection` (the live link: what was done, or the one step left, in plain words)."""
    t0 = time.time()
    if collection is not None:
        collection = flh_store.safe_name(collection)              # a file name: checked before anything is stored
    done = []; notes = []
    cat = _cat()
    if user_data is not None:
        norm = _set_user_data(user_data)
        done.append(f"FL user data folder: {norm} (this PC)")
        if os.environ.get("FLH_FL_USERDATA"):
            notes.append("FLH_FL_USERDATA is set in the MCP config and still wins over this")
        notes.append("if FL's bridge script lives in another copy, run fl_bridge_setup(action='update_script') so it's "
                     "installed where FL loads scripts from")
    unsure = fl_edition is not None and str(fl_edition).strip().lower().replace("_", " ") in EDITION_UNSURE
    if unsure:
        fl_edition = None
        H["_state"]({"fl_edition_unsure": True})
        done.append("FL edition: not sure (recorded, not asked again): new songs use only plugins every edition has "
                    "plus free ones; flh_setup(action='apply', fl_edition=...) any time unlocks the rest")
    if fl_edition is not None:
        key = cat.edition_key(fl_edition) if cat else None
        if not key:
            raise ValueError(f"unknown FL edition '{fl_edition}': fruity, producer, signature, all_plugins (or "
                             "unknown when the user isn't sure)")
        H["_state"]({"fl_edition": key, "fl_edition_unsure": False})
        u = cat.edition_unlocks(key)
        done.append(f"FL edition: {cat.EDITION_NAMES[key]} (this PC)" + (
            f" - beyond Fruity it includes {', '.join(u['instruments'])} and {len(u['effects'])} effects "
            f"({', '.join(u['effects'][:4])}...)" if u.get("instruments") else
            " - stock plugins only: new songs use FL Keys, BooBass, 3x Osc, Plucked!, DX10, FL's samples, free plugins"))
    if roots:
        rs = [str(r).strip().strip('"').strip() for r in (roots.split(";") if isinstance(roots, str) else roots)
              if str(r).strip().strip('"').strip()]
        missing = [r for r in rs if not os.path.isdir(r)]
        now = [os.path.normcase(os.path.abspath(r)) for r in H["roots"]()]
        new = [r for r in rs if os.path.normcase(os.path.abspath(r)) not in now]
        if _ext():
            notes.append(f"writable folders are picked by the user in {EXT_SETTINGS} (Song folders), not by a tool "
                         "call (that keeps the write limit in the user's hands)" + (
                             ": add them there and save" if new else ": these are already writable"))
        else:
            notes.append("writable folders are set in the MCP client's config, not by a tool call (that keeps the write "
                         "limit in the user's hands)" + (f": run  python install.py \"{';'.join(rs)}\"  in the "
                         "fl-headless folder, then restart the MCP client" if new else ": these are already writable"))
        if missing:
            notes.append(f"these folders don't exist yet: {missing}")
    name = collection or flh_store.profile_name() or "My Songs"
    cur = flh_store.profile_get(name)
    upd = {}
    if not cur:
        upd["finish"] = dict(flh_store.FINISH_DEFAULT, fade_s=2.5)
        upd["final_name"] = "{song} - FINAL.flac"
    if finish:
        if not isinstance(finish, dict):
            raise ValueError("finish: {lufs, true_peak, bits, fade_s}")
        bad = set(finish) - {"lufs", "true_peak", "bits", "fade_s"}
        if bad:
            raise ValueError(f"finish: unknown key(s) {sorted(bad)}")
        upd["finish"] = dict(upd.get("finish") or cur.get("finish") or {}, **finish)
    if fl_edition is not None:
        upd["fl_edition"] = cat.edition_key(fl_edition)
    if upd or not cur:
        flh_store.profile_set(name, upd, activate=True)
        done.append(("created" if not cur else "updated") + f" collection profile '{name}'")
    else:
        H["_state"]({"collection": name}); done.append(f"collection '{name}' is active (unchanged)")
    if _win():                                               # the bridge script into FL (no restart, nothing else)
        try:
            ud = H["_userdata_pick"]().get("path") if "_userdata_pick" in H else None
            have = [p for p, _ in H["_installed_scripts"]()]
            stale = _try(_script_stale, []) if have else []
            if ud and os.path.isdir(ud) and (not have or stale):  # (also the 'installed in another copy' case)
                r = H["fl_bridge_setup"]("update_script")
                done.append(("updated fl-headless's script in FL to this version: " if have else
                             "put fl-headless's script into FL: ") + ", ".join(r.get("updated") or []) +
                            " (FL loads it on its next start)")
        except Exception as e:                               # noqa: BLE001
            notes.append(f"FL script: {type(e).__name__}: {str(e)[:120]}")
    con = None
    if _win():                                               # the live connection: done here when it can be
        con = _try(lambda: _connect(restart_fl), None)
        if isinstance(con, dict) and con.get("done"):
            done.append(con["say"])
    if theme is False:
        notes.append("the fl-headless look is part of setup (your own theme is saved as a preset you can pick any "
                     "time in Options > Theme)")
    th = {}
    if _theme_supported():                                   # install, make it FL's theme once (or say the click)
        th = _try(lambda: theme_setup(apply=True, auto=True))
        if "error" in th:
            notes.append(f"FL theme: {th['error']}")
        else:
            done += th.get("done", []); notes += th.get("notes", []) + ([th["note"]] if th.get("note") else [])
    rep = check()
    out = {"done": done, "notes": notes, "steps": rep["steps"], "profile": flh_store.profile_get(name),
           "fl_edition": rep.get("fl_edition"), "ready": rep["ready"], "next_steps": rep["next_steps"],
           "steps_s": {"total": round(time.time() - t0, 3)}}
    if th.get("switch_back"):
        out["switch_back"] = th["switch_back"]                # one line: how to get the old look back
    if isinstance(con, dict):
        out["connection"] = {k: v for k, v in con.items() if k != "done"}
    if _ext():
        out["installed_as"] = "claude_extension"
    return out


# ============================================================================= fl-headless's own FL theme
# Shipped in data/theme: '<name>.flstheme' (UTF-8 with BOM, CRLF, key=value; BackPicFilename relative to its folder),
# the background picture and the thumbnail 'thm<name>.png'. Installed into FL's USER themes folder (never FL's
# install) and always made FL's theme once by setup: first the user's current theme is saved as a normal, visible
# preset ('My theme before fl-headless' + a swatch thumbnail) in the same folder, so switching back is one pick in
# Options > Theme. Written once, never overwritten; a theme the user picks later is never switched back. With FL
# open, setup installs the files and the user clicks once (or it switches the next time fl-headless starts FL).
# Writes go only to FL's Settings\Themes folder found below (AGENTS.md allows FL's settings folder; the server's
# writable() covers FLH_ROOTS only, so the narrow check is _theme_target_ok: our file names, inside a
# '...\Settings\Themes' folder this discovery found).
THEME_NAME = "fl-headless"
THEME_FILES = ("fl-headless.flstheme", "fl-headless background.jpg", "thmfl-headless.png")
THEME_CURRENT = "__current.flstheme"                     # FL's active theme settings (FL rewrites it itself)
THEME_BACKUP = "My theme before fl-headless.flstheme"    # the user's theme before ours (written once, never replaced)
THEME_LEGACY = "__before fl-headless.flstheme"           # the 2.1.0 dev builds' hidden backup (read, migrated)
_DATED = r"( \(\d{4}-\d\d-\d\d \d{4}\))?"
_BACKUP_RE = re.compile(rf"^(My theme before fl-headless{_DATED}\.flstheme|thmMy theme before fl-headless{_DATED}\.png)$")
THEME_SIGNATURE = ("Hue", "Saturation", "Lightness", "Contrast", "Selected", "Highlight", "BackColor",
                   "BackPicFilename")                     # keys that tell 'ours is the active theme'


THEME_MIN_FL = 21                                         # FL's themes (Options > Theme, .flstheme) came with FL 21
# PC-VERIFY: FL 20.8 has no Options > Theme (checklists/setup.md, theme section)


def _theme_fl_too_old():
    """FL's version when it's known and older than FL 21 (no themes there: nothing to install or claim), else None."""
    ver = _try(lambda: H["_fl_file_version"](), None)
    try:
        return ver if isinstance(ver, str) and int(ver.split(".")[0]) < THEME_MIN_FL else None
    except ValueError:
        return None


def _theme_supported():
    return _win() and not _theme_fl_too_old()


def _theme_src(name):
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "theme", name)


def _sha(path):
    import hashlib
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


def _env_expand(s):
    """%VAR% (Windows registry REG_EXPAND_SZ style) and $VAR expanded on any OS."""
    s = re.sub(r"%([^%\\/]+)%", lambda m: os.environ.get(m.group(1), m.group(0)), str(s))
    return os.path.expandvars(s)


def _theme_documents():
    """[(Documents folder, where it came from)]: the shell-folders registry value 'Personal' (OneDrive redirection),
    the known-folder API (server), %USERPROFILE%\\Documents, %USERPROFILE%\\OneDrive\\Documents, %OneDrive%\\Documents."""
    out = []
    try:
        v = H["_reg_values"]("HKEY_CURRENT_USER", r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders")
        if v.get("Personal"):
            out.append((_env_expand(str(v["Personal"])), "registry User Shell Folders\\Personal"))
    except Exception:                                        # noqa: BLE001
        pass
    try:
        out += [(d, "Windows Documents folder") for d in H["_known_documents"]()]
    except Exception:                                        # noqa: BLE001
        pass
    up = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    out += [(os.path.join(up, "Documents"), "%USERPROFILE%\\Documents"),
            (os.path.join(up, "OneDrive", "Documents"), "%USERPROFILE%\\OneDrive\\Documents")]
    for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        if os.environ.get(var):
            out.append((os.path.join(os.environ[var], "Documents"), f"%{var}%\\Documents"))
    return out


def _theme_candidates():
    """Every place FL's user themes folder could be: [{path, exists, found_via}] (Settings\\Themes and the nested
    'FL Studio\\FL Studio\\Settings\\Themes' FL 2026 used on the test PC, under each Documents folder and under the
    FL user data folder the server picked)."""
    c = []
    try:
        ud = H["_fl_userdata"]()
    except Exception:                                        # noqa: BLE001
        ud = ""
    if ud:
        c += [(os.path.join(ud, "FL Studio", "Settings", "Themes"), "FL user data folder"),
              (os.path.join(ud, "Settings", "Themes"), "FL user data folder")]
    for d, why in _theme_documents():
        base = os.path.join(d, "Image-Line", "FL Studio")
        c += [(os.path.join(base, "FL Studio", "Settings", "Themes"), why),
              (os.path.join(base, "Settings", "Themes"), why)]
    out = {}
    for p, why in c:
        p = os.path.normpath(p); k = os.path.normcase(os.path.abspath(p))
        if k not in out:
            out[k] = {"path": p, "exists": os.path.isdir(p), "settings": os.path.isdir(os.path.dirname(p)),
                      "found_via": [why], "from_userdata": why == "FL user data folder"}
        elif why not in out[k]["found_via"]:
            out[k]["found_via"].append(why)
    return list(out.values())


def _is_backup_name(name):
    return bool(_BACKUP_RE.match(name)) or name.lower().startswith("__before fl-headless")


def _theme_backups(folder):
    """The saved 'before fl-headless' presets in folder, oldest name first: the visible ones, then the legacy one."""
    try:
        names = sorted(n for n in os.listdir(folder) if _BACKUP_RE.match(n) and n.endswith(".flstheme"))
    except OSError:
        return []
    names.sort(key=lambda n: (n != THEME_BACKUP, n))
    return names + ([THEME_LEGACY] if os.path.isfile(os.path.join(folder, THEME_LEGACY)) else [])


def _theme_activity(folder, st):
    """(mtime, file) of FL's own newest theme file in `folder`: __current.flstheme first (FL writes it for the active
    theme), else any other .flstheme. A __current that is still exactly what setup wrote isn't FL's evidence: the
    backup (copied with its time kept) stands in for it."""
    cur = os.path.join(folder, THEME_CURRENT)
    if os.path.isfile(cur):
        if st.get("written_hash") and _sha(cur) == st["written_hash"]:
            for b in [st.get("backup_file")] + _theme_backups(folder):
                if b and os.path.isfile(os.path.join(folder, b)):
                    return 2, os.path.getmtime(os.path.join(folder, b)), b
        return 2, os.path.getmtime(cur), THEME_CURRENT
    best = (0, 0.0, None)
    try:
        for e in os.scandir(folder):
            low = e.name.lower()
            if low.endswith(".flstheme") and e.name != THEME_FILES[0] and not _is_backup_name(e.name) and e.is_file():
                t = e.stat().st_mtime
                if t > best[1]:
                    best = (1, t, e.name)
    except OSError:
        pass
    return best


def _theme_folder():
    """FL's user themes folder: {path, why, candidates}; path None when no FL settings folder exists.
    Newest __current.flstheme wins, then the newest other .flstheme, then the FL user data folder's Settings.
    PC-VERIFY: the chosen folder is the one Options > Theme lists user themes from."""
    st = (H["_state"]().get("theme") or {})
    cands = _theme_candidates()
    have = [c for c in cands if c["exists"]]
    acts = sorted(((_theme_activity(c["path"], st), c) for c in have), key=lambda a: (-a[0][0], -a[0][1]))
    res = None
    if acts and acts[0][0][0]:
        (lvl, t, f), c = acts[0]
        res = {"path": c["path"], "why": f"FL wrote {f} here most recently "
                                         f"({time.strftime('%Y-%m-%d %H:%M', time.localtime(t))})"}
    if res is None:
        for c in [c for c in cands if c["from_userdata"]] + cands:
            if c["exists"] or c["settings"]:
                res = {"path": c["path"], "why": ("" if c["exists"] else "Themes not created yet; ")
                       + ("under FL's user data folder" if c["from_userdata"] else "FL's Settings folder exists here")
                       + " (no theme files anywhere yet)"}
                break
    if res is None:
        res = {"path": None, "why": "no FL Settings folder found: start FL once"}
    k = os.path.normcase(os.path.abspath(res["path"])) if res["path"] else None
    res["others"] = [c["path"] for c in cands if (c["exists"] or c["settings"])
                     and os.path.normcase(os.path.abspath(c["path"])) != k]
    return res


def _theme_target_ok(path):
    """The narrow write check for theme files: one of our file names, directly inside a '...\\Settings\\Themes'
    folder that discovery found (links resolved: a junction can't lead elsewhere). Never FL's install folder."""
    name = os.path.basename(path)
    if name not in THEME_FILES + (THEME_CURRENT,) and not _BACKUP_RE.match(name):
        raise PermissionError(f"{path}: not a theme file fl-headless may write")
    folder = os.path.dirname(os.path.abspath(path))
    keys = {os.path.normcase(os.path.abspath(c["path"])) for c in _theme_candidates()}
    real = os.path.normcase(os.path.realpath(folder))
    parts = [x.lower() for x in re.split(r"[\\/]+", real) if x]
    if os.path.normcase(folder) not in keys or parts[-2:] != ["settings", "themes"] or "artwork" in parts:
        raise PermissionError(f"{path}: outside FL's user themes folder")
    return True


def _theme_write(dest, data):
    _theme_target_ok(dest)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest + ".tmp", "wb") as f:
        f.write(data)
    H["_retry_os"](lambda: os.replace(dest + ".tmp", dest)) if "_retry_os" in H else os.replace(dest + ".tmp", dest)


def _theme_keys(path):
    """{key lower: value} of a .flstheme ('' values kept); {} when unreadable."""
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as f:
            out = {}
            for line in f:
                k, sep, v = line.strip().partition("=")
                if sep:
                    out[k.strip().lower()] = v.strip()
            return out
    except OSError:
        return {}


def _theme_sig(path):
    """The signature values of the theme in `path` (BackPicFilename by file name only); None when there's no file."""
    a = _theme_keys(path)
    if not a:
        return None
    out = []
    for k in (x.lower() for x in THEME_SIGNATURE):
        v = a.get(k, "")
        out.append(re.split(r"[\\/]", v)[-1].lower() if k == "backpicfilename" else v)
    return out


def _theme_is_ours(path):
    """The theme in `path` is fl-headless's (same signature values; FL may reorder or add keys when it rewrites it).
    PC-VERIFY: FL's own __current.flstheme for our theme matches (FL may round or re-encode values)."""
    a = _theme_sig(path)
    return bool(a) and a == _theme_sig(_theme_src(THEME_FILES[0]))


# ----------------------------------------------------------------------------- the backup preset's thumbnail
def _png(rgb):
    """A minimal 8-bit RGB PNG (stdlib zlib) from an HxWx3 uint8 array."""
    import struct
    import zlib
    h, w = rgb.shape[:2]
    raw = b"".join(b"\x00" + rgb[y].tobytes() for y in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def _theme_rgb(keys, name, default):
    """A .flstheme color: negative numbers are 0xAARRGGBB, positive ones Windows COLORREF (0x00BBGGRR)."""
    try:
        n = int(float(keys.get(name.lower(), "")))
    except ValueError:
        return default
    if n < 0:
        n &= 0xFFFFFFFF
        return ((n >> 16) & 255, (n >> 8) & 255, n & 255)
    return (n & 255, (n >> 8) & 255, (n >> 16) & 255)


def _theme_base(keys):
    """FL's UI gray after the theme's Hue / Saturation / Lightness (an approximation for the swatch)."""
    import colorsys

    def num(k):
        try:
            return float(keys.get(k, 0) or 0)
        except ValueError:
            return 0.0
    light = min(0.85, max(0.04, 0.27 + num("lightness") / 256 * 0.25))
    sat = min(1.0, max(0.0, num("saturation") / 256 * 0.6))
    r, g, b = colorsys.hls_to_rgb((num("hue") % 360) / 360, light, sat)
    return (int(r * 255), int(g * 255), int(b * 255))


def theme_thumbnail(theme_path, w=300, h=180):
    """A swatch picture of a theme (PNG bytes, FL's thumbnail size): FL's window gray, the accent, step buttons,
    piano-roll notes and meters in the theme's own colors. A drawing, not a screenshot."""
    import numpy as np
    k = _theme_keys(theme_path)
    base = np.array(_theme_base(k), float)
    shade = lambda c, f: tuple(int(max(0, min(255, v * f))) for v in c)
    img = np.zeros((h, w, 3), np.uint8)
    img[:] = _theme_rgb(k, "BackColor", shade(base, 0.7))
    img[0:22] = shade(base, 1.35)                                        # the top bar
    text = _theme_rgb(k, "TextColor", _theme_rgb(k, "Selected", (215, 220, 225)))
    for i, x in enumerate((10, 40, 64, 100)):                            # 'words' on it
        img[8:13, x:x + (24 if i % 2 else 18)] = text
    img[22:25] = _theme_rgb(k, "Highlight", (230, 150, 60))              # the accent line
    img[34:172, 8:150] = base                                            # channel rack
    even, odd = _theme_rgb(k, "StepEven", shade(base, 0.8)), _theme_rgb(k, "StepOdd", shade(base, 1.2))
    lit = _theme_rgb(k, "Highlight", (230, 150, 60))
    for row in range(6):
        y = 42 + row * 21
        img[y:y + 14, 12:26] = shade(base, 1.5)                          # channel button
        for s in range(14):
            x = 30 + s * 8
            on = (s * (row + 3)) % 7 == 0
            img[y:y + 14, x:x + 7] = lit if on else (even if (s // 4) % 2 == 0 else odd)
    img[34:142, 158:292] = _theme_rgb(k, "PRGridback", shade(base, 0.6))  # piano roll
    grid = shade(_theme_rgb(k, "PRGridback", shade(base, 0.6)), 1.6)
    for x in range(158, 292, 17):
        img[34:142, x] = grid
    for i in range(7):                                                   # notes
        c = _theme_rgb(k, f"NoteColor{i % 3}", (120, 200, 140))
        y = 130 - ((i * 37) % 90); x = 162 + i * 18
        img[y:y + 7, x:x + 15 + (i % 3) * 8] = c
    meters = [_theme_rgb(k, f"Meter{i}", d) for i, d in enumerate(((60, 160, 90), (110, 180, 80), (170, 190, 70),
                                                                    (220, 190, 60), (230, 130, 60), (220, 70, 60)))]
    for x in range(158, 292):                                            # meter gradient
        f = (x - 158) / 134 * 5; i = min(4, int(f)); t = f - i
        img[150:166, x] = [int(meters[i][j] * (1 - t) + meters[i + 1][j] * t) for j in range(3)]
    return _png(img)


def _theme_save_thumb(folder, preset):
    """thm<preset name>.png next to a saved preset (only when it has none; never replaced)."""
    stem = preset[:-len(".flstheme")]
    dest = os.path.join(folder, "thm" + stem + ".png")
    if os.path.isfile(dest):
        return None
    try:
        _theme_write(dest, theme_thumbnail(os.path.join(folder, preset)))
        return os.path.basename(dest)
    except Exception:                                        # noqa: BLE001  (a swatch is a nicety, never a blocker)
        return None


def _theme_copy(folder, src_name, dest_name):
    src = os.path.join(folder, src_name)
    with open(src, "rb") as fh:
        _theme_write(os.path.join(folder, dest_name), fh.read())
    try:
        os.utime(os.path.join(folder, dest_name), (os.path.getatime(src), os.path.getmtime(src)))
    except OSError:
        pass


def _theme_migrate(folder, st):
    """The dev builds' hidden '__before fl-headless' backup becomes the visible preset (copied, the old file kept);
    every visible backup gets its thumbnail. -> [what was done]."""
    done = []
    if os.path.isfile(os.path.join(folder, THEME_LEGACY)) and not os.path.isfile(os.path.join(folder, THEME_BACKUP)):
        _theme_copy(folder, THEME_LEGACY, THEME_BACKUP)
        if st.get("backup_file") in (None, THEME_LEGACY):
            st["backup_file"] = THEME_BACKUP
        done.append(f"your theme from before is now the preset '{THEME_BACKUP[:-9]}' in Options > Theme")
    for b in _theme_backups(folder):
        if b != THEME_LEGACY:
            _theme_save_thumb(folder, b)
    return done


def _theme_backup(folder, st):
    """Save the user's current theme as a visible preset, once per distinct theme (never overwritten). Sets
    st['backup_file'] / st['before']. -> [what was done]."""
    cur = os.path.join(folder, THEME_CURRENT)
    if not os.path.isfile(cur):                              # FL never saved a theme: FL's default is in use
        st.setdefault("before", "default")
        return []
    if _theme_is_ours(cur):
        return []
    keys = _theme_keys(cur)
    for b in _theme_backups(folder):
        if b != THEME_LEGACY and _theme_keys(os.path.join(folder, b)) == keys:
            st["backup_file"] = b; st["before"] = "preset"
            return []
    name = THEME_BACKUP
    if os.path.isfile(os.path.join(folder, name)):
        name = f"{THEME_BACKUP[:-9]} ({time.strftime('%Y-%m-%d %H%M')}).flstheme"
        if os.path.isfile(os.path.join(folder, name)):
            return []
    _theme_copy(folder, THEME_CURRENT, name)
    # PC-VERIFY: FL's Options > Theme lists this preset by its file name, with thm<name>.png as its thumbnail
    thumb = _theme_save_thumb(folder, name)
    st["backup_file"] = name; st["before"] = "preset"
    return [f"saved your current theme as the preset '{name[:-9]}'" + (" (with a thumbnail)" if thumb else "")]


def _theme_back_line(th):
    """One line: how to switch back."""
    if th.get("backup_preset"):
        return f"To switch back: FL > Options > Theme > pick '{th['backup_preset']}'."
    if th.get("before") == "default":
        return "To switch back: FL > Options > Theme > pick FL's default theme."
    return "Any other theme: FL > Options > Theme."


def _start_hook():
    return isinstance(H.get("_BEFORE_FL_START"), list)


def _theme_before_start(*_a, **_k):
    """Runs right before fl-headless starts FL (server hook _BEFORE_FL_START): a switch that waited because FL was
    open happens now, unless the user picked a theme meanwhile. Never raises."""
    try:
        if (H["_state"]().get("theme") or {}).get("pending") and not _try(H["fl_running"], True):
            theme_setup(apply=True, auto=True)
    except Exception:                                        # noqa: BLE001
        pass


def _theme_status(folder=None):
    """Read only: {folder, why, installed (True / 'outdated' / False), active, backup, backup_preset, pending,
    user_choice, applied_by_setup}."""
    pick = folder or _theme_folder()
    st = H["_state"]().get("theme") or {}
    d = pick["path"]
    rep = {"folder": d, "why": pick["why"]}
    if pick.get("others"):
        rep["other_folders"] = pick["others"][:4]
    if not d:
        rep.update(installed=False, active=False)
        return rep
    hs = [(_sha(os.path.join(d, f)), _sha(_theme_src(f))) for f in THEME_FILES]
    rep["installed"] = True if all(a and a == b for a, b in hs) else ("outdated" if all(a for a, _ in hs) else False)
    rep["active"] = _theme_is_ours(os.path.join(d, THEME_CURRENT))
    bks = [b for b in _theme_backups(d) if b != THEME_LEGACY]
    b = st.get("backup_file") if st.get("backup_file") in bks else (bks[0] if bks else None)
    rep["backup"] = bool(b) or os.path.isfile(os.path.join(d, THEME_LEGACY))
    if b:
        rep["backup_preset"] = b[:-9]
    if st.get("before") == "default" and not b:
        rep["before"] = "default"
    if st.get("pending") and not rep["active"]:
        rep["pending"] = st["pending"].get("since", True)
    if not rep["active"] and not rep.get("pending") and (st.get("applied") or st.get("user_kept")):
        rep["user_choice"] = True                            # setup did its once; the user's pick stands
    if st.get("applied"):
        rep["applied_by_setup"] = st["applied"]
    return rep


def _theme_install(d):
    """Copy the three theme files into folder d (only those whose content differs). -> [file names written]."""
    wrote = []
    for f in THEME_FILES:
        src, dest = _theme_src(f), os.path.join(d, f)
        if _sha(src) is None:
            raise FileNotFoundError(f"fl-headless's theme file is missing: {src} (reinstall fl-headless)")
        if _sha(dest) != _sha(src):
            with open(src, "rb") as fh:
                _theme_write(dest, fh.read())
            wrote.append(f)
    return wrote


def theme_setup(apply=True, restore=False, auto=False):
    """Install fl-headless's FL theme and make it FL's theme (the user's current theme saved first as the visible
    preset 'My theme before fl-headless'), or restore the theme from before (FL closed).
    auto=True (setup): make it FL's theme only if setup never did - the user's later choice in Options > Theme
    always stands. FL open: files + preset installed, the switch waits (one click in FL, or the next FL start by
    fl-headless) unless the user picks another theme meanwhile. apply=False: files only."""
    t0 = time.time()
    if not _theme_supported():
        return {"done": [], "note": "FL themes are installed on Windows only" if not _win() else
                f"FL {_theme_fl_too_old()} has no themes (they came with FL Studio 21): nothing installed"}
    pick = _theme_folder(); d = pick["path"]
    st = dict(H["_state"]().get("theme") or {})
    st.pop("declined", None)                                 # (dev builds' opt-out: the look is part of setup now)
    done = []; notes = []
    if not d:
        return {"done": [], "folder": None, "note": "FL's user themes folder wasn't found: start FL once, then run "
                "flh_setup(action='apply')"}
    running = bool(_try(H["fl_running"], False))
    cur = os.path.join(d, THEME_CURRENT)
    now = time.strftime("%Y-%m-%d %H:%M")
    if restore:
        bak = next((b for b in [st.get("backup_file")] + _theme_backups(d) if b and os.path.isfile(os.path.join(d, b))),
                   None)
        if not bak:
            return {"done": [], "folder": d, "note": "no saved theme from before here" + (
                " (you were on FL's default theme)" if st.get("before") == "default" else "")
                + ": pick any theme in FL's Options > Theme"}
        if running:
            return {"done": [], "folder": d, "note": f"FL is open: pick '{bak[:-9]}' in Options > Theme (one click), "
                    "or close FL and run this again"}
        with open(os.path.join(d, bak), "rb") as fh:
            _theme_write(cur, fh.read())                     # the backup stays (never deleted)
        st.update(restored=now, written_hash=None, pending=None)
        H["_state"]({"theme": st})
        return {"done": [f"switched back to '{bak[:-9]}' (FL starts with it)"], "folder": d, "why": pick["why"],
                "steps_s": {"total": round(time.time() - t0, 3)}}
    wrote = _theme_install(d)
    done.append(("installed " if wrote else "theme files up to date: ") + ", ".join(wrote or ["no change"]) + f" in {d}")
    done += _theme_migrate(d, st)
    pend = st.get("pending")
    if not apply:
        H["_state"]({"theme": st})
        notes.append("not switched (files only): pick 'fl-headless' in FL's Options > Theme any time")
    elif _theme_is_ours(cur):
        st.setdefault("applied", now); st["pending"] = None; H["_state"]({"theme": st})
        done.append("fl-headless is FL's theme")
    elif auto and pend and _theme_sig(cur) != pend.get("sig"):
        st.update(pending=None, user_kept=now); H["_state"]({"theme": st})
        notes.append("you picked another theme in FL while the switch waited: kept ('fl-headless' stays in Options > "
                     "Theme)")
    elif auto and not pend and (st.get("applied") or st.get("user_kept")):
        notes.append("you picked another theme in FL after setup: kept ('fl-headless' stays in Options > Theme; "
                     "flh_setup(action='theme') switches back)")
    else:
        done += _theme_backup(d, st)
        if running:
            # PC-VERIFY: FL rewrites __current.flstheme itself, so a write while it runs would be lost - wait instead
            st["pending"] = {"since": now, "sig": _theme_sig(cur)}
            notes.append("FL is open: in FL click Options > Theme and pick 'fl-headless' (one click)" + (
                " - or it switches by itself the next time fl-headless starts FL" if _start_hook() else
                " - or close FL and run setup again"))
        else:
            with open(_theme_src(THEME_FILES[0]), "rb") as fh:
                data = fh.read()
            # PC-VERIFY: FL loads __current.flstheme at start, so FL opens with the fl-headless theme
            _theme_write(cur, data)
            import hashlib
            st.update(applied=now, folder=d, written_hash=hashlib.sha256(data).hexdigest(), pending=None,
                      user_kept=None)
            done.append("made fl-headless FL's theme (FL starts with it)")
        H["_state"]({"theme": st})
    rep = {"done": done, "notes": notes, "folder": d, "why": pick["why"],
           "steps_s": {"total": round(time.time() - t0, 3)}}
    th = _theme_status(pick)
    if apply and (th.get("active") or th.get("pending")):
        rep["switch_back"] = _theme_back_line(th)
    return rep


def flh_setup(action="check", roots=None, fl_edition=None, collection=None, finish=None, user_data=None, theme=None,
              apply=True, restore=False, restart_fl=None):
    if action == "check":
        return check()
    if action == "apply":
        return globals()["apply"](roots, fl_edition, collection, finish, user_data, theme, restart_fl)
    if action == "theme":
        return theme_setup(apply=bool(apply) if theme is None else bool(theme), restore=bool(restore))
    raise ValueError("action: check, apply or theme")


def register(ns):
    global H
    H = ns
    if not flh_store.H:
        flh_store.H = ns
    P, S = ns["_p"], ns["_schema"]
    hooks = ns.get("_BEFORE_FL_START")
    if isinstance(hooks, list):                              # a theme switch that waited for FL to close
        hooks[:] = [h for h in hooks if getattr(h, "__name__", "") != "_theme_before_start"] + [_theme_before_start]
    ns["TOOLS"]["flh_setup"] = (
        flh_setup, "First-run setup as one guided checklist. check (default): `steps` in order - FL Studio, song "
        "folders, connection to FL, FL edition, the fl-headless look, free extras, ready - each done / needs you / "
        "optional with ONE plain next action (`do` = the call, `auto` = apply does it); `next_steps` and the detail "
        "fields are for you. Show the steps to the user in plain words. 'Set me up' / 'set it up' (no song asked "
        "for) = check, then apply. apply: everything safe in one call - the FL edition the user names (never "
        "guess), user_data= when several exist, a neutral profile (-14 LUFS, -1 dBTP, 24-bit) unless finish={...}, "
        "FL's script (installed / updated), the live connection when Windows' MIDI loopback exists (FL open: ask "
        "the user, then restart_fl=true - FL is saved, closed, reopened once; `connection` says what's left), and "
        "fl-headless's FL theme made FL's theme once (the user's theme saved first as the preset 'My theme before "
        "fl-headless'). theme: install/switch (apply=false: files only), restore=true brings the old theme back "
        "(FL closed).",
        S(action=ns["_STR"]("check | apply | theme", True),
          roots=P(["string", "array"], "apply: folders you'd like writable (checked; set via the installer)", True,
                  items={"type": "string"}),
          fl_edition=ns["_STR"]("apply: fruity|producer|signature|all_plugins|unknown (ask)", True),
          collection=ns["_STR"]("apply: collection profile name (default: active, else 'My Songs')", True),
          finish=P("object", "apply: finals {lufs, true_peak, bits, fade_s}", True),
          user_data=ns["_STR"]("apply: FL's user data folder", True),
          apply=ns["_BOOL"]("theme: switch to it (default true; false = files only)", True),
          restore=ns["_BOOL"]("theme: bring back the theme from before", True),
          restart_fl=ns["_BOOL"]("apply: FL may close and reopen once to connect (ask the user first)", True)))
