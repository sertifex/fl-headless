"""FL's 'save changes?' (TMsgForm 'Confirm') is answered by key per policy: No after a file edit, never Escaped.
After Ctrl+O only FL's Open dialog is used: a never-saved project's Save As (or any other box) gets Esc and a refusal,
so a path is never typed into Save As."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import fl_headless_mcp as m

pressed, dialogs = [], [{"hwnd": 7, "title": "Confirm", "class": "TMsgForm", "kind": "save_changes", "texts": ["Confirm"]}]
m.fl_dialogs = lambda: list(dialogs)
def fake_dialog(action, button, hwnd=None):
    assert button.lower() != "escape", "a save question must never be escaped"
    pressed.append(button); dialogs.clear(); return {"clicked": button}
m.fl_dialog = fake_dialog
m._fl_main_window = lambda: (1, "Song - FL Studio 2026")
m.flp_project_info = lambda p: {}
song = os.path.join(os.sep, "x", "Song.flp")
assert m._answer_save_prompts("no") == [] and pressed == []        # no project named: never answers No
r = m._answer_save_prompts("no", [song])
assert pressed == ["No"] and r[0]["answered"] == "no", (pressed, r)
dialogs.append({"hwnd": 8, "title": "Confirm", "class": "TMsgForm", "kind": "save_changes", "texts": []})
assert m._answer_save_prompts("yes")[0]["answered"] == "yes" and pressed[-1] == "Yes"
assert m._answer_save_prompts("no", [song]) == []            # nothing open, nothing pressed
# another project is open in FL: No is never given for it
dialogs.append({"hwnd": 9, "title": "Confirm", "class": "TMsgForm", "kind": "save_changes", "texts": []})
m._fl_main_window = lambda: (1, "Song 2 - FL Studio 2026")
assert m._answer_save_prompts("no", [song]) == [] and pressed[-1] == "Yes"
# a demo "Save anyway" box is never answered
dialogs[:] = [{"hwnd": 10, "title": "Demo", "class": "TMsgForm", "kind": "other", "text": ["Save anyway?"]}]
assert m._is_save_anyway(dialogs[0]) and m._answer_save_prompts("yes") == []
print("save prompts ok")

# ---- after Ctrl+O: only the Open dialog is used (fl_open with a modified, never-saved project in FL)
m.time.sleep = lambda s_: None
esc = []


def press_vk(hwnd, vk, wait_s=1.5):
    esc.append(hwnd); dialogs[:] = [d for d in dialogs if d["hwnd"] != hwnd]; return "key"


m._press_vk = press_vk
after_yes = []


def click(action, button, hwnd=None):
    pressed.append(button)
    dialogs[:] = [d for d in dialogs if d["hwnd"] != hwnd] + list(after_yes if button == "Yes" else [])
    return {"clicked": button}


m.fl_dialog = click
SAVE_Q = {"hwnd": 20, "title": "Confirm", "class": "TMsgForm", "kind": "save_changes", "text": []}
SAVE_AS = {"hwnd": 21, "title": "Save As", "class": "#32770", "kind": "save_as", "file_dialog": True, "text": []}
OPEN = {"hwnd": 22, "title": "Open", "class": "#32770", "kind": "open_dialog", "file_dialog": True, "text": []}
target = os.path.join(os.sep, "x", "Target.flp")


def ctrl_o(first, then=(), title="Song - FL Studio 2026", on_save="yes"):
    m._fl_main_window = lambda: (1, title)
    dialogs[:] = [dict(first)] if first else []; after_yes[:] = [dict(t) for t in then]
    pressed.clear(); esc.clear(); m._OPEN_REFUSAL["why"] = None
    return m._await_open_dialog(set(), target, timeout_s=0.5, on_save=on_save)


# 1. a never-saved 'Project 3' with changes: Yes would bring up Save As -> Cancel, refused, nothing typed anywhere
got = ctrl_o(SAVE_Q, [SAVE_AS], title="Project 3 - FL Studio 2026")
assert got is None and pressed == [] and esc == [20], (got, pressed, esc)
assert m._OPEN_REFUSAL["why"].startswith("FL has an unsaved new project open: save it or close it in FL first")
dialogs[:] = [dict(SAVE_Q)]; pressed.clear()
assert m._answer_save_prompts("yes") == [] and pressed == [], pressed      # the retry path never says Yes either
# 2. the untitled name isn't recognized (another UI language): Yes answered, Save As appears -> Esc, refused
got = ctrl_o(SAVE_Q, [SAVE_AS], title="Neues Lied 3 - FL Studio 2026")
assert got is None and pressed == ["Yes"] and esc == [21], (got, pressed, esc)
assert "unsaved new project" in m._OPEN_REFUSAL["why"]
# ... also when FL's Save As has a title in a language the word lists don't know (file dialog after a Yes)
got = ctrl_o(SAVE_Q, [dict(SAVE_AS, title="Zapisz plik")], title="Neues Lied 3 - FL Studio 2026")
assert got is None and esc == [21] and "unsaved new project" in m._OPEN_REFUSAL["why"], (got, esc)
# 3. a saved project with changes: Yes, then FL's Open dialog is the one used
got = ctrl_o(SAVE_Q, [OPEN])
assert got and got["hwnd"] == 22 and pressed == ["Yes"] and not esc, (got, pressed, esc)
# 4. no question: the Open dialog straight away; a file dialog in an unknown language counts as Open then
assert ctrl_o(OPEN)["hwnd"] == 22
got = ctrl_o(dict(SAVE_AS, title="Avaa"))
assert got and got["hwnd"] == 21 and not esc, (got, esc)
# ... but a dialog titled Save As is never taken for Open
got = ctrl_o(SAVE_AS)
assert got is None and esc == [21] and "unsaved new project" in m._OPEN_REFUSAL["why"]
# 5. any other new box: Esc and a refusal naming it
got = ctrl_o({"hwnd": 23, "title": "Warning", "class": "#32770", "kind": "other", "text": []})
assert got is None and esc == [23] and "'Warning'" in m._OPEN_REFUSAL["why"], m._OPEN_REFUSAL
# 6. on_save='no' for another project: Cancel, refused (unchanged behaviour)
got = ctrl_o(SAVE_Q, [OPEN], title="Other - FL Studio 2026", on_save="no")
assert got is None and esc == [20] and "other than" in m._OPEN_REFUSAL["why"]
# 7. PC 2026-10-23: Ctrl+O that reached Edison's window opens ITS 'Load sample' dialog: told apart from FL's Open
_fdi = m._file_dialog_info
m._file_dialog_info = lambda h: {"edits": [], "combos": ["All supported files (*.wav;*.mp3;*.flac;*.ogg)"]} if h == 31 \
    else {"edits": [], "combos": ["FL Studio project (*.flp)"]}
assert m._audio_open_dialog({"hwnd": 31, "title": "Open", "kind": "open_dialog", "file_dialog": True})
assert not m._audio_open_dialog({"hwnd": 32, "title": "Open", "kind": "open_dialog", "file_dialog": True})
assert not m._audio_open_dialog({"hwnd": 33, "title": "Warning", "kind": "other"})
m._file_dialog_info = _fdi
assert m._focus_main_keys(1) is False or os.name == "nt"           # a no-op off Windows
print("ok")
