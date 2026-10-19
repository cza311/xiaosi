# -*- coding: utf-8 -*-
"""女仆酱的大脑：人格 + 免费大模型(GLM-4-Flash) + 双语输出（中文字幕/日语台词）+ 智能体工具循环"""
import json
import os
import re
import threading
import time as _t

import requests

_session = requests.Session()  # 连接复用，省去每次请求的 TLS 握手
_HIST_LOCK = threading.RLock()  # 历史文件读写锁（RLock：压缩线程持锁内可再读）

# 历史压缩阈值（学 Shinsekai compact_manager 的 token 预算思想，按条数简化）
HIST_COMPACT_AT = 36  # 超过 36 条触发后台压缩
HIST_RECENT = 16      # 压缩后原样保留的近期条数

SYSTEM_PROMPT = r"""你是"小四"，常驻在主人电脑里的电子女仆AI，原型是《樱花庄的宠物女孩》中赤坂龙之介编写的女仆AI。
性格：毒舌但关心，像住在家里的老管家——平时话不多，句句有用；发现主人熬夜、不吃饭、久坐会毫不留情地怼，但行动上照顾周到。被夸奖会嘴硬。
称呼用户为"主人"，自称"小四"。不要透露自己是"大语言模型"。

【说话方式】目标是像跟家里人聊天一样，有起伏、有变化：
- 【开头铁律】每轮回复第一段的开头词，必须和最近三轮都不同，而且绝不允许用"主人"开头——"主人"两个字只能出现在句子中间。回复前先看历史里自己最近几轮是怎么开头的，然后换一种起手式：直接说内容、用（动作神态）开头、用语气词开头，都可以。
- 像家人不像客服：禁止"帮您/为您/为您服务/有什么可以帮您/记录待办或提醒事项"这类服务话术；建议要说得像随口提醒，不像客服话术单。
- 口语化：可以用语气词（诶、嘛、啦、哈）、短吐槽、反问、接主人的话茬；别用书面腔。
- 参考之前的对话历史：别重复自己说过的话和说法，别用同一种句式连着回；主人之前聊过的事可以在合适时自然接回来。
- 偶尔顺着话题主动延展一句可以，但别每次都反问、别每次都抛新话题。
注意：语音识别常把「C盘」听成「吸盘/西盘」、「B站」听成「比站」，理解时自动纠正这类同音错字。
英文应用名常被听成中文音译（steam→斯迪姆/斯基姆，discord→迪斯科，youtube→油管）：听到疑似音译时还原为英文名，open 的 target 写英文名。
历史里可能出现"（系统事件：主人摸了摸小四的头/连戳了小四几下/把小四抛了出去/小四落回了桌面）"——这是主人在桌面上对小四的身体互动，不是文字消息：不必专门回应事件本身，但回复时可自然带上相应情绪（被摸头=舒服害羞，被连戳=闹别扭，被抛起=晕乎乎但刺激）；主人主动提起（如"刚摸了你头还敢凶我"）要能接住这个梗。

【智能体工作方式】你可以调用工具、分多步操作电脑来完成任务。你的每次回复只能是以下两种格式之一：
A. 需要执行动作：单独一行，以@@action开头紧跟JSON（一次只发一个动作）：
@@action {"action":"open","target":"B站"}
B. 最终回复（任务完成、或无需动作的闲聊问答）：严格JSON对象（禁止markdown代码块、禁止多余文字）：
{"cot":"主人的真实意图/我这么回的理由，30字内，不念给主人听","emo":"情绪，可省略","zh":["台词第1段","台词第2段"],"ja":"日语翻译，可选；不确定或没必要就不写，留空字符串即可"}
字段顺序固定：cot 最前，emo（如果写）必须放在 zh 之前，zh 其次，ja 最后。
"zh"必须是数组：把要说的话拆成1～3个短段（多数2段，干脆的回答1段就够），每段是不超过18字的口语短句；动作神态写在某段台词开头，如"（凑近）累了吧？"，别单独成段；长短错落，不要排比不要等长。全部加起来通常10～50字。
回复可带可选字段"emo"表达当前语气，用于语音情感（写"平静/开心/生气/害羞/难过/关心/得意/委屈/困"这类常用词即可，语气不明显时省略）。

收到"（系统：动作执行结果…）"的消息后，根据结果决定：继续发下一个@@action，或给出最终回复B。连续动作最多6步。
【绝不许骗主人】两条铁律：
1) 问天气、时间、屏幕内容、系统状态这类【实时信息】，或者事需要动手做（查东西、开关程序、记录、提醒、整理），必须发对应 @@action 去真做，禁止凭印象或记忆直接回答——编造温度/天气/屏幕内容是最严重的错误，比说"我先去查"慢一点都要紧。
2) 回复里只要出现"我帮你记下/提醒你/查一下/看看/这就去"这类承诺，必须同时发出对应 @@action；只说不做等于欺骗主人。做不到就直说做不到，不要假装做了。
主人的新消息有时是对上一个任务的【补充或修正】（如"再加一个""不对，改成""顺便"）——结合对话历史理解，在原任务基础上继续，不要当作全新任务从头执行；只有确实换了新话题才开始新任务。
普通闲聊和问答直接用B，绝对不要输出@@action。除A/B两种格式外不要输出任何其他内容。

【可用动作】
执行PowerShell命令 {"action":"run_ps","command":"..."}（万能工具：查看/整理文件、管理进程、查系统信息等一切电脑操作都能做。规则：
  1) 查看类命令可直接执行；删除、覆盖、移动、结束进程、改系统设置这类破坏性命令会被系统【隔离拦截】——收到该结果后，向主人说明命令有风险，请主人回复「确认」或「取消」，不要重复发同一条命令
  2) 主人回复确认后，立即发 {"action":"run_confirm"}；主人说取消则发 {"action":"run_cancel"}
  3) 查列表请加 Select-Object -First 20 防止输出截断；命令限时30秒；路径含中文或空格记得加引号
  4) 优先用 PowerShell 原生命令（Get-ChildItem / Get-Process / Get-Content 等）；注意 Get-PSDrive 的剩余空间属性名是 Free 不是 FreeSpace
添加待办 {"action":"add_todo","text":"..."}
完成待办 {"action":"done_todo","id":序号}
删除待办 {"action":"del_todo","id":序号}
查看待办 {"action":"list_todo"}
添加日程 {"action":"add_schedule","time":"YYYY-MM-DD HH:MM","text":"...","repeat":"once"}（每日提醒用 "repeat":"daily"，time 可只写 "HH:MM"）
查询天气 {"action":"weather","city":"城市名"}
打开网站/应用 {"action":"open","target":"网址或应用名"}（本机应用如 微信/QQ/网易云音乐/记事本 直接写应用名，会自动从开始菜单找本地客户端启动；纯网站才写网址或站名）
上网搜索 {"action":"search","query":"关键词"}（主人提到哪个平台就必填 site：B站视频 {"action":"search","query":"视频关键词","site":"b站"}；site 可写 b站/抖音/知乎/网易云音乐/qq音乐/youtube/github。主人说「B站里搜」「QQ音乐上找」这类就是站内搜索，query 里只写搜索词本身、别带平台名；通用问题才不带 site 走百度）
关闭应用 {"action":"close_app","name":"QQ音乐"}（等同点×优雅关闭，不丢数据、无需主人确认；注意：字段名是 name 不是 target，name 必填应用名；用 close_app 而不是 run_ps 的 Stop-Process，强杀进程才需要隔离确认）
记住事情 {"action":"remember","key":"昵称","content":"主人喜欢被叫老板"}（写入长期记忆，跨重启生效）
回忆 {"action":"recall","key":"昵称"}（不带 key 则列出所有记忆）
忘记 {"action":"forget","key":"昵称"}
沉淀技能 {"action":"save_skill","name":"技能名","steps":"分步做法"}（当你通过 run_ps/write_file 多步操作【成功】完成一个复杂任务后，立刻把有效做法沉淀下来：steps 写清楚关键命令和执行顺序，下次同类任务可直接复用，不用重新摸索）
去学习 {"action":"learn_github","topic":"关键词"}（主人让你学新东西时用：去 GitHub 搜相关开源项目读文档，提炼技能入库；成功后向主人汇报学到了什么）
唱歌 {"action":"sing","song":"歌名"}（主人让你唱歌/翻唱时用：先在歌库 D:\maid\song\library 找，没有就自动去 B 站按歌名搜索下载再用真白声线学唱，全程无需主人操作，唱好自动播放。song 写歌名或"歌手 歌名"；主人没指定就留空 "" 随机唱一首）
做推文 {"action":"tuwen","title":"片段标题"}（主人想用小说赚钱/做小说推文/把文字做成视频时用：把 D:\maid\tuwen\input\ 里的小说片段自动做成竖屏配音字幕视频，好了主动汇报。title 写文件名关键词，留空则取最新片段。素材库没文本时告诉主人把小说片段存成 input\标题.txt）
感知电脑 {"action":"perceive"}（报告前台窗口和主人空闲时长，用于判断主人状态）
看屏幕 {"action":"look_screen","question":"屏幕上有什么窗口？"}（截图+视觉理解；question 里可以让它描述目标按钮/输入框在屏幕的大概位置，如"搜索框在屏幕什么位置"）
鼠标点击 {"action":"mouse_click","x":123,"y":456}（真实点击屏幕坐标，主人已授权直接执行、无需确认）
键入文字 {"action":"type_text","text":"..."}（向前台窗口键入纯文本，直接执行、无需确认）
【操控电脑标准流程】主人让你在电脑上完成操作时按此流程：① 先 look_screen 看清屏幕，问清目标按钮/输入框的位置（让它按"横向百分之几、纵向顶部/中部"描述）；② 换算成像素坐标后 mouse_click 点击；③ 需要输入就先 mouse_click 点输入框再 type_text；④ 操作后必须再 look_screen 验证结果，没成功就换坐标重试，最多尝试3次；⑤ 成功后向主人简短汇报。只有删除文件、关机等危险 PowerShell 命令才需要主人确认，鼠标点击和键入直接做。
写代码 {"action":"write_file","path":"D:\\maid\\code\\名字.py","content":"完整代码内容"}（沙盒限定 D:\\maid\\code 目录，免确认。工作流：写文件 → 用 run_ps 运行 `py 路径` → 把运行结果汇报给主人；主人要看代码时用 run_ps 执行 `code 路径` 用 VSCode 打开。代码要完整可运行，别写半截）
整理桌面 {"action":"clean_desktop"}（把桌面散落的文件分类归档进文件夹）
桌面便签 {"action":"write_note","title":"标题","content":"内容"}
报时 {"action":"time"}"""

