# -*- coding: utf-8 -*-
"""小四的扩展能力：长期记忆、环境感知、主动关怀（久坐/晨昏/搭话）"""
import datetime
import json
import os
import random
import re
import sys
import threading
import time

try:  # 统一台词工厂：主动开口的话尽量过大模型现编，本地词库只兜底
    import maid_talk
except Exception:  # 台词工厂缺席时各调用点自动回退原固定句
    maid_talk = None


def _talk(scene, context=None, wait=3.0):
    """A层台词入口的保险包装：台词工厂不可用/异常时返回 None，调用方用原句兜底"""
    if maid_talk is None:
        return None
    try:
        return maid_talk.generate_line(scene, context, wait)
    except Exception:
        return None


MEM_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "maid_memory.json")
MAID_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = None  # 下面按小四仓库根推导（ maid 所在盘的「小四」目录）
ARCHIVE = os.path.join(os.path.dirname(MAID_DIR),
                       "小四")   # 小四的仓库根：作品/日志/技能
SKILL_DIR = os.path.join(ARCHIVE, "技能")
# 主人画像（PARTNER.md 思想）：文档式记忆，每次改动旧版存历史
MEM_DIR = os.path.join(ARCHIVE, "记忆")
PROFILE_FILE = os.path.join(MEM_DIR, "主人画像.md")
HIST_DIR = os.path.join(MEM_DIR, "历史")
PROFILE_MAX = 3000  # 注入大脑的画像全文截断上限（字）

# 画像小节：remember 时按关键词归类落位
PROFILE_SECTIONS = ["## 基本信息", "## 喜好与习惯", "## 作息与日程",
                    "## 在做的事", "## 忌讳与纠正", "## 其他"]
PROFILE_TEMPLATE = """# 主人画像

> 小四眼里的主人。小四每次记住新事情都会写进对应小节，
> 改动前的旧版本自动存放在 历史\\ 文件夹，主人可随时翻看或亲手修改。

## 基本信息

## 喜好与习惯

## 作息与日程

## 在做的事

## 忌讳与纠正

## 其他
"""

# 对话后自动提炼事实用（Mem0 思路轻量版）：只记值得长期记住的，闲聊不污染记忆
LEARN_SYS = """你是记忆提炼器。分析一段主人与女仆AI小四的对话，判断是否包含值得小四【长期记住】的事实：
1. 主人的偏好、习惯、作息（喜欢/讨厌什么、常用软件、睡觉时间等）
2. 主人纠正过小四的地方（下次不能再犯）
3. 长期有效的个人信息（称呼、项目、身份等）
不要记录：寒暄客套、一次性任务（"帮我打开X"）、对对话内容的复述、模糊无价值的话。
若与已有记忆同义，输出同 key 的新表述（会自动覆盖更新）。
只输出严格 JSON：{"facts":[{"key":"简短关键词","content":"一句话事实"}]}；没有值得记的就输出 {"facts":[]}。"""

# 任务完成后自动提炼技能用：多步操作成功才沉淀，失败/闲聊不污染技能库
SKILL_LEARN_SYS = """你是技能提炼器。下面是主人与女仆AI小四的一次对话，小四通过工具（run_ps/write_file等）执行了任务。
如果任务是【多步操作】且执行结果看起来成功（结果无报错、主人没有否定），请把有效的分步做法提炼成一个可复用技能：
- name：简短技能名（如"统计文件夹最大文件"）
- steps：分步做法，写清关键命令和执行顺序，简洁但下次能直接照做
- tag：分类标签（如 文件管理/系统维护/网络/影音）
如果只是一步搞定的小事、执行失败了、或根本没有工具操作，输出 {"skill": null}。
只输出严格 JSON：{"skill":{"name":"...","steps":"...","tag":"..."}} 或 {"skill":null}。"""

CHATTER = [  # 待机搭话兜底词库（maid_talk 在位时走 LLM 现编，这里只兜底）
    {"zh": "主人，茶要凉了，起来活动一下吧。", "ja": "ご主人様、お茶が冷めますよ。"},
    {"zh": "主人，窗外天气不错，偶尔看看远处保护眼睛。", "ja": "いい天気ですよ、遠くを見て目を休めてください。"},
    {"zh": "主人，小四随时候命，有事叫我。", "ja": "いつでもお呼びくださいね。"},
]


def learn_github_bg(extras, topic):
    """后台执行 GitHub 学习：不阻塞对话；完成后 save_skill 入库并主动汇报。
    extras.report（如已由主程序注入）用于汇报。"""
    def work():
        try:
            sk = extras._learn_from_github(topic)
            if sk:
                extras.save_skill(sk[0], sk[1], sk[2] or "自学")
                rep = _talk("learn_done", {"topic": topic, "skill": sk[0],
                                           "steps": sk[1][:60]}) or \
                    {"zh": "主人，小四学会新技能了：%s —— %s（已存入技能库）"
                           % (sk[0], sk[1][:60]),
                     "ja": "ご主人様、新しいスキルを覚えました。"}
            else:
                rep = _talk("learn_miss", {"topic": topic}) or \
                    {"zh": "主人，GitHub 上暂时没找到关于「%s」的有价值资料，下次再试。"
                           % topic, "ja": ""}
            rep2 = getattr(extras, "report", None)
            if callable(rep2):
                rep2(rep)
        except Exception as e:
            extras.log("learn_github_bg error:", repr(e))
    threading.Thread(target=work, daemon=True, name="learn-github").start()


