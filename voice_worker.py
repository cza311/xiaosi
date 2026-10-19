# -*- coding: utf-8 -*-
"""语音识别工作进程：本机 HTTP 服务（127.0.0.1 随机端口）
启动后在 stdout 打印 {"type":"ready","port":N}。
端点：
  POST /rec          整段 WAV 转文本（远程按住说话用）
  POST /stream       流式识别：body=16k int16 音频块，返回 {partial, final}
  POST /stream/reset 重置流式识别状态
主进程退出（stdin 关闭）时自动结束。"""
import base64
import io
import json
import os
import re
import sys
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, "libs"))


def log(*a):
    try:
        with open(os.path.join(BASE, "maid_log.txt"), "a", encoding="utf-8") as f:
            f.write("[voice-worker] " + " ".join(str(x) for x in a) + "\n")
    except OSError:
        pass


def find_model():
    dirs = sorted(os.listdir(BASE))
    # 优先大模型（识别更准）：小四的主识别链路跑在这里
    for d in ([x for x in dirs if x.startswith("vosk-model")] +
              [x for x in dirs if x == "model_cn"]):
        p = os.path.join(BASE, d)
        if os.path.isdir(p) and os.path.exists(os.path.join(p, "am")):
            return d
    return None


MODEL = None
REC = None  # 流式识别器（单会话，小四主链路独占）
REC_LOCK = threading.Lock()


def stream_chunk(data):
    """喂一块 16k int16 音频，返回 (partial, final)"""
    global REC
    import vosk
    with REC_LOCK:
        if REC is None:
            REC = vosk.KaldiRecognizer(MODEL, 16000)
            REC.SetWords(False)
        if REC.AcceptWaveform(data):
            final = json.loads(REC.FinalResult()).get("text", "")
            partial = ""
        else:
            final = ""
            partial = json.loads(REC.PartialResult()).get("partial", "")
    return partial, final


def recognize(data):
    wf = wave.open(io.BytesIO(data), "rb")
    if wf.getnchannels() != 1 or wf.getsampwidth() != 2:
        raise ValueError("需要 16bit 单声道 WAV")
    rate = wf.getframerate()
    frames = wf.readframes(wf.getnframes())
    import vosk
    rec = vosk.KaldiRecognizer(MODEL, rate)
    rec.SetWords(False)
    parts = []
    step = 8000
    for i in range(0, len(frames), step):
        if rec.AcceptWaveform(frames[i:i + step]):
            parts.append(json.loads(rec.Result()).get("text", ""))
    parts.append(json.loads(rec.FinalResult()).get("text", ""))
    return re.sub(r"\s+", "", "".join(parts))


class H(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            data = self.rfile.read(n) if n > 0 else b""
            if self.path == "/stream":
                if len(data) == 0 or len(data) > 1_000_000:
                    return self._json(400, {"error": "bad chunk"})
                partial, final = stream_chunk(data)
                return self._json(200, {"partial": partial, "final": final})
            if self.path == "/stream/reset":
                global REC
                with REC_LOCK:
                    REC = None
                return self._json(200, {"ok": True})
            if self.path != "/rec":
                return self._json(404, {"error": "not found"})
            if len(data) > 3_000_000:
                return self._json(400, {"error": "too long"})
            text = recognize(data)
            self._json(200, {"text": text})
        except Exception as e:
            self._json(500, {"error": str(e)})

    def _json(self, code, obj):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main():
    global MODEL
    import vosk
    vosk.SetLogLevel(-1)
    d = find_model()
    if not d:
        print(json.dumps({"type": "error", "error": "no model"}), flush=True)
        return
    MODEL = vosk.Model(os.path.join(BASE, d))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
    port = httpd.server_address[1]
    log("worker http ready, model =", d, "port =", port)
    print(json.dumps({"type": "ready", "port": port}), flush=True)
    # 模型页大部分是冷的：周期置换，压低物理内存
    import ctypes

    def tidy():
        while True:
            time.sleep(300)
            try:
                ctypes.windll.psapi.EmptyWorkingSet(
                    ctypes.windll.kernel32.GetCurrentProcess())
            except Exception:
                pass
    threading.Thread(target=tidy, daemon=True).start()
    # 主进程退出后 stdin 关闭 → 自动结束
    threading.Thread(target=lambda: (sys.stdin.readline(), os._exit(0)),
                     daemon=True).start()
    httpd.serve_forever()


if __name__ == "__main__":
    main()
