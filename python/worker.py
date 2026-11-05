# -*- coding: utf-8 -*-
"""干什么：常驻出图 worker —— 模型只加载一次，之后每个请求只算采样。

为什么要有它：draw.py 是「每次新起一个进程」，每张图都要重新把 6.9G 底模读进显存，
一张要 60~90 秒。这个 worker 挂在后台，模型一直在显存里，之后每张只要 8~30 秒。

协议（一行一个 JSON，stdin 进、stdout 出）：
  请求 {"action":"laughing","count":1,"size":832,"out":"目录","text":"台词","dim":0.96,"seed":null,"base":"底图"}
  响应 {"ok":true,"images":["绝对路径"],"log":"..."}  或  {"ok":false,"error":"..."}
  退出 {"cmd":"quit"}，或者 stdin 关掉（宿主进程没了）自动退。

日志：所有出图日志塞在响应的 log 字段里，不往 stdout 乱打（stdout 是协议通道）。
怎么撤：宿主不启动它就不会跑；手动退出就杀进程。
"""
import contextlib
import io
import json
import os
import sys
import traceback

这里 = os.path.dirname(os.path.abspath(__file__))
if 这里 not in sys.path:
    sys.path.insert(0, 这里)

import draw  # noqa: E402

# Windows 下 sys.stdin/stdout 默认是 GBK，中文台词会变成「鐀夏墠涓嶆檽」这种乱码。
# 协议这边统一按 UTF-8 收发，和宿主写的 JSON 对齐。
try:
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def 回(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def 处理(请求):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = draw.出图(
            str(请求.get("action") or ""),
            max(1, min(5, int(请求.get("count") or 1))),
            int(请求.get("size") or 832),
            str(请求.get("out") or "."),
            float(请求.get("dim") or 0.96),
            str(请求.get("text") or ""),
            请求.get("seed"),
            (str(请求.get("base") or "") or None),
        )
    输出 = buf.getvalue()
    图 = [l[4:].strip() for l in 输出.splitlines() if l.startswith("IMG:")]
    return {"ok": rc == 0 and bool(图), "images": 图, "log": 输出[-1500:]}


def main():
    回({"ready": True})
    for 行 in sys.stdin:
        行 = 行.strip()
        if not 行:
            continue
        try:
            请求 = json.loads(行)
        except Exception as e:
            回({"ok": False, "error": "请求不是合法 JSON：%s" % e})
            continue
        if 请求.get("cmd") == "quit":
            回({"ok": True, "bye": True})
            return 0
        try:
            回(处理(请求))
        except Exception as e:
            回({"ok": False, "error": "%s\n%s" % (e, traceback.format_exc()[-900:])})
    return 0


if __name__ == "__main__":
    sys.exit(main())