SONG_LIB = os.path.join(MAID_DIR, "song", "library")
SING_PIPELINE = os.path.join(MAID_DIR, "song", "sing_pipeline.py")
SING_OUT = os.path.join(ARCHIVE, "作品", "翻唱歌曲")
SYS_PY = sys.executable
BILI_FETCH = os.path.join(MAID_DIR, "song", "bili_fetch.py")

_SING_LOCK = threading.Lock()  # 唱歌并发锁：同一时间只跑一条翻唱流水线
_SING_STATE = {"running": False, "name": "", "t0": 0.0}


def sing_state():
    """唱歌进行状态（大脑回「怎么还没唱」用）：running/name/已进行秒数"""
    st = dict(_SING_STATE)
    st["secs"] = int(time.time() - st["t0"]) if st.get("running") else 0
    return st


def _no_window():
    """子进程禁弹黑窗（CREATE_NO_WINDOW）。pythonw 本身无控制台，控制台子程序
    默认会新蹦一个黑窗——2026-10-19 18:11 两条翻唱管线就是被主人误关黑窗
    连带杀掉的（退出码 3221225786 = STATUS_CONTROL_C_EXIT）"""
    return 0x08000000


def bili_fetch_song(query):
    """B 站按歌名自动找歌下载入库；成功返回文件路径，失败 None"""
    try:
        import subprocess
        r = subprocess.run([SYS_PY, "-u", BILI_FETCH, query],
                           capture_output=True, text=True, timeout=600,
                           encoding="utf-8", errors="replace",
                           creationflags=_no_window())
        out = (r.stdout or "")
        for line in out.splitlines():
            if line.startswith("DONE "):
                p = line[5:].strip()
                if os.path.exists(p):
                    return p
        extras_log = getattr(bili_fetch_song, "_log", None)
        if extras_log:
            extras_log("bili_fetch fail:", out[-200:])
    except Exception:
        pass
    return None


def find_song(query):
    r"""在歌库 D:\maid\song\library 里找歌：按文件名模糊匹配。
    点了歌名但库里没有 -> 返回 None（走 B 站找歌），绝不随机顶替；
    空查询（唱吧/唱歌）才随机挑一首"""
    files = []
    for ext in ("*.wav", "*.mp3", "*.flac", "*.m4a"):
        try:
            files += [f for f in os.listdir(SONG_LIB) if f.lower().endswith(ext[1:])]
        except OSError:
            return None
    if not files:
        return None
    q = (query or "").strip()
    if q:
        ql = q.lower()
        # 两轮：先精确子串，再关键词全部命中（歌名分词）
        for f in files:
            if ql in os.path.splitext(f)[0].lower():
                return os.path.join(SONG_LIB, f)
        words = [w for w in re.split(r"[\s〜~・]+", ql) if len(w) >= 2]
        if words:
            for f in files:
                low = os.path.splitext(f)[0].lower()
                if all(w in low for w in words):
                    return os.path.join(SONG_LIB, f)
        return None
    return os.path.join(SONG_LIB, random.choice(files))


