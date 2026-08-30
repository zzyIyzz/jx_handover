# -*- coding: utf-8 -*-
"""渲染最终干净版 Word（V3）并输出核对摘要。"""
import json
import os
import urllib.request

BASE = "http://127.0.0.1:8765/api"
OUT = os.path.join(os.environ.get("TEMP", "."), "final_render.txt")


def call(method, path, body=None):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read().decode("utf-8"))


batches = call("GET", "/handovers")
bid = batches[0]["id"]
det = call("GET", f"/handovers/{bid}")
st = det["stations"][0]
r = call("POST", f"/handovers/{bid}/render",
         {"station_meta_id": st["station_meta_id"]})
lines = [f"最终版 V{r['version']}", r["docx_path"], r["current_path"]]
with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print("OK ->", OUT)
