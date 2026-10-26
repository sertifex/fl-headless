"""The weekly 'is a newer fl-headless out?' look: cached, never raises, off switch, extension vs ZIP wording.
Run: python3 tests/test_updates.py (no network: the HTTP call is replaced)."""
import os, shutil, sys, tempfile, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
tmp = tempfile.mkdtemp(prefix="flh_upd_")
os.environ["FLH_ROOTS"] = tmp; os.environ["LOCALAPPDATA"] = os.path.join(tmp, "local")
os.environ.pop("FLH_UPDATE_CHECK", None); os.environ.pop("FLH_INSTALLED_BY", None)
import atexit; atexit.register(shutil.rmtree, tmp, True)  # noqa: E702
import fl_headless_mcp as m  # noqa: E402

calls = []
reply = {"tag_name": "v9.0.0", "html_url": "https://github.com/x/y/releases/tag/v9.0.0", "name": "big one"}
def fake_http(url, timeout):
    calls.append(url)
    if isinstance(reply, Exception):
        raise reply
    return reply
m._http_json = fake_http

# a newer release: one network look, then cached for a week
r = m._release_update()
assert r and r["version"] == "9.0.0" and r["installed"] == m.SERVER["version"] and "ZIP" in r["how"], r
assert len(calls) == 1 and calls[0] == m.UPDATE_URL, calls
assert m._release_update() and len(calls) == 1, "should use the weekly cache"
assert "fl_headless_update" in m.fl_status(), "fl_status should mention the new version"
assert len(calls) == 1

# the extension gets extension wording
os.environ["FLH_INSTALLED_BY"] = "mcpb"
assert ".mcpb" in m._release_update()["how"]
os.environ.pop("FLH_INSTALLED_BY")

# same or older version: nothing to say
reply = {"tag_name": "v" + m.SERVER["version"], "html_url": "u"}
assert m.flh_updates(check_now=True)["update"] == "you have the latest version"
reply = {"tag_name": "v0.9.0", "html_url": "u"}
assert m._release_update(force=True) is None

# offline: no exception, retried after a day (not a week), and the tool says so
reply = OSError("offline")
n = len(calls)
assert m._release_update(force=True) is None and len(calls) == n + 1
st = m._state()["update_seen"]; assert st["ok"] is False
m._state({"update_seen": dict(st, checked=time.time() - m.UPDATE_RETRY_S - 5)})
m._release_update(); assert len(calls) == n + 2, "a failed look is retried after a day"
assert "couldn't reach" in m.flh_updates()["note"]

# off switches: the tool and the env var; no network at all when off
n = len(calls)
r = m.flh_updates(enable=False)
assert r["weekly_check"] is False and m._release_update(force=True) is None and len(calls) == n
assert "fl_headless_update" not in m.fl_status()
m.flh_updates(enable=True)
os.environ["FLH_UPDATE_CHECK"] = "0"
assert m._release_update(force=True) is None and len(calls) == n
os.environ.pop("FLH_UPDATE_CHECK")

# a broken reply never breaks the caller
reply = {"tag_name": None}
assert m._release_update(force=True) is None
assert m._version_key("v2.10.1") > m._version_key("2.9.9") > m._version_key("junk")
assert "flh_updates" in m.TOOLS
print("ok")