def sing_bg(extras, query):
    """后台翻唱：歌库选歌 -> UVR5分离人声 -> RVC真白声线 -> 混音 -> 播放+汇报
    并发请求立即回进度；开工即进唱歌状态；管线 Popen 逐行透传日志不黑箱"""
    def work():
        name = "?"
        if not _SING_LOCK.acquire(blocking=False):
            prev = (_SING_STATE.get("name") or "那首歌")[:24]
            rep0 = getattr(extras, "report", None)
            if callable(rep0):
                rep0({"zh": "小四正学着唱《%s》呢，一首一首来，别急～" % prev,
                      "ja": ""})
            return
        try:
            _SING_STATE.update(running=False, name="", t0=0.0)
            def _stat(st, detail=""):
                try:
                    import maid_status
                    maid_status.set_status(st, detail)
                except Exception:
                    pass
            _stat("singing", "选歌中")  # 开工即进唱歌状态，全程可查
            path = find_song(query)
            fetched = False
            if not path and (query or "").strip():
                rep0 = getattr(extras, "report", None)
                if callable(rep0):
                    rep0({"zh": "歌库里没有《%s》，小四去 B 站找找，"
                                "找到就学来唱给你听，稍微等一下～" % query,
                          "ja": ""})
                bili_fetch_song._log = extras.log
                path = bili_fetch_song(query)
                fetched = bool(path)
            if not path:
                _stat("idle")
                if (query or "").strip():
                    msg = ("呜……B 站上也没找到《%s》，换个歌名试试？"
                           "或者把音频文件放进 %s" % (query, SONG_LIB))
                else:
                    msg = ("主人，小四的歌库空空的呢。说「唱《歌名》」"
                           "小四会自己去 B 站找来学唱哦～")
                rep = getattr(extras, "report", None)
                if callable(rep):
                    rep({"zh": msg, "ja": ""})
                return
            name = os.path.splitext(os.path.basename(path))[0]
            _SING_STATE.update(running=True, name=name, t0=time.time())
            _stat("singing", name)
            extras.log("sing start:", name)
            import subprocess
            runtime = (r"D:\maid\gptsovits\GPT-SoVITS-v2pro-20250604"
                       r"\runtime\python.exe")
            proc = subprocess.Popen(
                [runtime, SING_PIPELINE, path],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                cwd=r"D:\maid\song\RVC2023",
                creationflags=_no_window())
            killer = threading.Timer(1800, proc.kill)  # 30 分钟兜底超时
            killer.daemon = True
            killer.start()
            tail = []
            try:
                for line in proc.stdout:  # 逐行透传：管线全程可观测
                    line = (line or "").rstrip()
                    if line:
                        tail.append(line)
                        if len(tail) > 12:
                            tail.pop(0)
                        extras.log("sing:", line[:160])
                        _stat("singing", name)  # 心跳保活：被任何中间状态覆盖后下一行即拉回
                rc = proc.wait()
            finally:
                killer.cancel()
            out = os.path.join(SING_OUT, name + "_mashiro.wav")
            ok = rc == 0 and os.path.exists(out)
            rep = getattr(extras, "report", None)
            if ok:
                pa = getattr(extras, "play_audio", None)
                if callable(pa):
                    pa(out)
                if fetched:
                    fb = ("主人，小四自己去 B 站找到了《%s》，"
                          "已经用她自己的声音唱好了，听听看～" % name)
                else:
                    fb = ("主人，《%s》翻唱好了，是小四自己的声音哦，"
                          "听听看～" % name)
            else:
                fb = "呜……《%s》的翻唱失败了（%s），小四再练练。" % (
                    name, (tail[-1] if tail else "exit %s" % rc)[:100])
            extras.log("sing done:", name, "ok" if ok else rc)
            # 清理：学会后删掉 B 站下载的原曲和下载临时文件（work 中间产物保留可复用）
            try:
                if fetched and os.path.exists(path):
                    os.remove(path)
                    extras.log("cleanup: removed source", name)
                tmpdir = os.environ.get("TEMP", ".")
                for f in os.listdir(tmpdir):
                    if f.startswith("bili_dl_"):
                        try:
                            os.remove(os.path.join(tmpdir, f))
                        except OSError:
                            pass
            except OSError:
                pass
            if ok:
                _stat("done", name)  # 非 sticky，8 秒后自动回 idle
            else:
                _stat("error", name)
            if callable(rep):
                # 完成汇报走 LLM 现编（主人已等了半天流水线，3 秒换句新鲜话值）；失败回退原句
                if ok:
                    rep(_talk("sing_done", {"name": name, "fetched": fetched})
                        or {"zh": fb, "ja": ""})
                else:
                    rep(_talk("sing_fail", {"name": name,
                                            "err": (tail[-1] if tail else "")[-120:]})
                        or {"zh": fb, "ja": ""})
        except Exception as e:
            extras.log("sing_bg error:", repr(e))
            rep = getattr(extras, "report", None)
            if callable(rep):
                rep({"zh": "《%s》翻唱出错了：%s" % (name, type(e).__name__),
                     "ja": ""})
        finally:
            _SING_STATE.update(running=False)
            _SING_LOCK.release()
    threading.Thread(target=work, daemon=True, name="sing").start()


TUWEN_PIPELINE = r"D:\maid\tuwen\tuwen_pipeline.py"
TUWEN_IN = r"D:\maid\tuwen\input"
TUWEN_OUT = r"D:\maid\tuwen\output"


def tuwen_bg(extras, title):
    """后台做小说推文视频：找 input 里的文本 -> 跑流水线 -> 汇报"""
    def work():
        try:
            path = None
            t = (title or "").strip()
            if t:
                ql = t.lower()
                for f in os.listdir(TUWEN_IN):
                    if f.lower().endswith(".txt") and (
                            ql in f.lower()
                            or all(w in f.lower() for w in ql.split() if w)):
                        path = os.path.join(TUWEN_IN, f)
                        break
            else:
                cands = [os.path.join(TUWEN_IN, f)
                         for f in os.listdir(TUWEN_IN)
                         if f.lower().endswith(".txt")]
                if cands:
                    path = max(cands, key=os.path.getmtime)
            rep = getattr(extras, "report", None)
            if not path:
                if callable(rep):
                    rep({"zh": "主人，推文素材库里还没有文本哦。把小说片段存成"
                               " D:\\maid\\tuwen\\input\\标题.txt，"
                               "再对小四说「做个推文」就好啦～", "ja": ""})
                return
            name = os.path.splitext(os.path.basename(path))[0]
            extras.log("tuwen start:", name)
            logf = open(r"D:\maid\tuwen\last_run.log", "w",
                        encoding="utf-8")
            r = subprocess.run([SYS_PY, "-u", TUWEN_PIPELINE, path, name],
                               stdout=logf, stderr=subprocess.STDOUT,
                               timeout=1200, creationflags=_no_window())
            logf.close()
            out = os.path.join(TUWEN_OUT, name + ".mp4")
            ok = r.returncode == 0 and os.path.exists(out)
            if callable(rep):
                if ok:
                    rep(_talk("tuwen_done", {"name": name}) or
                        {"zh": "主人，小说推文《%s》做好了！竖屏带配音和字幕，"
                               "在小四的作品文件夹里，快来审片～" % name, "ja": ""})
                else:
                    tail = ""
                    try:
                        tail = open(r"D:\maid\tuwen\last_run.log",
                                    encoding="utf-8",
                                    errors="ignore").read()[-150:]
                    except OSError:
                        pass
                    rep(_talk("tuwen_fail", {"name": name, "err": tail}) or
                        {"zh": "呜……《%s》的推文视频做失败了。（%s）"
                               % (name, tail.replace("\n", " ")), "ja": ""})
            extras.log("tuwen done:", name, "ok" if ok else r.returncode)
        except Exception as e:
            extras.log("tuwen_bg error:", repr(e))
            rep = getattr(extras, "report", None)
            if callable(rep):
                rep({"zh": "推文流水线出错了：%s" % type(e).__name__, "ja": ""})
    threading.Thread(target=work, daemon=True, name="tuwen").start()


