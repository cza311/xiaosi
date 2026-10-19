# -*- coding: utf-8 -*-
"""小四 · 远程控制服务端：局域网 HTTP API + 手机 PWA 控制台
仅用标准库，token 鉴权，手机浏览器打开 http://电脑IP:8765 即可使用。
"""
import base64
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
UI_DIR = os.path.join(BASE, "remote_ui")
LOG_FILE = os.path.join(BASE, "maid_log.txt")
CONFIG_FILE = os.path.join(BASE, "config.json")

# 允许手机直接执行的动作白名单（run_ps 走聊天通道以便隔离确认，不在此列）
SAFE_ACTIONS = {
    "time", "weather", "open", "search", "list_todo", "add_todo",
    "done_todo", "del_todo", "add_schedule", "clean_desktop",
    "write_note", "close_app",
}

# 允许手机修改的配置键 -> 类型
CONFIG_KEYS = {
    "master_name": str, "city": str, "mic_keyword": str,
    "api_key": str, "api_base": str, "model": str,
    "tts_voice": str, "tts_rate": str, "tts_pitch": str,
    "wake_words": list, "session_timeout": int,
    "morning_greeting": str, "night_greeting": str,
    "greetings_enabled": bool,
    "remote_port": int, "remote_token": str,
}


def _log(*args):
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(time.strftime("[%H:%M:%S] ") + " ".join(str(a) for a in args) + "\n")
    except OSError:
        pass


def _lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


