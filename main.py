# -*- coding: utf-8 -*-
"""女仆酱 · 主程序：桌面形象 + 语音唤醒/按键唤醒 + 对话（日语音色 + 中文字幕），无聊天窗口"""
import json
import os
# 证书库：certifi + Windows 系统信任（含 Watt Toolkit 等加速器根证书），
# 必须在 import requests 之前设置，对本进程所有 requests 调用生效
os.environ.setdefault("REQUESTS_CA_BUNDLE",
                      os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "ca_bundle.pem"))
import queue
import random
import subprocess
import sys
import threading
import time
import traceback

import ctypes


def _set_dpi_aware():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


_set_dpi_aware()  # 必须在创建任何窗口前

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, "libs"))
sys.path.insert(0, BASE)

WAKE_ACK = {"zh": "在的，主人。请讲。", "ja": "はい、お呼びでしょうか、ご主人様。"}
SLEEP_BYE = {"zh": "那小四先退下了，主人叫我一声就好。", "ja": "それでは、また呼んでくださいね、ご主人様。"}
HELLO = {"zh": "主人，小四上线了。喊「小四」或按 C K 都能叫我。", "ja": "おかえりなさいませ、ご主人様。メイドちゃん、起動しました。"}
VOICE_FAIL = {"zh": "麦克风或语音模型不可用，语音功能暂时关闭（%s）。按 C K 仍可唤醒我。", "ja": ""}

try:  # 统一台词工厂：唤醒/休眠应答也从变体词库洗牌抽（即时场景，不走 LLM）
    import maid_talk
except Exception:
    maid_talk = None


def _fixed(bank_fn, dft):
    """变体词库抽取的保险包装：maid_talk 缺席/异常时用原固定句"""
    if maid_talk is None:
        return dft
    try:
        return bank_fn() or dft
    except Exception:
        return dft


def log(*args):
    try:
        p = os.path.join(BASE, "maid_log.txt")
        try:
            if os.path.exists(p) and os.path.getsize(p) > 512 * 1024:
                os.replace(p, p + ".old")  # 日志轮转
        except OSError:
            pass
        with open(p, "a", encoding="utf-8") as f:
            f.write(time.strftime("[%H:%M:%S] ") + " ".join(str(a) for a in args) + "\n")
    except OSError:
        pass


def _hook_exceptions():
    """P6：任何线程的未捕获异常都落日志，不再静默死亡"""
    def th(args):
        log("THREAD FATAL:", args.exc_type.__name__, str(args.exc_value)[:160],
            "".join(traceback.format_exception(
                args.exc_type, args.exc_value, args.exc_traceback))[-1200:])
    threading.excepthook = th

    def sysexc(t, v, tb):
        log("FATAL:", t.__name__, str(v)[:200])
    sys.excepthook = sysexc


_hook_exceptions()