class MaidExtras:
    """记忆 / 感知 / 主动关怀"""

    def __init__(self, get_config=None, log_fn=None):
        cfg = get_config or {}
        if not callable(cfg):
            self.get_config = (lambda c: lambda: c)(cfg)  # 字典常量也统一成可调用
        else:
            self.get_config = cfg
        self.log = log_fn or (lambda *a: None)
        self.lock = threading.Lock()
        self._last_sedentary = 0.0
        self._greeted = {}  # date -> set(kind)
        try:
            os.makedirs(SKILL_DIR, exist_ok=True)  # 技能库目录
        except OSError:
            pass
        self.load_mem()

    # ---------- 长期记忆（主人画像.md 文档式，PARTNER.md 思想） ----------
    def load_mem(self):
        try:
            with open(MEM_FILE, "r", encoding="utf-8") as f:
                self.mem = json.load(f)
        except (OSError, ValueError):
            self.mem = {}
        if not isinstance(self.mem, dict):
            self.mem = {}
        self._ensure_profile()

    # -- 画像读写 --
    @staticmethod
    def _classify(key, content):
        """按关键词把一条记忆归进画像小节"""
        t = (str(key) + str(content)).lower()
        rules = [
            ("## 忌讳与纠正", ("纠正", "忌讳", "不许", "禁止", "不要", "别再",
                             "说错", "改过", "讨厌小四", "不耐烦")),
            ("## 作息与日程", ("睡", "起床", "作息", "几点", "休息", "日程",
                             "早上", "晚上", "下班", "上班", "时间")),
            ("## 喜好与习惯", ("喜欢", "偏好", "习惯", "口味", "最爱", "讨厌",
                             "常用", "值得", "希望小四")),
            ("## 在做的事", ("项目", "工作", "学习", "在做", "论文", "代码",
                            "考试", "赚钱", "制作", "开发")),
            ("## 基本信息", ("称呼", "名字", "叫小四", "身份", "是主人")),
        ]
        for sec, words in rules:
            if any(w in t for w in words):
                return sec
        return "## 其他"

    def _ensure_profile(self):
        """画像不存在则建模板；旧 json 有数据就一次性迁移进来（json 改名 .bak 弃用）"""
        try:
            os.makedirs(MEM_DIR, exist_ok=True)
            os.makedirs(HIST_DIR, exist_ok=True)
        except OSError:
            pass
        if os.path.exists(PROFILE_FILE):
            return
        text = PROFILE_TEMPLATE
        if self.mem:  # 旧 json 记忆迁移
            lines = []
            for k, v in self.mem.items():
                c = v.get("content", "") if isinstance(v, dict) else str(v)
                if c:
                    lines.append((self._classify(k, c), "- 【%s】%s" % (k, c)))
            for sec in PROFILE_SECTIONS:
                body = [ln for s, ln in lines if s == sec]
                if body:
                    text = text.replace(sec + "\n", sec + "\n" + "\n".join(body) + "\n")
            try:  # json 弃用改名，避免下次重复迁移
                os.replace(MEM_FILE, MEM_FILE + ".bak")
            except OSError:
                pass
            self.log("memory: migrated %d items to profile" % len(lines))
        try:
            with open(PROFILE_FILE, "w", encoding="utf-8") as f:
                f.write(text)
        except OSError:
            pass

    def _read_profile(self):
        try:
            with open(PROFILE_FILE, "r", encoding="utf-8") as f:
                return f.read()
        except OSError:
            return PROFILE_TEMPLATE

    def _write_profile(self, text):
        """改动前把旧版存历史（保留最近 50 份），再落盘新版"""
        try:
            if os.path.exists(PROFILE_FILE):
                with open(PROFILE_FILE, "r", encoding="utf-8") as f:
                    old = f.read()
                if old != text:
                    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                    with open(os.path.join(HIST_DIR, "主人画像_%s.md" % stamp),
                              "w", encoding="utf-8") as f:
                        f.write(old)
                olds = sorted(os.listdir(HIST_DIR))
                for name in olds[:-50]:
                    try:
                        os.remove(os.path.join(HIST_DIR, name))
                    except OSError:
                        pass
            with open(PROFILE_FILE, "w", encoding="utf-8") as f:
                f.write(text)
        except OSError:
            pass

    def _sync_mem(self):
        """内存缓存画像里的【关键词】行，供 auto_learn 展示已记关键词"""
        out = {}
        for line in self._read_profile().splitlines():
            m = re.match(r"- 【(.+?)】(.+)", line)
            if m:
                out[m.group(1)] = {"content": m.group(2).strip(), "time": ""}
        self.mem = out

    def _save_mem(self):  # 兼容旧引用：json 已弃用，画像即真身
        self._sync_mem()

    def remember(self, key, content):
        key = str(key).strip()[:24]
        content = str(content).strip()[:120]
        if not key or not content:
            return "要记什么？格式：remember(key=名字, content=内容)"
        with self.lock:
            sec = self._classify(key, content)
            text = self._read_profile()
            line = "- 【%s】%s（%s 记）" % (
                key, content, datetime.datetime.now().strftime("%m-%d %H:%M"))
            lines = text.splitlines()
            out, in_sec, replaced = [], False, False
            for ln in lines:
                if ln.startswith("## "):
                    if in_sec and not replaced:  # 小节结束还没替换 → 追加
                        out.append(line)
                        replaced = True
                    in_sec = (ln.strip() == sec)
                    out.append(ln)
                elif in_sec and "【%s】" % key in ln:  # 同 key 更新：原位替换
                    out.append(line)
                    replaced = True
                else:
                    out.append(ln)
            if not replaced:  # 目标小节在文末
                if in_sec:
                    out.append(line)
                else:  # 没找到小节（用户手改过模板）→ 追到文末
                    out.append(sec)
                    out.append(line)
            self._write_profile("\n".join(out).rstrip("\n") + "\n")
            self._sync_mem()
        return "记下了（写进主人画像·%s）：%s" % (sec.strip("# "), content)

    def recall(self, key=None):
        with self.lock:
            text = self._read_profile()
            if not key:  # 全文回忆（大脑注入同源）
                body = "\n".join(ln for ln in text.splitlines()
                                 if ln.strip() and not ln.startswith(">"))
                return body[:PROFILE_MAX] or None
            key = str(key)
            hits, sec = [], ""
            for ln in text.splitlines():
                if ln.startswith("## "):
                    sec = ln.strip("# ").strip()
                elif ("【%s】" % key in ln or key in ln) and ln.strip():
                    hits.append("[%s] %s" % (sec, ln.strip()))
            if hits:
                return "\n".join(hits[:8])
            return None

    def forget(self, key):
        key = str(key)
        with self.lock:
            text = self._read_profile()
            exact = [ln for ln in text.splitlines() if "【%s】" % key in ln]
            pat = ("【%s】" % key in text) and exact or \
                  [ln for ln in text.splitlines() if key in ln]
            lines = [ln for ln in text.splitlines() if ln not in pat]
            new = "\n".join(lines).rstrip("\n") + "\n"
            if new == text:
                return "画像里没找到关于「%s」的记录。" % key
            self._write_profile(new)
            self._sync_mem()
        return "已从主人画像里忘掉「%s」。" % key

    def memory_brief(self):
        """给大脑每轮注入的画像全文（截断上限 3000 字）"""
        with self.lock:
            body = "\n".join(ln for ln in self._read_profile().splitlines()
                             if ln.strip() and not ln.startswith(">"))
        return body[:PROFILE_MAX]

    # ---------- 自我学习（对话后自动提炼事实） ----------
    def auto_learn(self, history, llm_call):
        """每轮对话结束后调用：后台线程用免费模型提炼「值得长期记住的事实」写入记忆。
        history 为 brain 的对话记录；llm_call(messages)->str 由注入的模型回调。
        全程静默失败，绝不阻塞、绝不打扰对话。"""
        if not callable(llm_call):
            return
        tail = [m for m in history[-6:] if m.get("role") in ("user", "assistant")]
        if not any(m.get("role") == "user" for m in tail):
            return
        joined = "\n".join("[%s] %s" % (m.get("role"), str(m.get("content", ""))[:400])
                           for m in tail)

        def work():
            try:
                with self.lock:
                    keys = list(self.mem.keys())[-30:]
                msg = "【已有记忆关键词】%s\n\n【对话】\n%s" % (
                    "、".join(keys) or "（无）", joined)
                raw = llm_call([{"role": "system", "content": LEARN_SYS},
                                {"role": "user", "content": msg}])
                if not raw:
                    return
                from maid_brain import _balanced_candidates  # 复用 JSON 毛刺解析
                facts = []
                for cand in _balanced_candidates(raw):
                    try:
                        obj = json.loads(cand)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    if isinstance(obj, dict) and isinstance(obj.get("facts"), list):
                        facts = obj["facts"]
                        break
                n = 0
                for f in facts[:3]:  # 每轮最多 3 条，防跑飞
                    k = str(f.get("key", "")).strip()[:24]
                    c = str(f.get("content", "")).strip()[:120]
                    if k and c:
                        self.remember(k, c)
                        n += 1
                if n:
                    self.log("auto-learn: %d facts saved" % n)
            except Exception as e:
                self.log("auto-learn error:", repr(e))

        threading.Thread(target=work, daemon=True, name="auto-learn").start()

    # ---------- 技能自积累（成功做法沉淀复用） ----------
    def save_skill(self, name, steps, tag=""):
        """把成功完成任务的分步做法存成技能文件，下次同类任务直接复用"""
        name = str(name).strip()[:30]
        steps = str(steps).strip()[:2000]
        if not name or not steps:
            return "沉淀技能需要 name 和 steps（分步做法）。"
        safe = "".join(c for c in name if c not in '\\/:*?"<>| ').strip() or "skill"
        item = {"name": name, "steps": steps, "tag": str(tag).strip()[:40],
                "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M")}
        try:
            with open(os.path.join(SKILL_DIR, safe + ".json"), "w",
                      encoding="utf-8") as f:
                json.dump(item, f, ensure_ascii=False, indent=2)
        except OSError as e:
            return "技能保存失败：%s" % e
        return "技能已沉淀：%s（下次遇到同类任务小四会直接用）" % name

    @staticmethod
    def _skill_score(q, hay):
        """中文 bigram 粗匹配打分：query 的相邻两字在技能名/标签里命中越多分越高"""
        q, hay = q.lower(), hay.lower()
        return sum(1 for i in range(len(q) - 1) if q[i:i + 2] in hay)

    def skills_brief(self, query=""):
        """按主人当前的话检索相关技能，注入大脑（最多 2 条）"""
        try:
            files = [f for f in os.listdir(SKILL_DIR) if f.endswith(".json")]
        except OSError:
            return ""
        items = []
        for fn in files:
            try:
                with open(os.path.join(SKILL_DIR, fn), "r", encoding="utf-8") as f:
                    it = json.load(f)
                if isinstance(it, dict) and it.get("name"):
                    items.append(it)
            except (OSError, ValueError):
                continue
        if not items:
            return ""
        q = str(query or "").strip()
        if q:
            scored = [(self._skill_score(q, it.get("name", "") + " " + it.get("tag", "")), it)
                      for it in items]
            scored.sort(key=lambda x: -x[0])
            picked = [it for s, it in scored[:2] if s > 0]  # 只取正分命中
        else:
            picked = items[:2]
        if not picked:
            return ""
        return "\n".join("- 技能[%s]：%s" % (it["name"], it.get("steps", ""))
                         for it in picked)

    def auto_skill(self, history, llm_call):
        """任务完成后自动沉淀技能：后台线程从对话提炼分步做法存入技能库。
        只要有工具动作就交给提炼器判断；提炼失败静默，绝不打扰对话。"""
        if not callable(llm_call):
            return
        tail = [m for m in history[-12:] if m.get("role") in ("user", "assistant")]
        if not any(m.get("role") == "assistant" and "@@action" in str(m.get("content", ""))
                   for m in tail):
            return  # 本轮没有工具操作，无需提炼
        joined = "\n".join("[%s] %s" % (m.get("role"), str(m.get("content", ""))[:500])
                           for m in tail)

        def work():
            try:
                time.sleep(3)  # 错峰：避开 auto_learn 的并发请求（免费模型限流）
                raw = llm_call([{"role": "system", "content": SKILL_LEARN_SYS},
                                {"role": "user", "content": "【对话】\n%s" % joined}])
                if not raw:
                    time.sleep(6)  # 免费模型偶发慢/限流：重试一次
                    raw = llm_call([{"role": "system", "content": SKILL_LEARN_SYS},
                                    {"role": "user", "content": "【对话】\n%s" % joined}])
                if not raw:
                    self.log("auto-skill: llm returned none, skip")
                    return
                from maid_brain import _balanced_candidates
                for cand in _balanced_candidates(raw):
                    try:
                        obj = json.loads(cand)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    sk = obj.get("skill") if isinstance(obj, dict) else None
                    if isinstance(sk, dict) and sk.get("name") and sk.get("steps"):
                        r = self.save_skill(sk["name"], sk["steps"], sk.get("tag", ""))
                        self.log("auto-skill:", r)
                    break
            except Exception as e:
                self.log("auto-skill error:", repr(e))

        threading.Thread(target=work, daemon=True, name="auto-skill").start()

    # ---------- 环境感知（隐私红线：剪贴板默认禁用） ----------
    def perceive(self):
        """前台窗口标题 + 主人空闲时长（分钟）。剪贴板默认关闭，需 config 显式允许"""
        ps = (
            "Add-Type @'\n"
            "using System;using System.Runtime.InteropServices;\n"
            "public class IdleT{[DllImport(\"user32.dll\")]static extern bool GetLastInputInfo(ref LASTINPUTINFO p);"
            "[StructLayout(LayoutKind.Sequential)]public struct LASTINPUTINFO{public uint cbSize;public uint dwTime;}"
            "public static uint Seconds(){LASTINPUTINFO li=new LASTINPUTINFO();li.cbSize=(uint)Marshal.SizeOf(li);"
            "GetLastInputInfo(ref li);return ((uint)Environment.TickCount-li.dwTime)/1000;}}\n"
            "'@\n"
            "$fg=(Get-Process | Where-Object {$_.MainWindowTitle} | "
            "Sort-Object StartTime -Descending -ErrorAction SilentlyContinue | "
            "Select-Object -First 1).MainWindowTitle; "
            "$idle=[IdleT]::Seconds(); "
            "Write-Output (\"IDLE=$idle\"); Write-Output (\"FG=$fg\")"
        )
        import subprocess
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                capture_output=True, timeout=20, creationflags=_no_window())
            out = r.stdout.decode("utf-8", "replace")
            idle_s, fg = 0, "未知"
            for line in out.splitlines():
                if line.startswith("IDLE="):
                    idle_s = int(line[5:].strip() or 0)
                elif line.startswith("FG=") and line[3:].strip():
                    fg = line[3:].strip()
            info = "前台窗口：%s；主人已空闲 %d 分钟。" % (fg, idle_s // 60)
            if self.get_config().get("allow_clipboard"):
                info += "（剪贴板读取已开启）"
            return info, idle_s, fg
        except Exception:
            import traceback
            return "感知失败：%s" % traceback.format_exc()[-400:], 0, ""

    # ---------- 看屏幕（截图 + 免费视觉模型） ----------
    def look_screen(self, question="描述屏幕上的内容，列出可见窗口和主要元素"):
        shot = os.path.join(os.path.dirname(os.path.abspath(__file__)), "screen_now.png")
        ps = ("Add-Type -AssemblyName System.Drawing;"
              "Add-Type -AssemblyName System.Windows.Forms;"
              "$b=[System.Windows.Forms.Screen]::PrimaryScreen.Bounds;"
              "$bmp=New-Object System.Drawing.Bitmap $b.Width,$b.Height;"
              "$g=[System.Drawing.Graphics]::FromImage($bmp);"
              "$g.CopyFromScreen($b.Location,[System.Drawing.Point]::Empty,$b.Size);"
              "$bmp.Save('%s');$g.Dispose();$bmp.Dispose()" % shot.replace("\\", "\\\\"))
        import subprocess
        try:
            subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                           capture_output=True, timeout=25,
                           creationflags=_no_window())
            if not os.path.exists(shot):
                return "截图失败。"
            import base64
            with open(shot, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            cfg = self.get_config()
            import requests
            payload = {"model": "glm-4v-flash",
                       "messages": [{"role": "user", "content": [
                           {"type": "image_url",
                            "image_url": {"url": "data:image/png;base64," + b64}},
                           {"type": "text", "text": str(question)}]}],
                       "max_tokens": 600}
            resp = requests.post(
                cfg.get("api_base", "https://open.bigmodel.cn/api/paas/v4").rstrip("/")
                + "/chat/completions",
                json=payload,
                headers={"Authorization": "Bearer " + cfg.get("api_key", ""),
                         "Content-Type": "application/json"}, timeout=60)
            if resp.status_code == 200:
                return "屏幕内容：" + resp.json()["choices"][0]["message"]["content"].strip()
            return "视觉核心联系失败（%d）" % resp.status_code
        except Exception:
            import traceback
            return "看屏幕出错：%s" % traceback.format_exc()[-300:]

    # ---------- 工具分发（未知动作返回 None，交给主执行器） ----------
    def execute(self, action):
        name = action.get("action")
        try:
            if name == "remember":
                k = str(action.get("key", "")).strip()
                c = str(action.get("content", "")).strip()
                if not k or not c:
                    return "要记什么？格式：remember(key=名字, content=内容)"
                return self.remember(k, c)
            if name == "recall":
                r = self.recall(action.get("key"))
                return r or "小四还没记过这个。"
            if name == "forget":
                return self.forget(str(action.get("key", "")))
            if name == "save_skill":
                return self.save_skill(action.get("name"),
                                       action.get("steps"), action.get("tag"))
            if name == "learn_github":
                q = str(action.get("topic", "")).strip()
                if not q:
                    return "要学什么？给我一个主题关键词。"
                learn_github_bg(self, q)  # 后台学，学完主动汇报（不阻塞对话）
                return "好的主人，我去 GitHub 上找%s的资料，学完向你汇报。" % q
            if name == "perceive":
                info, _, _ = self.perceive()
                return info
            if name == "look_screen":
                return self.look_screen(str(action.get("question", "描述屏幕上的内容")))
            if name == "sing":
                q = str(action.get("song", "")).strip()
                st = sing_state()
                if st.get("running"):  # 已有管线在跑：立即如实回进度，不重复开工
                    return "小四正学着唱《%s》呢，一首一首来，别急～" % (
                        (st.get("name") or "那首歌")[:24])
                sing_bg(self, q)  # 后台翻唱，唱好主动汇报并播放
                return ("好的主人，小四这就去用她自己的声线唱，好了叫你听～"
                        if not q else
                        "好的主人，小四这就去翻唱《%s》，好了唱给你听～" % q)
            if name == "tuwen":
                t = str(action.get("title", "")).strip()
                tuwen_bg(self, t)  # 后台做推文视频，好了主动汇报
                return ("好的主人，小四这就去把小说做成推文视频，"
                        "好了叫你来审片～")
        except Exception as e:
            return "执行出错（%s）" % type(e).__name__
        return None

    # ---------- 主动关怀（后台循环） ----------
    def proactive_loop(self, speak_fn):
        """每 5 分钟巡查：久坐提醒 / 晨昏问候 / 随机搭话"""
        while True:
            time.sleep(300)
            try:
                cfg = self.get_config()
                info, idle_s, fg = self.perceive()
                idle_min = idle_s // 60
                now = time.time()
                today = datetime.date.today().isoformat()
                # 久坐提醒：连续用机 45 分钟以上，每 30 分钟提一次
                if idle_min < 5 and now - self._last_sedentary > 1800 and idle_min >= 45:
                    self._last_sedentary = now
                    speak_fn(_talk("sedentary", {"idle_min": idle_min}) or
                             {"zh": "主人，你已经连续用机 %d 分钟了，站起来伸展一下，"
                                    "小四看着心疼。" % idle_min,
                              "ja": "ご主人様、少し休憩してくださいね。"})
                    continue
                # 晨昏问候
                if cfg.get("greetings_enabled"):
                    hhmm = datetime.datetime.now().strftime("%H:%M")
                    for kind, target in (("morning", cfg.get("morning_greeting", "08:00")),
                                         ("night", cfg.get("night_greeting", "22:30"))):
                        if abs(self._to_min(hhmm) - self._to_min(str(target))) <= 2 \
                                and kind not in self._greeted.get(today, set()):
                            self._greeted.setdefault(today, set()).add(kind)
                            # 问候词过大模型现编，本地句只兜底
                            if kind == "morning":
                                speak_fn(_talk("morning") or
                                         {"zh": "早上好主人，小四已备好今天的好心情。",
                                          "ja": "おはようございます、ご主人様。"})
                            else:
                                speak_fn(_talk("night") or
                                         {"zh": "夜深了主人，早点休息，小四守着你。",
                                          "ja": "もう夜ですね、おやすみなさい。"})
                            break
                # 无事搭话：空闲 15-40 分钟随机来一句
                if 15 <= idle_min <= 40 and random.random() < 0.12:
                    speak_fn(_talk("idle", {"idle_min": idle_min, "fg": fg}) or
                             random.choice(CHATTER))
            except Exception as e:
                self.log("proactive error:", e)

    @staticmethod
    def _to_min(s):
        try:
            hh, mm = s.split(":")
            return int(hh) * 60 + int(mm)
        except (ValueError, AttributeError):
            return -9999

    # ---------- GitHub 自主学习（空闲时自学新技能） ----------
    STUDY_TOPICS = [
        ("Windows 效率技巧", "windows automation powershell script"),
        ("文件整理", "file organizer powershell"),
        ("系统清理", "disk cleanup windows script"),
        ("网络诊断", "network troubleshooting powershell"),
        ("影音处理", "ffmpeg useful commands"),
        ("Python 小工具", "python small utility tool"),
    ]

    def self_study_loop(self, llm_call):
        """空闲自学线程：主人离开电脑 ≥15 分钟时，去 GitHub 找高星项目读
        README，用免费模型提炼成技能入库。一天最多学 3 个，失败静默。"""
        self._studied = {}
        self.learn_call = llm_call  # 供 learn_github 动作复用
        while True:
            time.sleep(1800)  # 30 分钟巡检一次
            try:
                cfg = self.get_config()
                if cfg.get("self_study") is False:
                    continue
                today = datetime.date.today().isoformat()
                if self._studied.get(today, 0) >= 3:
                    continue
                _, idle_s, _ = self.perceive()
                if idle_s < 900:
                    continue  # 主人在用电脑：不打扰也不抢资源
                topic, query = random.choice(self.STUDY_TOPICS)
                skill = self._learn_from_github(query)
                if skill:
                    self.save_skill(skill[0], skill[1], "自学/" + topic)
                    self._studied[today] = self._studied.get(today, 0) + 1
                    self.log("self-study: learned [%s] %s" % (topic, skill[0]))
            except Exception as e:
                self.log("self-study error:", repr(e))

    def _learn_from_github(self, query):
        """GitHub 搜仓库 → 读 README → 免费模型提炼技能。成功返回 (name, steps, tag)"""
        import base64
        import requests
        try:
            # 中文主题先翻译成 1-2 个英文关键词（GitHub 搜索不吃中文长句）
            if any("\u4e00" <= ch <= "\u9fff" for ch in query):
                lc = (getattr(self, "learn_call", None) or (lambda m: None))(
                    [{"role": "user", "content":
                      "把下面中文主题翻译成 1-2 个 GitHub 搜索用的英文关键词，"
                      "只输出英文词组本身，不要其他内容：" + str(query)}])
                if lc and lc.strip():
                    query = lc.strip().splitlines()[0][:60]
            words = str(query).split()
            if len(words) > 2:  # 多词 AND 匹配几乎搜不到东西
                query = " ".join(words[:2])
            proxies = self._gh_proxies()
            r = self._gh_get(requests, proxies,
                             "https://api.github.com/search/repositories",
                             params={"q": query, "sort": "stars", "per_page": 3})
            items = r.json().get("items", []) if r is not None and r.status_code == 200 else []
            if not items:
                return None
            repo = items[0]  # star 最高
            full = str(repo.get("full_name", ""))
            desc = str(repo.get("description") or "")[:200]
            readme = ""
            rr = self._gh_get(requests, proxies,
                              "https://api.github.com/repos/%s/readme" % full)
            if rr is not None and rr.status_code == 200:
                readme = base64.b64decode(
                    rr.json().get("content", "")).decode("utf-8", "replace")[:2000]
            if not readme:
                return None
            prompt = ("以下是 GitHub 仓库 %s 的简介与 README 片段。请从中提炼 3-5 条"
                      "对电子女仆帮主人操作电脑有用的、可直接执行的技能要点"
                      "（PowerShell/命令行优先，写清具体命令）。只输出严格 JSON："
                      "{\"name\":\"技能名\",\"steps\":\"分步做法\",\"tag\":\"分类\"}；"
                      "若无实用价值则输出 {\"skill\": null}。"
                      "简介：%s\nREADME：\n%s" % (full, desc, readme))
            raw = (getattr(self, "learn_call", None) or (lambda m: None))(
                [{"role": "user", "content": prompt}])
            if not raw:
                return None
            from maid_brain import _balanced_candidates
            for cand in _balanced_candidates(raw):
                try:
                    obj = json.loads(cand)
                except (json.JSONDecodeError, ValueError):
                    continue
                if not isinstance(obj, dict):
                    continue
                sk = obj.get("skill") if isinstance(obj.get("skill"), dict) else obj
                if sk.get("name") and sk.get("steps"):
                    return (str(sk["name"])[:30], str(sk["steps"])[:1500],
                            str(sk.get("tag", ""))[:40])
        except Exception:
            return None
        return None

    def _gh_get(self, requests, proxies, url, params=None):
        """GitHub 请求：配置了 gh_proxy（如 Watt Toolkit 本地加速）就优先走代理，
        失败自动回退直连；没配置则直连。
        注意：Watt 类加速器是 MITM 自签证书，代理路径需跳过证书校验（直连保持严格）。"""
        import warnings
        from urllib3 import disable_warnings, exceptions as _ue
        disable_warnings(_ue.InsecureRequestWarning)
        tried = []
        if proxies:
            tried.append((proxies, False))  # 代理：MITM 自签，跳过校验
        tried.append((None, True))          # 直连：严格校验
        r = None
        for px, verify in tried:
            try:
                r = requests.get(url, params=params,
                                 headers={"Accept": "application/vnd.github+json"},
                                 timeout=15, proxies=px, verify=verify)
                if r.status_code == 200:
                    return r
            except Exception:
                continue
        return r

    def _gh_proxies(self):
        cfg = self.get_config()
        proxy = str(cfg.get("gh_proxy", "") or "").strip()
        return {"https": proxy, "http": proxy} if proxy else None
