# -*- coding: utf-8 -*-
"""收尾：恢复演示优先级 + 验证人员字典与定期工作模板库接口。"""
import json
import os
import urllib.request

BASE = "http://127.0.0.1:8080/api"
OUT = os.path.join(os.environ.get("TEMP", "."), "final_check.txt")
L = []


def call(method, path, body=None):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    # 1. 恢复演示事项为普通优先级
    batches = call("GET", "/handovers")
    bid = batches[0]["id"]
    det = call("GET", f"/handovers/{bid}")
    st = det["stations"][0]
    for it in st["items"]:
        if it["priority"] in ("urgent", "important"):
            call("PATCH", f"/handover-items/{it['id']}",
                 {"revision": it["revision"], "priority": "normal"})
            L.append(f"已恢复普通: {it['title'][:20]}")

    # 2. 人员字典接口
    staff = call("GET", "/staff")
    L.append(f"\n人员字典 {len(staff)} 人（片区通用+场站）:")
    for s in staff[:6]:
        L.append(f"  {s['name']} | {s['role']} | {s['station_code']}")
    xs_staff = call("GET", "/staff?station_code=XS_MMS")
    L.append(f"按场站筛选 XS_MMS: {len(xs_staff)} 人")

    # 3. 定期工作模板库接口
    lib = call("GET", "/periodic/library")
    L.append(f"\n模板库规模: {lib['summary']}")
    L.append(f"总项数: {len(lib['items'])}")
    sample = next(i for i in lib["items"] if i["library_id"] == "m22")
    L.append(f"示例 m22: {sample['name']} | 周期={sample['schedule']} | "
             f"责任人={sample['owner']}")
    L.append(f"  资料清单={sample['doc_list'][:30]}")
    L.append(f"  留存目录={sample['doc_dir'][:30]}")
    L.append(f"  内容要求={sample['content'][:40]}")

    # 4. 场站列表（片区化）
    stations = call("GET", "/stations")
    L.append(f"\n场站 {len(stations)} 个（共用模板，station_code 区分）:")
    for s in stations:
        L.append(f"  {s['code']} | {s['name']}")

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print("OK ->", OUT)


if __name__ == "__main__":
    main()