class MaidApp:
    def __init__(self):
        log("app starting")
        with open(os.path.join(BASE, "config.json"), "r", encoding="utf-8") as f:
            self.cfg = json.load(f)

        from maid_avatar import MaidAvatar
        from maid_voice import Speaker, KeyWake, MouseWake
        from maid_brain import MaidBrain
        from maid_tasks import MaidTasks

        avatar_png = os.path.join(BASE, "avatar.png")
        # 形象引擎分流：config.json "avatar_engine" == "live2d" 时启用 Live2D
        # 引擎（pywebview 透明窗口），初始化失败自动回退 PNG 立绘；默认 "png"
        self.avatar = None
        if str(self.cfg.get("avatar_engine", "png")).strip().lower() == "live2d":
            try:
                from maid_avatar_l2d import L2dAvatar
                self.avatar = L2dAvatar(
                    avatar_png,
                    fullscreen_hide=self.cfg.get("fullscreen_hide", True))
                log("avatar engine: live2d (Hiyori)")
            except Exception as e:
                log("L2dAvatar init failed, fallback to PNG:", repr(e)[:200])
                self.avatar = None
        if self.avatar is None:
            # W4：fullscreen_hide=False 时立绘不启动全屏退让轮询（config 支持）
            self.avatar = MaidAvatar(
                avatar_png, fullscreen_hide=self.cfg.get("fullscreen_hide", True))
        self.avatar.menu_handler = self.on_menu
        self.tasks = MaidTasks(
            on_remind=self.on_remind,
            get_config=lambda: self.brain.cfg)
        from maid_extras import MaidExtras
        self.extras = MaidExtras(get_config=lambda: self.brain.cfg, log_fn=log)

        def _exec(action):
            try:  # W1：执行前进入干活状态（动作名进 detail）
                import maid_status
                maid_status.set_status(
                    "working",
                    action.get("action", "?") if isinstance(action, dict) else "?")
            except Exception:
                pass
            r = self.extras.execute(action)
            r = self.tasks.execute(action) if r is None else r
            try:  # 中文运行日志：动作与结果记进 D:\小四\日志
                import maid_cnlog
                act = action.get("action", "?") if isinstance(action, dict) else "?"
                maid_cnlog.cnlog("动作[%s] → %s" % (act, str(r)[:100]))
            except Exception:
                pass
            try:  # W1+W3：按结果更新全局状态并驱动表情（不阻塞）
                import maid_status
                act = action.get("action", "?") if isinstance(action, dict) else "?"
                s = str(r)
                if "等待确认" in s or "待确认" in s:
                    maid_status.set_status("waiting", act)
                elif ("失败" in s or "错误" in s or "Error" in s
                        or "Traceback" in s):
                    maid_status.set_status("error", act)
                    self.avatar.pose("angry")  # W3：出错皱眉
                else:
                    maid_status.finish(act)  # 长任务进行中不覆盖（singing 保护）
                    self.avatar.pose(random.choice(("heart", "wave")))  # W3：完成比心/挥手
            except Exception:
                pass
            return r

        self.brain = MaidBrain(os.path.join(BASE, "config.json"),
                               executor=_exec, log_fn=log)
        self.brain.memory_provider = self.extras.memory_brief  # 长期记忆自动注入大脑
        self.brain.skill_provider = self.extras.skills_brief  # 技能库检索注入
        self.brain.say_stream = self._on_stream_seg  # 流式念白：台词闭合一段念一段（学七海）
        self._stream_session = None  # 当前的"边生成边播"会话（speak 统一收尾）
        self._streamed_segs = []     # 流式已念出的段落（供 speak 对账）
        self.execute = _exec  # 远程控制也走同一条工具链
        self.extras.report = self.speak  # 后台学习完成后主动开口汇报
        self.extras.play_audio = self.play_wav  # 翻唱成品播放
        try:  # 统一台词工厂：主动开口的话（问候/搭话/汇报）尽量过大模型现编
            import maid_talk
            maid_talk.setup(brain=self.brain,
                            memory_brief=self.extras.memory_brief)
        except Exception as e:
            log("maid_talk setup failed:", e)
        try:  # W1/W2/W3：全局状态变化 → 灵动岛胶囊 + 唱歌表情联动（零侵入，不接不显示）
            import maid_status

            def _on_status(status, detail):
                try:
                    self.avatar.set_status(status, detail)  # W2：状态胶囊
                    if status == "singing":
                        self.avatar.pose("heart", 4000)  # W3：开唱比心
                except Exception:
                    pass

            maid_status.set_listener(_on_status)
        except Exception as e:
            log("maid_status init failed:", e)
        self.speaker = Speaker(self.cfg.get("tts_voice", "ja-JP-NanamiNeural"),
                               rate=self.cfg.get("tts_rate", "-10%"),
                               pitch=self.cfg.get("tts_pitch", "-15Hz"),
                               volume=self.cfg.get("tts_volume", "-10%"),
                               on_idle=lambda: (self.avatar.set_talking(False),
                                                self.avatar.set_anim(False)))
        self.events = queue.Queue()
        self.listener = None
        self.quitting = False
        self._holder = {"listener": None}

        self.avatar.on_click = self.manual_wake
        self.avatar.on_touch_event = self.on_touch_event  # 戳/摸头/抛掷进对话上下文
        self.mousewake = MouseWake(self.manual_wake, log_fn=log)  # 全局中键唤醒：点滚轮键

        try:  # 手机远程控制服务（局域网）：同时拉起语音识别 worker
            from maid_remote import start_remote
            start_remote(self)
        except Exception as e:
            log("remote failed:", e)

        threading.Thread(target=self._setup_listener, daemon=True).start()
        threading.Thread(target=self._converse, daemon=True).start()
        threading.Thread(target=self.extras.proactive_loop,
                         args=(self.speak,), daemon=True).start()
        threading.Thread(target=self._heartbeat, daemon=True).start()
        threading.Thread(target=self._mem_tidy, daemon=True).start()
        threading.Thread(target=self.extras.self_study_loop,
                         args=(self.brain.learn_call,), daemon=True).start()  # 空闲自学
        log("threads started")
        try:  # P6 看门狗：崩溃自动拉起
            subprocess.Popen(["py", os.path.join(BASE, "watchdog.py")], cwd=BASE,
                             creationflags=0x00000008 | 0x00000200)
        except Exception as e:
            log("watchdog spawn failed:", e)

        try:
            from maid_tray import start_tray
            start_tray(avatar_png, on_quit=self.quit_all, avatar=self.avatar,
                       listener_holder=self._holder)
        except Exception as e:
            log("tray failed:", e)

        threading.Timer(1.2, lambda: (self.avatar.pose("wave", 2000),
                                      self.avatar.spin(),
                                      self.speak(_fixed(maid_talk.hello
                                                        if maid_talk else None, HELLO)))).start()

    def _mem_tidy(self):
        """内存治理：周期 gc + 置换不活跃页面；并追踪内存增长来源"""
        import gc
        import tracemalloc
        tracemalloc.start(6)
        first = None
        while not self.quitting:
            time.sleep(300)
            try:
                gc.collect()
                snap = tracemalloc.take_snapshot()
                if first is None:
                    first = snap
                log("MEM top:", *["%s" % s for s in snap.statistics("lineno")[:6]])
                log("MEM growth:", *["%s" % s
                                     for s in snap.compare_to(first, "lineno")[:6]])
                psapi = ctypes.windll.psapi
                psapi.EmptyWorkingSet(ctypes.windll.kernel32.GetCurrentProcess())
            except Exception:
                pass

    def _heartbeat(self):
        """P6：每 10 秒写心跳，供看门狗判断崩溃"""
        hb = os.path.join(BASE, "maid_alive.txt")
        while not self.quitting:
            try:
                with open(hb, "w") as f:
                    f.write(str(time.time()))
            except OSError:
                pass
            time.sleep(10)
        try:
            os.remove(hb)  # 正常退出，看门狗不再拉起
        except OSError:
            pass

    # ---------- 语音 ----------
    def _setup_listener(self):
        def find_model():
            names = sorted(os.listdir(BASE))
            # 优先大模型（vosk-model-*，识别更准），其次兼容 "model_cn" 小模型
            for want in ("vosk-model", "model"):
                for d in names:
                    p = os.path.join(BASE, d)
                    if os.path.isdir(p) and d.startswith(want) \
                            and os.path.exists(os.path.join(p, "am")):
                        return p
            return None

        try:
            # 等 remote 拉起的识别 worker 就绪（worker 加载大模型需几秒）
            port = getattr(self, "_voice_port", None)
            for _ in range(120):
                port = getattr(self, "_voice_port", None)
                if port:
                    break
                time.sleep(0.5)
            remote_url = ("http://127.0.0.1:%d" % port) if port else None
            if remote_url:
                log("listener mode: remote worker (vosk out-of-process)")
            from maid_voice import Listener
            model_dir = find_model()
            if not model_dir:
                raise RuntimeError("未找到语音模型")
            l = Listener(model_dir, self.cfg.get("wake_words",
                         ["女仆", "梅酱", "妹抖", "梅德", "maid"]),
                         on_event=lambda k, t: self.events.put((k, t)),
                         speaker=self.speaker, log_fn=log,
                         mic_keyword=self.cfg.get("mic_keyword", ""),
                         on_heard=self.avatar.show_heard,
                         remote_url=remote_url,
                         # 切句器参数（移植 Coopanion segmenter）：config 可调，
                         # 麦克风吵就升 threshold_db（如 -38），安静就降回 -42
                         mic_cfg={
                             "threshold_db": self.cfg.get("mic_threshold_db", -42.0),
                             "min_speech_ms": self.cfg.get("mic_min_speech_ms", 180),
                             "dispatch_silence_ms": self.cfg.get("mic_dispatch_silence_ms", 250),
                             "silence_ms": self.cfg.get("mic_silence_ms", 500),
                             "max_utterance_ms": self.cfg.get("mic_max_utterance_ms", 15000),
                             "pre_roll_ms": self.cfg.get("mic_pre_roll_ms", 320),
                             "min_utterance_ms": self.cfg.get("mic_min_utterance_ms", 350),
                             "min_chars": self.cfg.get("mic_min_chars", 2),
                             "join_gap_ms": self.cfg.get("mic_join_gap_ms", 0),
                             "max_hold_ms": self.cfg.get("mic_max_hold_ms", 8000),
                         })
            self.listener = l
            self._holder["listener"] = l
            log("listener ok")
            l.watch(self.cfg.get("session_timeout", 60), lambda: self.events.put(("sleep", "")))
        except Exception as e:
            log("listener failed:", e)
            self.avatar.say(VOICE_FAIL["zh"] % e)

    def manual_wake(self):
        """单击形象或按 Alt 唤醒（重复按会重新应答并刷新会话计时）。
        语音监听被主人关闭时不唤醒，避免 Alt+Tab/Alt+F4 误触叫醒她。"""
        if self.listener and self.listener.enabled:
            self.listener.wake()
            self.events.put(("wake", ""))

    def on_touch_event(self, kind, extra=""):
        """桌宠触碰事件（戳/摸头/抛掷，学 Coopanion）：并入对话上下文，
        主人下次开口时小四自然"记得"刚才的互动，而不是只有立绘台词。"""
        text = {"pat": "主人摸了摸小四的头",
                "poke": "主人连戳了小四几下",
                "throw": "主人把小四拎起来抛了出去",
                "landed": "小四抛出去后落回了桌面"}.get(kind)
        if not text:
            return
        try:
            h = self.brain._load_history()
            h.append({"role": "user", "content":
                      "（系统事件：%s%s。这是身体互动，不必专门回应，"
                      "但下次回复时可以自然带点相应的情绪）" % (text, extra)})
            self.brain._save_history(h)
        except Exception:
            pass

    # ---------- 右键菜单 ----------
    def play_wav(self, path):
        """异步播放本地音频（翻唱成品等），不阻塞主循环"""
        try:
            import winsound
            self.avatar.set_talking(True)
            winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
        except Exception as e:
            log("play_wav failed:", e)

    def on_menu(self, tag):
        if tag == "settings":
            subprocess.Popen(["pyw", os.path.join(BASE, "settings.py")], cwd=BASE)
        elif tag == "chat":
            # 独立进程聊天窗：避开主窗口无边框体系的键盘焦点坑
            subprocess.Popen(["pyw", os.path.join(BASE, "maid_chat.py")], cwd=BASE)
        elif tag == "voice":
            l = self.listener
            if l is not None:
                l.enabled = not l.enabled
                if not l.enabled:
                    l.sleep()
                self.avatar.set_voice_state(l.enabled)
        elif tag == "warehouse":
            try:
                os.startfile(os.path.join(os.path.dirname(BASE), "小四"))
            except Exception:
                pass
        elif tag == "hide":
            self.avatar.toggle()
        elif tag == "quit":
            self.quit_all()

    # ---------- 对话主循环 ----------
    def after_chat(self, text):
        """每轮对话结束后的自我学习与技能沉淀（语音/文字入口共用）"""
        try:
            self.extras.auto_learn(self.brain._load_history(),
                                   self.brain.learn_call)
        except Exception as e:
            log("auto-learn spawn error:", e)
        try:
            if any(a.get("action") in ("run_ps", "write_file")
                   for a in (getattr(self.brain, "last_actions", None) or [])):
                self.extras.auto_skill(self.brain._load_history(),
                                       self.brain.learn_call)
        except Exception as e:
            log("auto-skill spawn error:", e)

    def _converse(self):
        log("converse thread up")
        while not self.quitting:
            try:
                kind, text = self.events.get()
            except Exception as e:
                log("event error:", repr(e))
                continue
            log("event:", kind, str(text)[:40])
            try:
                if kind == "wake":
                    self.avatar.touch()
                    self.avatar.pose("wave", 1800)
                    self.speak(_fixed(maid_talk.wake_ack if maid_talk else None, WAKE_ACK))
                elif kind == "sleep":
                    if self.listener:
                        self.listener.sleep()
                    self.speak(_fixed(maid_talk.sleep_bye if maid_talk else None, SLEEP_BYE))
                elif kind == "utter":
                    self.avatar.touch()
                    self.speaker.interrupt()  # 主人插话，立刻闭嘴听新命令
                    if len(text) < 2:
                        continue  # 单字噪音不进大脑
                    # 合并积压的连续语音，只取最新一句，防止排队雪崩
                    deferred = []
                    try:
                        while True:
                            k2, t2 = self.events.get_nowait()
                            if k2 == "utter" and len(t2) >= 2:
                                text = t2
                            else:
                                deferred.append((k2, t2))
                    except queue.Empty:
                        pass
                    for ev in deferred:
                        self.events.put(ev)
                    log("chat <<<", text)
                    try:  # W1：请求 LLM 前进入思考状态
                        import maid_status
                        maid_status.set_status("thinking")
                    except Exception:
                        pass
                    # 流式念白会话：大脑每闭合一段台词立刻送合成（学七海 tts_queue）
                    self._stream_session = self.speaker.open_stream()
                    self._streamed_segs = []
                    try:
                        resp = self.brain.chat(text)
                        try:  # W1：回复到手回空闲（仅思考中；动作状态不被覆盖）
                            import maid_status
                            maid_status.clear_if("thinking")
                        except Exception:
                            pass
                        log("chat >>>", str(resp.get("zh", ""))[:60])
                        self.speak(resp)
                        self.after_chat(text)  # 对话后自我学习 + 技能沉淀
                    finally:
                        # speak 已按对账结果 finish/abandon；异常兜底再 abandon（幂等）
                        sess, self._stream_session = self._stream_session, None
                        self._streamed_segs = []
                        if sess:
                            sess.abandon()
                elif kind == "remote_say":
                    # 手机控制台发来的回复/消息：桌面同步显示+朗读
                    if isinstance(text, dict):
                        self.speak(text)
            except Exception:
                log("converse error:", traceback.format_exc())

    def _tts_engine(self):
        """当前 TTS 引擎（每次现读 config，设置切换即时生效）"""
        try:
            with open(os.path.join(BASE, "config.json"), "r",
                      encoding="utf-8") as f:
                return str(json.load(f).get("tts_engine", "") or "")
        except Exception:
            return str(self.cfg.get("tts_engine", "") or "")

    def _on_stream_seg(self, seg):
        """大脑流式回调（学七海）：台词每闭合一段，字幕跟着走、语音立刻进队列。
        会话由 speak() 按对账结果统一 finish/abandon。"""
        seg = str(seg).strip()
        if not seg or getattr(self, "_stream_session", None) is None:
            return
        self._streamed_segs.append(seg)
        try:
            self.avatar.say(" ".join(self._streamed_segs))  # 字幕跟随生成进度
        except Exception:
            pass
        sess = self._stream_session
        if not getattr(sess, "ok", False):
            try:  # 首段即将出声：摆动+口型（播完由 on_idle 统一关闭）
                self.avatar.set_anim(True)
                self.avatar.set_talking(True)
            except Exception:
                pass
        sess.feed(seg)

    def speak(self, resp):
        # 流式念白对账（学七海）：台词已随生成逐段出声就不再重复朗读
        sess = getattr(self, "_stream_session", None)
        if sess is not None:
            self._stream_session = None
            streamed = list(self._streamed_segs)
            self._streamed_segs = []
            zh = resp.get("zh", "")
            segs_norm = ([str(s).strip() for s in zh if str(s).strip()]
                         if isinstance(zh, list) else
                         ([str(zh).strip()] if str(zh or "").strip() else []))
            if getattr(sess, "ok", False) and streamed == segs_norm:
                sess.finish()  # 已念且对上了：等队列播完自然收尾
                if self.listener:
                    self.listener.last_seen = time.time()
                return
            sess.abandon()  # 没念上或念岔了：弃单，走下面的整段路径
        zh = resp.get("zh", "")
        ja = resp.get("ja", "")
        zh_segs = zh if isinstance(zh, list) else None  # 剧本式台词数组（学七海）
        if isinstance(zh, list):
            zh = " ".join(str(s) for s in zh if str(s).strip())
        else:
            zh = str(zh or "")
        if isinstance(ja, list):
            ja = " ".join(str(s) for s in ja if str(s).strip())
        else:
            ja = str(ja or "")
        display = zh or ja  # 字幕/气泡：优先中文（含（动作）旁白，看着更有戏）
        if not display:
            return  # 没有可说的话：不出框不出声
        try:
            emo = str(resp.get("emo", "") or "").strip()  # 大脑回复的情绪标签（可无）
        except Exception:
            emo = ""
        self.avatar.say(display)
        if not zh:
            # 只有日语：字幕照显但【不发声】——中文音素念日语必成乱音，主人也听不懂
            return
        self.avatar.set_anim(True)
        self.avatar.set_talking(True)  # 摆动+口型持续到 Speaker 播完（on_idle 统一关闭）
        if self._tts_engine() == "gptsovits":
            if zh_segs:  # 台词数组逐段进流式队列，短句节奏感
                self.speaker.speak_segments(zh_segs, emo)
            else:
                self.speaker.speak_stream(zh, emo)
        else:
            self.speaker.say(zh)
        if self.listener:
            self.listener.last_seen = time.time()

    def on_remind(self, text):
        self.speak({"zh": "⏰ " + text, "ja": "ご主人様、日程のお時間ですよ。"})

    # ---------- 退出 ----------
    def quit_all(self):
        if self.quitting:
            return
        self.quitting = True
        try:
            if self.listener:
                self.listener.stop()
        except Exception:
            pass
        try:
            self.speaker.stop()
            self.tasks.stop()
            self.avatar.quit()
        except Exception:
            pass


def _single_instance():
    """防止重复启动（命名互斥体）"""
    ctypes.windll.kernel32.CreateMutexW(None, False, "MaidChan_SingleInstance_Mutex")
    return ctypes.windll.kernel32.GetLastError() != 183  # 183 = ERROR_ALREADY_EXISTS


def main():
    if not _single_instance():
        log("another instance is running, exit")
        return
    app = MaidApp()
    app.avatar.run()  # Tk 主循环在主线程
    app.quit_all()


if __name__ == "__main__":
    main()
