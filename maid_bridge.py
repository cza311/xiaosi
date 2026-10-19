# -*- coding: utf-8 -*-
"""桥接脚本：Trae 调小四的统一入口。
小四 HTTP API 在本地 8765，token 从 config.json 动态读取，不出现在 skill 文件里。

用法:
  python maid_bridge.py ping          # 测试小四是否在线
  python maid_bridge.py chat "主人今天好累啊"   # 对话
  python maid_bridge.py say zh="你好" ja="こんにちは"  # 直接开口
  python maid_bridge.py wake          # 唤醒
  python maid_bridge.py sleep         # 退下
  python maid_bridge.py display       # 切换立绘显示
  python maid_bridge.py pose [wave|spin]   # 做动作
  python maid_bridge.py status        # 状态
  python maid_bridge.py todos         # 看待办
  python maid_bridge.py log [n]       # 看最近日志（默认50行）

返回值全部是 stdout JSON，stderr 用于错误。
"""
import json
import os
import sys
import urllib.request
import urllib.parse

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG = json.load(open(os.path.join(BASE, "config.json"), encoding="utf-8"))
TOKEN = str(CONFIG.get("remote_token", "")).strip()
PORT = int(CONFIG.get("remote_port", 8765))
URL = f"http://127.0.0.1:{PORT}"

DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def api(path, method="GET", body=None, params=None):
    url = URL + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    headers = {"X-Token": TOKEN}
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with DIRECT.open(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    args = sys.argv[1:]
    if not args:
        print(json.dumps({"error": "no command"}, ensure_ascii=False))
        sys.exit(1)
    cmd = args[0]
    try:
        if cmd == "ping":
            print(json.dumps(api("/api/ping"), ensure_ascii=False))
        elif cmd == "chat":
            text = args[1] if len(args) > 1 else ""
            print(json.dumps(api("/api/chat", "POST", {"text": text}),
                             ensure_ascii=False))
        elif cmd == "say":
            body = {}
            for a in args[1:]:
                if "=" in a:
                    k, v = a.split("=", 1)
                    body[k] = v
            print(json.dumps(api("/api/say", "POST", body), ensure_ascii=False))
        elif cmd == "wake":
            print(json.dumps(api("/api/wake", "POST", {}), ensure_ascii=False))
        elif cmd == "sleep":
            print(json.dumps(api("/api/sleep", "POST", {}), ensure_ascii=False))
        elif cmd == "display":
            print(json.dumps(api("/api/display", "POST", {}), ensure_ascii=False))
        elif cmd == "pose":
            key = args[1] if len(args) > 1 else "wave"
            print(json.dumps(api("/api/pose", "POST", {"key": key}),
                             ensure_ascii=False))
        elif cmd == "status":
            print(json.dumps(api("/api/status"), ensure_ascii=False))
        elif cmd == "todos":
            print(json.dumps(api("/api/todos"), ensure_ascii=False))
        elif cmd == "log":
            n = int(args[1]) if len(args) > 1 else 50
            print(json.dumps(api("/api/log", params={"n": n}), ensure_ascii=False))
        else:
            print(json.dumps({"error": f"unknown command: {cmd}"},
                             ensure_ascii=False))
            sys.exit(1)
    except Exception as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
