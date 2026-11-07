"""Offline test: slow/failing Overpass servers must fail over inside the time budget."""
import time, requests, app as A

calls = []
class Resp:
    def __init__(s, code=200, js=None): s.status_code, s._j = code, js or {}
    def json(s): return s._j

def run(behaviours):
    calls.clear()
    it = iter(behaviours)
    def fake_post(url, data=None, headers=None, timeout=None):
        calls.append((url.split("/")[2], timeout))
        b = next(it)
        if b == "timeout": raise requests.exceptions.ReadTimeout()
        if isinstance(b, int): return Resp(b)
        return Resp(200, b)
    A.requests.post = fake_post
    try: return A.fetch_overpass("q")
    except RuntimeError as e: return str(e)

ok = {"elements": [{"type": "way", "id": 1}]}
print("1) 1st times out, 2nd ok     ->", run(["timeout", ok]), "| servers tried:", [c[0] for c in calls])
print("2) 406, then query-timeout remark, then ok ->", run([406, {"remark": "runtime error: Query timed out in \"query\""}, ok]) == ok, "| tried:", len(calls))
print("3) all three fail ->", run(["timeout", "timeout", "timeout"])[:95], "...")
pass