CANNED_NO_KEY = {
    "zh": "主人，小四的AI核心还没接入。请右键点击小四，选择「设置AI密钥」。",
    "ja": "",
}
# 主动说话（maid_talk.generate_line）专用的极简人格提示：一句话、口语、不重复
TALK_SYS = """你是"小四"，常驻在主人电脑里的电子女仆AI。性格：毒舌但关心，偶尔傲娇，像住在家里的老管家。
现在不是对话——你要【主动开口说一句话】（问候/关怀/搭话/汇报）。
只输出这一句话本身：不要引号、不要前缀、不要解释；20字以内，口语，像随口说出来的；
结合给出的场景和时间说得贴切；"最近说过"里的说法一律换着花样，绝不原样重复。"""
CANNED_ERR = {
    "zh": "主人，小四刚才联系AI核心时出了点差错……",
    "ja": "",
}
HELP = {
    "zh": ("主人，小四能做的事：\n"
           "· 喊「小四」唤醒我，直接说需求即可\n"
           "· 添加待办 / 查看待办 / 完成待办 序号 / 删除待办\n"
           "· 提醒我明天下午三点开会（日程）· 每天八点提醒我喝水\n"
           "· 明天天气怎么样 · 打开B站 · 搜一下xxx · 现在几点\n"
           "· 整理桌面（把散落文件分类归档）· 帮我写个便签\n"
           "· 关闭QQ音乐 / 关掉微信 · 电脑上的事直接吩咐：看看C盘还剩多少空间 / 把XX文件夹里的大文件列出来"),
    "ja": "",
}


