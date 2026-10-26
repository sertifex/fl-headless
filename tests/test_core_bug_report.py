"""flh_bug_report: tool errors land in a ring buffer from the dispatcher (also inside flh_run, and after flh_reload),
the zip holds versions/setup/errors/timings/extensions with user names scrubbed and nothing else.
Run: python3 tests/test_core_bug_report.py"""
import io, json, os, sys, tempfile, zipfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
tmp = tempfile.mkdtemp()
os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = os.path.join(tmp, "local")
os.environ["USERNAME"] = "Zoltan.Kovacs"
import fl_headless_mcp as m  # noqa: E402

secret_paths = [r"C:\Users\Zoltan.Kovacs\Music\Secret Song.flp", "/home/zoltan.kovacs/x.flp", "zoltan.kovacs@mail.example"]
m.TOOLS["zz_boom"] = (lambda path=None: (_ for _ in ()).throw(FileNotFoundError(path)), "", {})
for p in secret_paths:
    out = m.handle({"method": "tools/call", "params": {"name": "zz_boom", "arguments": {"path": p}}})
    assert out["isError"]
m.flh_run([{"tool": "zz_boom", "args": {"path": secret_paths[0]}}])
assert len(m._ERRORS) == 4 and m._ERRORS[-1]["tool"] == "flh_run:zz_boom" and "Traceback" in m._ERRORS[0]["traceback"]
assert m._scrub(r"C:\Users\Zoltan.Kovacs\Music") == r"C:\Users\<user>\Music"
assert m._scrub("/home/someone/a b/c.flp") == "/home/<user>/a b/c.flp"
assert m._scrub("mail me: a.b@c.org") == "mail me: <email>"
# names with spaces, apostrophes, any script; the profile / OneDrive folders (organisation names) as a whole
for raw, want in ((r"C:\Users\John Smith\Music\a.flp", r"C:\Users\<user>\Music\a.flp"),
                  (r"C:\Users\José Ñúñez\x", r"C:\Users\<user>\x"), ("C:/Users/李雷/x.flp", "C:/Users/<user>/x.flp"),
                  ('File "C:\\Users\\O\'Brien\\x.py", line 3', 'File "C:\\Users\\<user>\\x.py", line 3'),
                  ("'C:\\Users\\Bob', 'x'", "'C:\\Users\\<user>', 'x'"),
                  ("first.last+fl@mail.co.uk wrote", "<email> wrote")):
    assert m._scrub(raw) == want, (raw, m._scrub(raw), want)
_env = dict(os.environ)
os.environ.update(USERPROFILE=r"C:\Users\Ann Lee", OneDrive=r"C:\Users\Ann Lee\OneDrive - Contoso Ltd", USERNAME="Ann Lee")
got = m._scrub(r"C:\Users\Ann Lee\OneDrive - Contoso Ltd\Beats\x.flp; 'C:\\Users\\Ann Lee\\Music'")
assert "Ann" not in got and "Contoso" not in got and got.startswith("<user>\\Beats"), got
os.environ.clear(); os.environ.update(_env)

r = m.flh_bug_report(errors=3)
assert os.path.isfile(r["zip"]) and r["zip"].startswith(os.path.join(tmp, "local", "fl-headless", "bug_reports")), r
assert r["errors_included"] == 3 and set(r["contains"]) == {"report.json", "setup_check.json", "errors.json",
                                                             "timings.json", "extensions.json", "state.json",
                                                             "usage.json"}, r
z = zipfile.ZipFile(r["zip"]); blob = "".join(z.read(n).decode() for n in z.namelist())
assert "zoltan" not in blob.lower() and "Secret Song" in blob and "<user>" in blob and "<email>" in blob
rep = json.loads(z.read("report.json"))
assert rep["fl_headless"] == m.SERVER["version"] and rep["python"] and "os" in rep, rep
assert isinstance(json.loads(z.read("errors.json")), list)
for _ in range(12):
    m.flh_bug_report()
assert len(os.listdir(os.path.dirname(r["zip"]))) <= 10                       # only the newest 10 kept

# after flh_reload the live copy records into the same buffer
n0 = len(m._ERRORS)
m.flh_reload()
L = sys._flh_live
L["TOOLS"]["zz_boom"] = m.TOOLS["zz_boom"]
m.handle({"method": "tools/call", "params": {"name": "zz_boom", "arguments": {"path": "x"}}})
assert L["_ERRORS"] is m._ERRORS and len(m._ERRORS) == n0 + 1
print("ok")
