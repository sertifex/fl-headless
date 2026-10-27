"""_expand_sample: FL's own path variables in saved projects resolve to real files - %FLStudioFactoryData% (the FL
install), %FLStudioUserData% (either level of the user data folder) and %FLStudioDownloadsData% (downloaded content
such as FLEX/Nebula packs, which FL rewrites our absolute paths into on save). Run: python3 tests/test_sample_vars.py"""
import os, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
tmp = tempfile.mkdtemp()
os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = os.path.join(tmp, "local"); os.environ["FLH_NO_WARM"] = "1"
import fl_headless_mcp as m  # noqa: E402

user = os.path.join(tmp, "Documents", "Image-Line", "FL Studio")
f1 = os.path.join(user, "Downloads", "Nebula", "Presets", "FLEX", "Pack", "Dusty.ogg")
f2 = os.path.join(tmp, "Documents", "Image-Line", "Downloads", "Other", "Hit.ogg")
for f in (f1, f2):
    os.makedirs(os.path.dirname(f), exist_ok=True); open(f, "wb").close()
m._fl_userdata = lambda *a, **k: user
song = os.path.join(tmp, "Song", "Song.flp")
q = m._expand_sample("%FLStudioDownloadsData%\\Nebula\\Presets\\FLEX\\Pack\\Dusty.ogg".replace("\\", os.sep), song)
assert os.path.isfile(q), q                                         # Downloads under the 'FL Studio' level
q = m._expand_sample("%FLStudioDownloadsData%\\Other\\Hit.ogg".replace("\\", os.sep), song)
assert os.path.isfile(q), q                                         # ... or under Image-Line
q = m._expand_sample("%FLStudioDownloadsData%\\Gone\\x.ogg".replace("\\", os.sep), song)
assert "%FLStudio" not in q and os.path.isabs(q), q                 # missing: a real path that doesn't exist, not
assert not os.path.exists(q)                                        # a variable glued to the song folder
print("sample vars ok")