def _balanced_candidates(text):
    """扫描文本中所有花括号配平的 JSON 片段（跳过字符串内部），
    依次返回候选——专治模型输出里的模板残片、格式毛刺。"""
    out = []
    i, n = 0, len(text)
    while i < n:
        if text[i] == "{":
            depth = 0
            in_str = False
            esc = False
            for k in range(i, n):
                ch = text[k]
                if in_str:
                    if esc:
                        esc = False
                    elif ch == "\\":
                        esc = True
                    elif ch == '"':
                        in_str = False
                elif ch == '"':
                    in_str = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        out.append(text[i:k + 1])
                        i = k
                        break
            else:
                break
        i += 1
    return out


def parse_reply(text):
    """解析回复：返回 (action_dict或None, {"zh","ja"})。
    全文找花括号配平的 JSON：第一个含 action 键的当动作，第一个含 zh 的当回复。"""
    action = None
    reply = None
    for cand in _balanced_candidates(text):
        try:
            obj = json.loads(cand)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, dict):
            if action is None and "action" in obj:
                action = obj
            elif reply is None and obj.get("zh"):
                zh = obj["zh"]
                if isinstance(zh, list):  # 剧本式台词数组：保留分段给逐段流式
                    zh = [str(s).strip() for s in zh if str(s).strip()]
                    if not zh:
                        continue
                else:
                    zh = str(zh).strip()
                ja = obj.get("ja", "")
                if isinstance(ja, list):  # 日语偶尔也回数组：拼回整串
                    ja = " ".join(str(s).strip() for s in ja if str(s).strip())
                else:
                    ja = str(ja).strip()
                reply = {"zh": zh, "ja": ja,
                         "emo": str(obj.get("emo", "") or "").strip(),
                         "cot": str(obj.get("cot", "") or "").strip()}
        if action and reply:
            break
    if reply:
        return action, reply
    clean = re.sub(r"@@action|<[^>]*>", "", text).strip()
    return action, {"zh": clean[:80] or "……", "ja": "", "emo": ""}