class MaidRemote:
    def __init__(self, app):
        self.app = app
        self.started = time.time()
        cfg = app.cfg
        self.port = int(cfg.get("remote_port", 8765))
        self.token = str(cfg.get("remote_token") or "").strip()
        if not self.token:
            self.token = secrets.token_hex(4)
            cfg["remote_token"] = self.token
            self._save_cfg(cfg)
        self.httpd = None

    @staticmethod
    def _save_cfg(cfg):
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except OSError as e:
            _log("remote: save config failed:", e)

    def start(self):
        handler = _make_handler(self)
        self.app._voice_lock = threading.Lock()  # 语音识别串行化
        threading.Thread(target=_spawn_voice_worker, args=(self.app,),
                         daemon=True).start()  # 预热识别进程
        # 便携多开：8765 被占就依次向后找空位（最多 10 只），实际端口写回 config
        # （maid_bridge / maid_chat 每次从本目录 config.json 读端口，天然跟手）
        httpd = None
        first = self.port

        class _QuietServer(ThreadingHTTPServer):
            """客户端主动断开（手机切网/关页面）不是故障——
            不再让 ConnectionResetError 刷满 _boot_err.log"""
            def handle_error(self, request, client_address):
                import sys
                exc = sys.exc_info()[1]
                if isinstance(exc, (ConnectionResetError, ConnectionAbortedError,
                                    BrokenPipeError, TimeoutError)):
                    return
                super().handle_error(request, client_address)

        for p in range(first, first + 10):
            try:
                httpd = _QuietServer(("0.0.0.0", p), handler)
                self.port = p
                break
            except OSError:
                continue
        if httpd is None:
            _log("remote: bind %d-%d all occupied, give up" % (first, first + 9))
            return
        self.httpd = httpd
        if self.port != first:  # 端口挪了：写回 config，让本实例的桥接/聊天窗找得到
            self.app.cfg["remote_port"] = self.port
            self._save_cfg(self.app.cfg)
        t = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        t.start()
        _log("remote: serving at http://%s:%d  (token=%s)" % (_lan_ip(), self.port, self.token))
        # 尝试放行防火墙（无管理员权限则失败，仅提示）
        try:
            subprocess.Popen(
                ["netsh", "advfirewall", "firewall", "add", "rule",
                 "name=MaidRemote", "dir=in", "action=allow",
                 "protocol=TCP", "localport=%d" % self.port],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

    def stop(self):
        if self.httpd:
            try:
                self.httpd.shutdown()
            except Exception:
                pass


def _wrap(fn):
    """路由异常必须留痕，pyw 下 stderr 不可见"""
    def inner(self, *a, **kw):
        try:
            return fn(self, *a, **kw)
        except Exception:
            import traceback
            _log("remote handler error:", traceback.format_exc())
            try:
                self._json(500, {"error": "internal error"})
            except Exception:
                pass
    return inner


def _spawn_voice_worker(app):
    """启动识别工作进程（独立进程 HTTP 服务）；成功挂到 app，返回 True"""
    cand = os.path.join(sys.base_prefix or "", "python.exe")
    exe = cand if os.path.exists(cand) else sys.executable
    w = subprocess.Popen(
        [exe, "-u", os.path.join(BASE, "voice_worker.py")],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, cwd=BASE,
        text=True, encoding="utf-8")
    ready = w.stdout.readline()
    info = json.loads(ready) if ready.strip() else {}
    if info.get("type") != "ready":
        _log("remote: voice worker failed:", info)
        try:
            w.kill()
        except Exception:
            pass
        return False
    app._voice_worker = w
    app._voice_port = info.get("port")
    _log("remote: voice worker up (port %s)" % info.get("port"))
    return True


def _make_handler(remote):
    app = remote.app

    class Handler(BaseHTTPRequestHandler):
        server_version = "MaidRemote/1.0"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            _log("remote:", fmt % args)

        # ---------- 基础 ----------
        def _json(self, code, obj):
            data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _authed(self):
            cur = str(app.cfg.get("remote_token") or remote.token)  # 实时读取，改了立即生效
            q = self.path.split("?", 1)
            if "token=" in (q[1] if len(q) > 1 else ""):
                from urllib.parse import urlparse, parse_qs
                qs = parse_qs(urlparse(self.path).query)
                if qs.get("token", [""])[0] == cur:
                    return True
            return self.headers.get("X-Token", "") == cur

        def _read_chunked(self, n_limit=3_000_000):
            """RFC7230 chunked 传输编码解析（反代如 tailscale serve 会改写成分块）"""
            data = b""
            try:
                while len(data) <= n_limit:
                    line = self.rfile.readline(65536).strip()
                    if b";" in line:
                        line = line.split(b";")[0]
                    try:
                        size = int(line, 16)
                    except ValueError:
                        break
                    if size == 0:
                        self.rfile.readline()
                        break
                    data += self.rfile.read(size)
                    self.rfile.read(2)  # 块尾 CRLF
            except (OSError, ValueError):
                pass
            return data

        def _raw_body(self):
            try:
                te = (self.headers.get("Transfer-Encoding") or "").lower()
                if "chunked" in te:
                    return self._read_chunked()
                n = int(self.headers.get("Content-Length", 0))
                return self.rfile.read(n) if n > 0 else b""
            except (ValueError, OSError):
                return b""

        def _body(self):
            raw = self._raw_body()
            try:
                return json.loads(raw.decode("utf-8"))
            except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
                return {}

        # ---------- 对话与语音 ----------
        def _chat(self, text):
            """远程对话统一入口：大脑 + 桌面同步显示朗读"""
            _log("remote: chat <<<", text[:40])
            app.avatar.say("……", thinking=True)
            try:  # 与语音路径一致：思考中 → 回完回 idle（/api/state 不再恒 idle）
                import maid_status
                maid_status.set_status("thinking")
            except Exception:
                pass
            try:
                resp = app.brain.chat(text)
            except Exception as e:
                _log("remote: chat error:", repr(e))
                try:
                    import maid_status
                    maid_status.set_status("error")
                except Exception:
                    pass
                raise
            try:  # 仅思考中才回空闲：期间开工的动作状态（singing/working）不能被覆盖
                import maid_status
                maid_status.clear_if("thinking")
            except Exception:
                pass
            try:  # 文字指令同样触发自我学习与技能沉淀
                app.after_chat(text)
            except Exception:
                pass
            app.events.put(("remote_say", resp))
            _log("remote: chat >>>", str(resp.get("zh", ""))[:40])
            out = dict(resp)  # 手机端展示用拼接文本（桌面端保留分段逐段播）
            if isinstance(out.get("zh"), list):
                out["zh"] = " ".join(str(s) for s in out["zh"] if str(s).strip())
            if isinstance(out.get("ja"), list):
                out["ja"] = " ".join(str(s) for s in out["ja"] if str(s).strip())
            return out

        def _voice_worker(self):
            """取识别工作进程；死了或没起就用模块级函数重新拉起"""
            w = getattr(app, "_voice_worker", None)
            if w is not None and w.poll() is not None:
                w = None
            return w or _spawn_voice_worker(app)

        def _recognize_wav(self, data):
            """WAV 字节 -> 识别文本；经本机 HTTP 调用工作进程"""
            if len(data) > 3_000_000:
                return None, "录音太长啦，限 90 秒"
            port = getattr(app, "_voice_port", None)
            alive = getattr(app, "_voice_worker", None)
            if not port or (alive and alive.poll() is not None) or not alive:
                if not _spawn_voice_worker(app):
                    return None, "识别进程没起来"
                port = app._voice_port
            try:
                req = urllib.request.Request(
                    "http://127.0.0.1:%s/rec" % port, data=data,
                    headers={"Content-Type": "audio/wav"}, method="POST")
                with urllib.request.urlopen(req, timeout=30) as r:
                    out = json.loads(r.read().decode("utf-8"))
            except Exception as e:
                _log("remote: voice worker io error:", repr(e))
                app._voice_worker = None
                return None, "识别进程异常"
            if out.get("error"):
                return None, "识别出错"
            return out.get("text", ""), None

        def _file(self, fpath, ctype, cache=None):
            try:
                with open(fpath, "rb") as f:
                    data = f.read()
            except OSError:
                self._json(404, {"error": "not found"})
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control",
                             "max-age=%d" % cache if cache else "no-store")
            self.end_headers()
            self.wfile.write(data)

        # ---------- 路由 ----------
        def _do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/" or path == "/index.html":
                return self._file(os.path.join(UI_DIR, "index.html"),
                                  "text/html; charset=utf-8")
            # PWA 静态资源（免 token：清单/图标/Worker 需要裸抓取，均不含敏感信息）
            if path == "/manifest.json":
                return self._file(os.path.join(UI_DIR, "manifest.json"),
                                  "application/manifest+json", cache=3600)
            if path == "/sw.js":
                return self._file(os.path.join(UI_DIR, "sw.js"),
                                  "application/javascript", cache=0)
            if path == "/avatar.png":
                return self._file(os.path.join(BASE, "avatar.png"),
                                  "image/png", cache=86400)
            m = re.fullmatch(r"/icons/([A-Za-z0-9_.-]+\.png)", path)
            if m and os.path.isdir(os.path.join(UI_DIR, "icons")):
                return self._file(os.path.join(UI_DIR, "icons", m.group(1)),
                                  "image/png", cache=86400)
            if not self._authed():
                return self._json(401, {"error": "unauthorized"})
            if path == "/api/ping":
                return self._json(200, {"ok": True, "name": "小四",
                                        "master": app.cfg.get("master_name", "主人")})
            if path == "/api/status":
                return self._json(200, self._status())
            if path == "/api/todos":
                from urllib.parse import urlparse, parse_qs
                qbody = {k: v[-1] for k, v in
                         parse_qs(urlparse(self.path).query,
                                  keep_blank_values=True).items()
                         if k != "token"}
                if qbody:  # GET+query 带内容=添加待办（手机端 GET 通道）
                    return self._api(path, qbody)
                return self._json(200, {"todos": app.tasks.list_todos()})
            if path == "/api/config":
                from urllib.parse import urlparse, parse_qs
                qbody = {k: v[-1] for k, v in
                         parse_qs(urlparse(self.path).query,
                                  keep_blank_values=True).items()
                         if k != "token"}
                if qbody:  # GET+query 带设置项=保存（此前被读取分支遮蔽→手机设置存不上）
                    return self._save_config(qbody)
                cfg = dict(app.cfg)
                cfg["api_key"] = "••••••••" if cfg.get("api_key") else ""
                return self._json(200, {"config": cfg})
            if path == "/api/log":
                from urllib.parse import urlparse, parse_qs
                qs = parse_qs(urlparse(self.path).query)
                n = min(int(qs.get("n", ["120"])[0] or 120), 500)
                return self._json(200, {"log": self._tail(n)})
            if path == "/api/cnlog":
                # 中文运行日志（D:\小四\日志\运行日志_中文_今天.txt）
                from urllib.parse import urlparse, parse_qs
                import time as _t
                qs = parse_qs(urlparse(self.path).query)
                n = min(int(qs.get("n", ["200"])[0] or 200), 1000)
                fp = os.path.join(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "小四", "日志", "运行日志_中文_%s.txt" % _t.strftime("%Y-%m-%d"))
                lines = []
                try:
                    with open(fp, encoding="utf-8") as f:
                        lines = f.read().splitlines()
                except OSError:
                    pass
                return self._json(200, {"log": lines[-n:]})
            if path == "/api/state":
                # W1：全局状态机当前状态（idle/thinking/working/waiting/error/done/singing）
                try:
                    import maid_status
                    st = maid_status.get_status()
                except Exception:
                    st = {"status": "unknown", "detail": "", "ts": 0}
                return self._json(200, st)
            if path == "/api/selftest":
                """进程内回环自测：绕开外部环境干扰"""
                import io
                import math
                import struct
                import wave as wavemod
                out = {}
                buf = io.BytesIO()
                w = wavemod.open(buf, "wb")
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
                w.writeframes(b"".join(struct.pack(
                    "<h", int(8000 * math.sin(2 * math.pi * 440 * i / 16000)))
                    for i in range(32000)))
                w.close()
                tok = str(app.cfg.get("remote_token", ""))
                jhdr = {"Content-Type": "application/json", "X-Token": tok}
                base = "http://127.0.0.1:%d" % self.server.server_port

                def _post(path_, payload):
                    r = urllib.request.Request(
                        base + path_, data=json.dumps(payload).encode("utf-8"),
                        headers=jhdr, method="POST")
                    with urllib.request.urlopen(r, timeout=60) as f:
                        return json.loads(f.read().decode("utf-8"))

                try:  # 语音：分块上传通道
                    b64 = base64.b64encode(buf.getvalue()).decode()
                    beg = _post("/api/vbegin", {})
                    CH = 12288
                    for i in range(0, len(b64), CH):
                        _post("/api/vchunk", {"uid": beg["uid"], "seq": i // CH,
                                              "data": b64[i:i + CH]})
                    out["voice"] = _post("/api/vend", {"uid": beg["uid"]})
                except Exception as e:
                    out["voice_error"] = repr(e)
                try:
                    out["chat"] = _post("/api/chat", {"text": "现在几点了"})
                except Exception as e:
                    out["chat_error"] = repr(e)
                return self._json(200, out)

            # GET 桥接：tailscale serve 等反代会丢 POST body，
            # 所有写操作同时接受 GET+query（手机端默认走 GET 通道）
            if path.startswith("/api/"):
                from urllib.parse import urlparse, parse_qs
                qs = parse_qs(urlparse(self.path).query, keep_blank_values=True)
                body = {k: v[-1] for k, v in qs.items()}
                return self._api(path, body)
            return self._json(404, {"error": "not found"})

        def _do_POST(self):
            if not self._authed():
                return self._json(401, {"error": "unauthorized"})
            path = self.path.split("?", 1)[0]
            body = self._body()
            return self._api(path, body)

        def _api(self, path, body):
            if path == "/api/chat":
                text = str(body.get("text", "")).strip()
                if len(text) < 1:
                    return self._json(400, {"error": "empty"})
                try:
                    resp = self._chat(text)
                except Exception:
                    return self._json(500, {"error": "brain error"})
                return self._json(200, {"resp": resp})

            if path == "/api/voice":
                t0 = time.time()
                data = self._raw_body()
                _log("remote: voice in %dB (%.2fs)" % (len(data), time.time() - t0))
                if not data:
                    return self._json(400, {"error": "empty"})
                text, err = self._recognize_wav(data)
                _log("remote: voice rec (%.2fs) text=%r err=%r" %
                     (time.time() - t0, text, err))
                if err:
                    return self._json(400, {"error": err})
                if not text:
                    return self._json(200, {"text": "", "resp": None})
                try:
                    resp = self._chat(text)
                except Exception:
                    return self._json(500, {"error": "brain error"})
                return self._json(200, {"text": text, "resp": resp})

            # —— 语音分块上传（小 body 走可靠通道）——
            if path == "/api/vbegin":
                uid = secrets.token_hex(6)
                store = getattr(app, "_voice_up", None)
                if store is None:
                    store = app._voice_up = {}
                store[uid] = {"parts": {}, "t": time.time()}
                return self._json(200, {"uid": uid})
            if path == "/api/vchunk":
                uid = str(body.get("uid", ""))
                up = getattr(app, "_voice_up", {}).get(uid)
                if up is None:
                    return self._json(400, {"error": "no upload"})
                up["parts"][int(body.get("seq", 0))] = \
                    base64.b64decode(str(body.get("data", "")))
                return self._json(200, {"ok": True})
            if path == "/api/vend":
                uid = str(body.get("uid", ""))
                up = getattr(app, "_voice_up", {}).pop(uid, None)
                if up is None:
                    return self._json(400, {"error": "no upload"})
                data = b"".join(p for _, p in sorted(up["parts"].items()))
                text, err = self._recognize_wav(data)
                if err:
                    return self._json(400, {"error": err})
                if not text:
                    return self._json(200, {"text": "", "resp": None})
                try:
                    resp = self._chat(text)
                except Exception:
                    return self._json(500, {"error": "brain error"})
                return self._json(200, {"text": text, "resp": resp})

            if path == "/api/say":
                resp = {"zh": str(body.get("zh", "")), "ja": str(body.get("ja", ""))}
                if not resp["zh"] and not resp["ja"]:
                    return self._json(400, {"error": "empty"})
                app.events.put(("remote_say", resp))
                return self._json(200, {"ok": True})

            if path == "/api/wake":
                app.manual_wake()
                return self._json(200, {"ok": True})

            if path == "/api/sleep":
                app.events.put(("sleep", ""))
                return self._json(200, {"ok": True})

            if path == "/api/pose":
                key = str(body.get("key", "wave"))
                if key == "spin":
                    app.avatar.spin()
                else:
                    app.avatar.pose("wave", int(body.get("ms", 1800)))
                return self._json(200, {"ok": True})

            if path == "/api/display":
                app.avatar.toggle()
                return self._json(200, {"ok": True})

            if path == "/api/todos":
                text = str(body.get("text", "")).strip()
                if not text:
                    return self._json(400, {"error": "empty"})
                app.tasks.add_todo(text)
                return self._json(200, {"ok": True})

            if path == "/api/config":
                return self._save_config(body)

            if path == "/api/run":
                action = body.get("action", {})
                if isinstance(action, str):  # GET 桥接时为 JSON 字符串
                    try:
                        action = json.loads(action)
                    except (ValueError, TypeError):
                        action = {}
                if not isinstance(action, dict):
                    action = {}
                name = str(action.get("action", ""))
                if name not in SAFE_ACTIONS:
                    return self._json(400, {"error": "action not allowed: %s" % name})
                runner = getattr(app, "execute", None) or app.tasks.execute
                result = runner(action)
                app.avatar.say(str(result)[:60])
                return self._json(200, {"result": result})

            return self._json(404, {"error": "not found"})

        def _do_DELETE(self):
            if not self._authed():
                return self._json(401, {"error": "unauthorized"})
            if self.path.startswith("/api/todos/"):
                try:
                    tid = int(self.path.rsplit("/", 1)[-1])
                except ValueError:
                    return self._json(400, {"error": "bad id"})
                ok = app.tasks.del_todo(tid)
                return self._json(200, {"ok": ok})
            return self._json(404, {"error": "not found"})

        # ---------- 内部 ----------
        def _status(self):
            l = app.listener
            return {
                "uptime_s": int(time.time() - remote.started),
                "listener_ready": l is not None,
                "mic_enabled": bool(l.enabled) if l else False,
                "awake": bool(l.awake) if l else False,
                "todos": len(app.tasks.list_todos()),
                "model": app.cfg.get("model", ""),
                "master": app.cfg.get("master_name", "主人"),
                "lan_url": "http://%s:%d" % (_lan_ip(), remote.port),
            }

        @staticmethod
        def _tail(n):
            try:
                with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
                    return "".join(f.readlines()[-n:])
            except OSError:
                return "(暂无日志)"

        def _save_config(self, body):
            changed, needs_restart = [], []
            for k, v in body.items():
                if k not in CONFIG_KEYS:
                    continue
                want = CONFIG_KEYS[k]
                try:
                    if want is int:
                        v = int(v)
                    elif want is bool:
                        if isinstance(v, str):
                            v = v.lower() in ("1", "true", "on", "是")
                        v = bool(v)
                    elif want is list:
                        if isinstance(v, str):
                            try:  # UI 走 GET 通道时数组被 JSON 序列化成字符串
                                v = json.loads(v)
                            except ValueError:
                                v = re.split(r"[,，]", v)
                        v = [str(x) for x in v]
                    else:
                        v = str(v)
                except (TypeError, ValueError):
                    return self._json(400, {"error": "bad value: %s" % k})
                if v == app.cfg.get(k):
                    continue
                app.cfg[k] = v
                changed.append(k)
                if k.startswith("tts_") or k in ("session_timeout",):
                    needs_restart.append(k)
            if changed:
                self._save_config_file()
                # 热更新 brain 侧配置（称呼/城市/API 即时生效）
                if hasattr(app, "brain") and hasattr(app.brain, "cfg"):
                    app.brain.cfg.update({k: app.cfg[k] for k in changed})
            _log("remote: config changed:", ",".join(changed) or "(none)")
            return self._json(200, {"ok": True, "changed": changed,
                                    "needs_restart": needs_restart})

        @staticmethod
        def _save_config_file():
            try:
                with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                    json.dump(app.cfg, f, ensure_ascii=False, indent=2)
            except OSError as e:
                _log("remote: save config failed:", e)

    Handler.do_GET = _wrap(Handler._do_GET)
    Handler.do_POST = _wrap(Handler._do_POST)
    Handler.do_DELETE = _wrap(Handler._do_DELETE)
    return Handler


def start_remote(app):
    remote = MaidRemote(app)
    remote.start()
    app.remote = remote
    return remote