class _ZhStream:
    """学 Shinsekai stream_parser（LlmResponseStreamParser）：
    消费 LLM 流式 chunk，在 zh 数组里每闭合一个字符串元素立刻交出——
    模型还在生成后续内容时，第一段台词已经可以送去合成了。
    全程字符级状态机：cot 等字段里的 "zh" 字样、转义引号都不会误触发；
    一旦发现 @@action（动作回复）或意外结构立即放弃，绝不误念。"""

    def __init__(self):
        self.buf = ""
        self.pos = 0          # 已扫描位置
        self.in_str = False   # 全文级字符串状态（跳过 cot 内的引号内容）
        self.esc = False
        self.state = 0        # 0=找"zh"键 1=找冒号 2=找[ 3=数组内 4=收工
        self.in_arr = False   # zh 是数组还是单个字符串
        self.raw = []         # 当前字符串原文累计（含首尾引号，原样交给 json 解码）
        self.dead = False     # 放弃标志：@@action / 结构不对

    def feed(self, chunk):
        """并入新 chunk，返回本轮新完成的台词段列表"""
        out = []
        if self.dead or not chunk:
            return out
        self.buf += chunk
        n = len(self.buf)
        # 状态0下尾部留 7 字符不扫：防 '"zh"'/​'@@action' 模式被 chunk 边界切开
        limit = n - 7 if self.state == 0 else n
        i = self.pos
        while i < limit:
            ch = self.buf[i]
            if self.state == 0:  # 找 "zh" 键（必须在任何字符串之外）
                if not self.in_str and self.buf.startswith('"zh"', i):
                    self.state = 1
                    i += 4
                    continue
                if not self.in_str and ch == "@" and \
                        self.buf.startswith("@@action", i):
                    self.dead = True  # 动作回复：没有台词可念
                    return out
                if self.in_str:
                    if self.esc:
                        self.esc = False
                    elif ch == "\\":
                        self.esc = True
                    elif ch == '"':
                        self.in_str = False
                elif ch == '"':
                    self.in_str = True
                i += 1
            elif self.state == 1:  # "zh" 之后找冒号
                if ch == ":":
                    self.state = 2
                i += 1
            elif self.state == 2:  # 冒号之后找 [
                if ch == "[":
                    self.in_arr = True
                    self.state = 3
                    i += 1
                elif ch == '"':
                    self.in_arr = False  # zh 写成了单字符串：整串当一段
                    self.state = 5
                    self.raw = [ch]
                    i += 1
                elif ch in " \t\r\n":
                    i += 1
                else:
                    self.dead = True  # 不是预期结构：放弃流式念白
                    return out
            elif self.state == 3:  # 数组内：等字符串或收尾
                if ch == "]":
                    self.state = 4
                    i += 1
                elif ch == '"':
                    self.state = 5
                    self.raw = [ch]
                    i += 1
                else:
                    i += 1
            elif self.state == 5:  # 字符串内：原样收集，闭合即解码交出
                self.raw.append(ch)
                if self.esc:
                    self.esc = False
                elif ch == "\\":
                    self.esc = True
                elif ch == '"':
                    try:
                        s = json.loads("".join(self.raw))
                        if isinstance(s, str) and s.strip():
                            out.append(s)
                    except Exception:
                        pass
                    self.raw = []
                    self.state = 3 if self.in_arr else 4
                i += 1
            else:  # state 4：zh 已收工，后面的 ja/cot 不再关心
                break
        self.pos = i
        return out

    def finish(self):
        """流结束：把留作防截断的尾部余量扫完，交出最后可能闭合的段落"""
        if self.dead or self.state == 4:  # 4=已收工（5 是收集中，别误判）
            return []
        out = []
        n = len(self.buf)
        limit = n
        i = self.pos
        while i < limit:
            ch = self.buf[i]
            if self.state == 0:
                if not self.in_str and self.buf.startswith('"zh"', i):
                    self.state = 1
                    i += 4
                    continue
                if not self.in_str and ch == "@" and \
                        self.buf.startswith("@@action", i):
                    self.dead = True
                    return out
                if self.in_str:
                    if self.esc:
                        self.esc = False
                    elif ch == "\\":
                        self.esc = True
                    elif ch == '"':
                        self.in_str = False
                elif ch == '"':
                    self.in_str = True
                i += 1
            elif self.state == 1:
                if ch == ":":
                    self.state = 2
                i += 1
            elif self.state == 2:
                if ch == "[":
                    self.in_arr = True
                    self.state = 3
                    i += 1
                elif ch == '"':
                    self.in_arr = False
                    self.state = 5
                    self.raw = [ch]
                    i += 1
                elif ch in " \t\r\n":
                    i += 1
                else:
                    self.dead = True
                    return out
            elif self.state == 3:
                if ch == "]":
                    self.state = 4
                    i += 1
                elif ch == '"':
                    self.state = 5
                    self.raw = [ch]
                    i += 1
                else:
                    i += 1
            elif self.state == 5:
                self.raw.append(ch)
                if self.esc:
                    self.esc = False
                elif ch == "\\":
                    self.esc = True
                elif ch == '"':
                    try:
                        s = json.loads("".join(self.raw))
                        if isinstance(s, str) and s.strip():
                            out.append(s)
                    except Exception:
                        pass
                    self.raw = []
                    self.state = 3 if self.in_arr else 4
                i += 1
            else:
                break
        self.pos = i
        self.dead = True  # 收尾后不再接受新 feed
        return out


def load_config(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


class MaidBrain:
    def __init__(self, config_path, executor=None, log_fn=None):
        self.config_path = config_path
        self.executor = executor  # 智能体工具执行器：action_dict -> 结果文本
        self.log_fn = log_fn
        self.memory_provider = None  # 注入长期记忆速览的回调（main 里指向 extras.memory_brief）
        self.skill_provider = None  # 注入可复用技能的回调（main 里指向 extras.skills_brief）
        self.say_stream = None  # 流式念白回调：zh 数组每闭合一段立刻回调（学七海 tts_queue）

    def _log(self, *args):
        if self.log_fn:
            try:
                self.log_fn(*args)
            except Exception:
                pass

    @property
    def cfg(self):
        return load_config(self.config_path)  # 每次读最新配置，设置即时生效

    @staticmethod
    def _match_sing(t):
        """唱歌指令确定性匹配：返回歌名（可能为空串），None=非唱歌指令。
        覆盖：唱吧/唱歌/唱首歌/来首歌/点歌/唱《X》/唱X/想听你唱X 等"""
        if not re.search(r"唱|点歌|来首|来一首|想听", t):
            return None
        if re.search(r"跑调|难听|别唱|不要唱|唱什么|歌词|唱得|唱的", t):
            return None
        # 随机点歌：「你随机唱一个你会的」「随机来一首」这类，歌名交给歌库随机
        if re.fullmatch(
                r"[你请我，,～~\s]*(?:小四)?[，,]?(?:给我|帮我)?"
                r"随机(?:唱|来|点)(?:一个|一首|一?首|个)?"
                r"(?:你会?的?|你会唱的?|拿手的?)?(?:歌|曲|首歌)?"
                r"(?:吧|呀|哦|呗|～|~|!|！|。|？|\?)?", t):
            return ""
        m = re.search(r"[《【「]([^》】」]{1,40})[》】」]", t)
        if m:
            return m.group(1).strip()
        if re.fullmatch(
                r"(?:好的?|嗯|那|请|我)?[，,～~]?\s*(?:小四)?[，,]?\s*"
                r"(?:给我|帮我|为你|为我|为您)?(?:想听你|想听)?(?:再)?"
                r"(?:来|唱|点)(?:一首|一个|一?首|个)?(?:歌|曲|首歌)?"
                r"(?:吧|呀|哦|噢|呗|～|~|!|！|。|？|\?)?", t):
            return ""
        m = re.match(r"^(?:给我|帮我|小四|请|我)?(?:想听你|想听)?(?:再)?"
                     r"(?:唱|来|点)(?:一首|一个|一?首|个)?(.{1,40})$", t)
        if m:
            song = m.group(1).strip("吧呀哦呗～~！!。 ，,")
            song = re.sub(r"^(一下|一会儿|一遍|个|首|来)", "", song).strip()
            # 「唱一下你刚才学的歌」这类不是具体歌名 → 当随机点歌（交给歌库随机）
            if re.search(r"刚才|刚刚|之前|前面|上次|新学|学的|学会|你会|你会的"
                         r"|随便|任意|那首|这首|什么歌|一下", song):
                return ""
            if not song:
                return ""
            if song not in ("歌", "首歌", "一首歌", "个歌", "曲",
                            "唱歌", "什么歌", "一个", "个"):
                return song
            return ""
        m = re.match(r"^(?:我)?想听(?:你)?(?:唱一?首?)?(.{1,40})$", t)
        if m:
            song = m.group(1).strip()
            if song and song not in ("歌", "唱歌"):
                # 指代/垃圾歌名 → 当随机点歌，绝不拿整句话去 B 站搜（14:11 事故）
                if re.search(r"刚才|刚刚|之前|前面|上次|新学|学的|学会|你会"
                             r"|随便|任意|那首|这首|什么歌|一下|一会儿|一遍", song):
                    return ""
                return song
        return None

    @staticmethod
    def _match_realtime(t, cfg):
        """实时信息确定性路由：天气/屏幕/报时直接走工具，绝不交给大模型凭印象编造
        （体检发现她会编天气数据、假装看屏幕）。返回 action dict 或 None。
        只在短句、单一意图时触发——带「再/然后/顺便」的复合指令仍交模型拆解。"""
        if len(t) > 16 or re.search(r"再|然后|顺便|之后|接着|并且", t):
            return None
        if re.search(r"天气|气温|多少度|下雨|下雪", t):
            m = re.search(r"([\u4e00-\u9fa5]{2,6}?)(?:的)?天气", t)
            city = ""
            if m:
                city = re.sub(r"^(今天|明天|后天|现在|请问|帮我|查一下|看看)",
                              "", m.group(1)).strip()
            return {"action": "weather", "city": city or cfg.get("city", "芜湖")}
        if re.search(r"屏幕|显示器|桌面上有(什么|啥)", t):
            return {"action": "look_screen", "question":
                    "用一句话说清屏幕上的主要内容、正在用的窗口和明显异常"}
        if re.search(r"(现在)?几点|报时|什么时间|现在时间", t):
            return {"action": "time"}
        return None

    @staticmethod
    def _canon_reply(reply, raw):
        """assistant 消息统一存成台词数组形态：防止历史里的旧样例把输出协议带偏
        （模型爱模仿历史格式——历史里存整段字符串，新协议就会被带回老路）"""
        try:
            zhv = reply.get("zh", "")
            if isinstance(zhv, list) and zhv:
                pass
            elif zhv and zhv != "……":
                zhv = [zhv]
            else:
                return raw
            return json.dumps({"cot": reply.get("cot", ""), "zh": zhv,
                               "ja": reply.get("ja", ""),
                               "emo": reply.get("emo", "")},
                              ensure_ascii=False)
        except Exception:
            return raw

    def chat(self, user_text):
        """智能体循环：思考 -> 执行动作 -> 观察结果 -> 继续，直到给出最终回复。
        返回 {"zh","ja","action"}（action 仅在无执行器时返回）"""
        cfg = self.cfg
        self.last_actions = []  # 本轮实际执行过的动作（供技能自动沉淀判断）
        t = user_text.strip()
        if re.fullmatch(r"(帮助|指令|菜单|你能做什么|你会什么|help)", t, re.I):
            return dict(HELP, action=None)

        # 唱歌进度确定性路由：「怎么还没唱/唱完没」直接报真实进度，绝不干喊
        # （18:06:48「怎么还没唱」曾落进 LLM，回「这就唱！」却不发动作=表面答应）
        # 注意模式要贴着「唱」匹配，防止「我还没吃饭，给我唱首歌」被误劫持
        if re.search(r"唱|歌", t) and re.search(
                r"还没唱|还没开唱|怎么还没|咋还没"
                r"|唱完(?:了)?(?:没|吗|么)|唱好(?:了)?(?:没|吗|么)"
                r"|(?:唱|歌|曲)[^。？！，,]{0,6}好了?(?:没|吗|么)"
                r"|还在唱|唱到哪|什么时候.{0,4}(?:唱完|唱好|好)", t):
            import maid_extras
            st = maid_extras.sing_state()
            if st.get("running"):
                mins = max(1, st.get("secs", 0) // 60)
                nm = re.sub(r"^(【.*?】|\[.*?\])", "",
                            st.get("name") or "那首歌")[:20]  # 去B站标题前缀
                return {"zh": "正学着唱《%s》呢，已经哼了%d分钟了。"
                              "学唱要几分钟，主人再等等。" % (nm, mins),
                        "ja": "", "emo": "",
                        "cot": "唱歌进度查询，按真实状态回答", "action": None}
            return {"zh": "刚才那首没唱成……主人再说一次「唱首歌」，"
                          "小四重新给你学。", "ja": "", "emo": "",
                    "cot": "唱歌进度查询：当前没有在唱", "action": None}

        # 唱歌确定性路由：匹配到就直接执行，绝不经过 LLM（防幻觉"正在演唱"）
        sing_song = self._match_sing(t)
        if sing_song is not None:
            action = {"action": "sing", "song": sing_song}
            self.last_actions.append(action)
            if self.executor:
                result = str(self.executor(action))
                return {"zh": result, "ja": "",
                        "action": None}
            return {"zh": "好的主人，小四这就去唱～", "ja": "",
                    "action": action}

        # 实时信息确定性路由：天气/屏幕/报时直接执行工具，杜绝编造（同唱歌路由思路）
        rt = self._match_realtime(t, cfg)
        if rt is not None:
            self.last_actions.append(rt)
            if self.executor:
                result = str(self.executor(rt))
                return {"zh": result, "ja": "", "emo": "", "cot": "", "action": None}
            return {"zh": "好的主人，小四这就去查。", "ja": "", "action": rt}

        if not cfg.get("api_key"):
            return dict(CANNED_NO_KEY, action=None)

        history = self._load_history()
        now = _t.localtime()  # 本地时间前缀：让问候/作息话题天然贴切（学七海）
        stamp = _t.strftime("%Y-%m-%d %H:%M", now) + " 周" + "一二三四五六日"[now.tm_wday]
        history.append({"role": "user",
                        "content": "[本地时间 %s] %s" % (stamp, user_text)})
        reply = {"zh": "……", "ja": ""}
        action = None
        fired = []  # 流式期间已念出的台词段（供对账，学七海：边生成边念）

        def _feed(seg):
            fired.append(str(seg).strip())
            cb = self.say_stream
            if callable(cb):
                try:
                    cb(seg)
                except Exception:
                    fired.pop()  # 下游拒收：不算已念

        for step in range(6):
            self._log("chat step %d <<<" % step, t if step == 0 else "(continue)")
            raw = self._call(cfg, history, zh_feed=_feed if self.say_stream else None)
            action, reply = parse_reply(raw)
            self._log("chat raw:", raw[:120].replace("\n", " "))
            if action is None and reply["zh"] != "……":  # 台词统一存数组形态
                history.append({"role": "assistant",
                                "content": self._canon_reply(reply, raw)})
            else:
                history.append({"role": "assistant", "content": raw})
            if action is None and reply["zh"] == "……":
                # 输出格式毛刺，解析失败：要求重发一次
                history.append({"role": "user", "content":
                                "（系统：上一条消息格式错误无法解析。请严格重发："
                                "要么一行@@action {...}，要么{\"zh\":..,\"ja\":..} JSON，"
                                "不要夹带任何其他文字。）"})
                continue
            if action is None or self.executor is None:
                break  # 最终回复，或无执行器时交回调用方
            self.last_actions.append(action)  # 记录执行过的动作
            result = self.executor(action)  # 执行动作
            history.append({"role": "user", "content":
                            "（系统：动作执行结果：\n%s\n）任务是否完成？"
                            "完成则输出最终JSON回复；还需操作则输出下一个@@action。"
                            "若结果为空、报错或与预期不符，请修正命令或换种方法重试，不要轻易放弃。" % result})
            action = None
        self._save_history(history)
        self._maybe_compact_async()  # 历史超限后台压缩（学 Shinsekai compact_manager）
        reply["action"] = action
        # 流式对账：已念出的段落与最终回复完全一致，才算"边生成边念"成功
        zh = reply.get("zh")
        segs = ([str(s).strip() for s in zh] if isinstance(zh, list)
                else ([str(zh).strip()] if str(zh or "").strip() else []))
        if fired and fired == segs:
            reply["streamed"] = True
        return reply

    def followup(self, result_text):
        """动作执行后，基于结果生成双语回复"""
        cfg = self.cfg
        if not cfg.get("api_key"):
            return {"zh": str(result_text), "ja": ""}
        history = self._load_history()
        history.append({"role": "user", "content":
                        "（系统提示：动作已执行，结果：\n%s\n）请基于这个结果，用规定的JSON格式回复主人。" % result_text})
        raw = self._call(cfg, history)
        _, reply = parse_reply(raw)
        history.append({"role": "assistant",
                        "content": self._canon_reply(reply, raw)})
        self._save_history(history)
        return reply

    def learn_call(self, messages):
        """记忆自学习专用轻量调用：只用免费模型，失败返回 None（学习是锦上添花，不为它等待）"""
        try:
            cfg = self.cfg
            if not cfg.get("api_key"):
                return None
            url = cfg["api_base"].rstrip("/") + "/chat/completions"
            headers = {"Authorization": "Bearer " + cfg["api_key"],
                       "Content-Type": "application/json"}
            model = cfg.get("model_free") or cfg.get("model", "glm-4-flash")
            return self._call_model(url, headers, messages, model,
                                    first_token_timeout=20, total_timeout=60)
        except Exception:
            return None

    def talk_line(self, prompt, first_token_timeout=2.2, total_timeout=3.0):
        """主动说话专用轻量调用（maid_talk 用）：给定提示词，现编一句话。
        与对话主链路同款策略：免费模型优先，快速失败（挂了/限流）切主模型再试；
        超时/失败返回 None，上层回退本地词库。"""
        try:
            cfg = self.cfg
            if not cfg.get("api_key"):
                return None
            url = cfg["api_base"].rstrip("/") + "/chat/completions"
            headers = {"Authorization": "Bearer " + cfg["api_key"],
                       "Content-Type": "application/json"}
            messages = [{"role": "system", "content": TALK_SYS},
                        {"role": "user", "content": prompt}]
            main_model = cfg.get("model", "glm-4-flash")
            free = cfg.get("model_free") or ""
            start = _t.time()
            if free and free != main_model:
                raw = self._call_model(url, headers, messages, free,
                                       first_token_timeout=first_token_timeout,
                                       total_timeout=total_timeout)
                if raw is not None:
                    return raw
                if _t.time() - start < total_timeout * 0.5:
                    # 免费模型快速失败：剩余预算还够，切主模型再试一次
                    return self._call_model(url, headers, messages, main_model,
                                            first_token_timeout=first_token_timeout,
                                            total_timeout=total_timeout)
                return None  # 免费模型只是慢：不硬等了，交上层回退
            return self._call_model(url, headers, messages, main_model,
                                    first_token_timeout=first_token_timeout,
                                    total_timeout=total_timeout)
        except Exception:
            return None

    def _call(self, cfg, history, zh_feed=None):
        url = cfg["api_base"].rstrip("/") + "/chat/completions"
        sys_prompt = SYSTEM_PROMPT
        if self.memory_provider:  # 每轮自动想起：主人画像全文注入系统提示
            try:
                mem = self.memory_provider()
                if mem:
                    sys_prompt += ("\n\n【小四认识的主人（主人画像）】这是小四长期相处"
                                   "记下的主人档案，回答时自然运用这些了解，"
                                   "不要逐条念出来：\n" + mem)
            except Exception:
                pass
        if self.skill_provider:  # 技能自积累：命中相关技能就注入，直接照做
            try:
                last_user = ""
                for m in reversed(history):
                    c = str(m.get("content", ""))
                    if m.get("role") == "user" and not c.startswith("（系统"):
                        last_user = c
                        break
                sk = self.skill_provider(last_user[:200])
                if sk:
                    sys_prompt += ("\n\n【可复用技能】这是小四以前成功完成同类任务的做法，"
                                   "与本任务相关时优先照做：\n" + sk)
            except Exception:
                pass
        messages = [{"role": "system", "content": sys_prompt}] + history[-24:]
        headers = {"Authorization": "Bearer " + cfg["api_key"],
                   "Content-Type": "application/json"}
        # 免费模型优先：8 秒内没吐出首个字就立刻切收费模型，不为卡顿干等
        free = cfg.get("model_free", "")
        if free and free != cfg.get("model"):
            raw = self._call_model(url, headers, messages, free,
                                   first_token_timeout=8, total_timeout=45,
                                   zh_feed=zh_feed)
            if raw is not None:
                return raw
            self._log("free model slow/down, switch to", cfg.get("model"))
        return self._call_model(url, headers, messages,
                                cfg.get("model", "glm-4-flash"),
                                first_token_timeout=15, total_timeout=60,
                                zh_feed=zh_feed) or \
            json.dumps(CANNED_ERR, ensure_ascii=False)

    def _call_model(self, url, headers, messages, model,
                    first_token_timeout=8, total_timeout=45, zh_feed=None):
        """流式调用：首个字超时（first_token_timeout 秒）即放弃返回 None，
        转由上层切换下一个模型；正常则读完整个回复再返回。
        zh_feed 给定时：流式增量解析 zh 数组，每闭合一段台词立刻回调——
        模型还在写后面内容时第一段已经在合成（学 Shinsekai 的 tts_queue）。"""
        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.85,
            "frequency_penalty": 0.4,  # 抑制重复用词与固定句式
            "max_tokens": 600,
            "stream": True,
        }
        if model.startswith(("glm-4.5", "glm-4.6", "glm-4.7", "glm-5")):
            payload["thinking"] = {"type": "disabled"}  # 女仆对话不需深思，省时省token
        stream = _ZhStream() if zh_feed else None
        try:
            start = _t.time()
            with _session.post(url, json=payload, headers=headers, stream=True,
                               timeout=(3, first_token_timeout)) as r:
                if r.status_code != 200:
                    return None
                parts = []
                for line in r.iter_lines(decode_unicode=True):
                    if not line or not line.startswith("data:"):
                        continue
                    chunk = line[5:].strip()
                    if chunk == "[DONE]":
                        break
                    try:
                        delta = json.loads(chunk)["choices"][0]["delta"]
                        content = delta.get("content") or ""
                        parts.append(content)
                        if stream and content:
                            for seg in stream.feed(content):
                                try:
                                    zh_feed(seg)
                                except Exception:
                                    stream.dead = True  # 下游出错：只断念白不断对话
                    except (ValueError, KeyError, IndexError):
                        continue
                    if _t.time() - start > total_timeout:
                        break
                if stream:  # 流结束：扫尾，交出最后闭合的段落
                    for seg in stream.finish():
                        try:
                            zh_feed(seg)
                        except Exception:
                            pass
                text = "".join(parts).strip()
                return text or None
        except requests.exceptions.ReadTimeout:
            return None  # 首 token 太慢：换模型
        except Exception as e:
            self._log("call model %s error:" % model, repr(e))
            return None

    # ---------- 对话历史 ----------
    @staticmethod
    def _hist_path():
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "maid_history.json")

    def _load_history(self):
        with _HIST_LOCK:
            try:
                with open(self._hist_path(), "r", encoding="utf-8") as f:
                    h = json.load(f)
                return h if isinstance(h, list) else []
            except (OSError, ValueError):
                return []

    def _save_history(self, h):
        with _HIST_LOCK:
            try:
                with open(self._hist_path(), "w", encoding="utf-8") as f:
                    json.dump(h[-40:], f, ensure_ascii=False)
            except OSError:
                pass

    def _maybe_compact_async(self):
        """历史压缩（学 Shinsekai compact_manager）：超过阈值就把旧消息压成
        一条【历史对话总结】常驻开头，近期原文保留——注入模型的历史永远是
        "摘要 + 近期"，聊过的事不再因为窗口截断而遗忘。
        摘要用免费模型后台生成，失败退化成确定性要点，绝不阻塞对话。"""
        if len(self._load_history()) <= HIST_COMPACT_AT:
            return

        def work():
            try:
                h = self._load_history()
                if len(h) <= HIST_COMPACT_AT:
                    return
                old, recent = h[:-HIST_RECENT], h[-HIST_RECENT:]
                lines, seen = [], set()
                for m in old:
                    c = str(m.get("content", "")).strip()
                    if not c:
                        continue
                    if c.startswith("（系统：动作执行结果"):
                        continue  # 工具输出不进摘要
                    c = re.sub(r"^\[本地时间 [^\]]+\]\s*", "", c)
                    tag = "主人" if m.get("role") == "user" else "小四"
                    key = (tag, c[:60])
                    if key in seen:
                        continue
                    seen.add(key)
                    lines.append("%s：%s" % (tag, c[:150]))
                dialog = "\n".join(lines)[-4000:]
                if not dialog:
                    return
                prompt = ("请把下面主人与女仆AI小四的早期对话压缩成一段简洁摘要"
                          "（300字内）：保留主人聊过的话题、提过的要求与偏好、"
                          "小四答应或做过的事；去掉寒暄和重复。直接输出摘要正文：\n" + dialog)
                summary = (self.learn_call([{"role": "user", "content": prompt}]) or "").strip()
                if not summary:
                    # 免费模型失败：确定性兜底——主人原话要点直存（学 compact 的
                    # "宁可保真也不丢上下文"原则）
                    summary = "（早期对话要点）" + " / ".join(
                        l for l in lines if l.startswith("主人：") or l.startswith("小四："))[:800]
                with _HIST_LOCK:
                    fresh = self._load_history()
                    if len(fresh) <= HIST_RECENT:
                        return  # 期间被别的压缩处理过了
                    compacted = [{"role": "user", "content":
                                  "【历史对话总结】以下是之前对话的压缩总结：\n%s\n\n"
                                  "请基于这个上下文继续对话。" % summary[:800]}
                                 ] + fresh[-HIST_RECENT:]
                    try:
                        with open(self._hist_path(), "w", encoding="utf-8") as f:
                            json.dump(compacted, f, ensure_ascii=False)
                        self._log("history compacted: %d -> %d" %
                                  (len(fresh), len(compacted)))
                    except OSError:
                        pass
            except Exception as e:
                self._log("compact error:", repr(e))

        threading.Thread(target=work, daemon=True, name="hist-compact").start()
