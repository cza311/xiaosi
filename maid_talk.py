# -*- coding: utf-8 -*-
"""小四的统一台词工厂：她要开口说的话都从这里出（根治固定台词重复）。
学 Coopanion/Cortico 的思路——主动开口的话尽量过大模型现编（A层：
晨昏问候/待机搭话/久坐关怀/动作汇报），本地词库只做兜底；
手指一动就要出声的即时互动（B层：摸头/戳/抛掷/落地）用
"大词库 + 模板组合 + 洗牌抽样"保证短期绝不重复。"""
import collections
import datetime
import random
import threading


# ============ 通用洗牌抽样器 ============
class ShuffledBank:
    """洗牌抽样：把词库洗成队列逐个抽，抽完一轮再重洗；
    重洗时避免上一轮末尾那句排到队首，杜绝短期内重复。"""

    def __init__(self, items):
        self._pool = [str(x) for x in items if str(x).strip()]
        self._queue = []
        self._last = None

    def draw(self):
        if not self._pool:
            return ""
        if not self._queue:
            self._queue = self._pool[:]
            random.shuffle(self._queue)
            if len(self._queue) > 1 and self._queue[-1] == self._last:
                # 新一轮开头别跟上一轮结尾撞车：从后面随机换一张到队首
                i = random.randrange(0, len(self._queue) - 1)
                self._queue[-1], self._queue[i] = self._queue[i], self._queue[-1]
        self._last = self._queue.pop()
        return self._last


# ============ B层：即时互动台词（摸头/戳/抛掷/落地） ============
class TouchBank:
    """整句词库 × 模板组合：50% 抽整句，50% 用 开场词×核心×尾缀 现拼，
    组合空间数百条，配合洗牌抽样短期几乎不可能重复。"""

    def __init__(self, fulls, opens, cores, tails):
        self.fulls = ShuffledBank(fulls)
        self.opens = [str(x) for x in opens]
        self.cores = ShuffledBank(cores)
        self.tails = [str(x) for x in tails]

    def draw(self):
        if random.random() < 0.5 or not self.tails:
            return self.fulls.draw()
        core = self.cores.draw().rstrip("。！？!?~～… ")
        if not core:
            return self.fulls.draw()
        return random.choice(self.opens) + core + random.choice(self.tails)


_TOUCH = {
    "pat": TouchBank(
        fulls=[
            "嘿嘿…再摸就蹭你了", "唔…好舒服，继续呀", "毛茸茸认证通过～",
            "主人的手，暖洋洋的", "再摸下去小四要睡着了啦", "摸吧摸吧，今天免费",
            "唔…头发要被摸乱了啦", "主人摸头的手法，熟练得可疑",
            "再摸就要充电充满了", "被摸头的小四，状态+10",
            "哼，只准摸这一次哦", "嗯～比午睡还舒服",
            "小心，摸多了会上瘾的", "（安静地蹭了蹭手心）",
            "毛都要被你顺光了", "这算什么，摸头杀？",
            "唔哇，电量恢复中…",
        ],
        opens=["唔…", "嘿嘿，", "呐，", "呜哇，", "哼，", "…", "喵，", "诶嘿嘿，"],
        cores=["好舒服", "头要被你摸乱了", "再摸就蹭你了", "主人的手好暖",
               "毛茸茸认证通过", "继续呀别停", "头顶在发烫啦", "幸福感超标了",
               "要长不高的哦", "头发要打结了啦"],
        tails=["～", "啦", "哦", "…", "！", "（蹭蹭）", "嘛"],
    ),
    "poke": TouchBank(
        fulls=[
            "戳戳戳，要戳坏啦！", "再戳我真的要生气了哦（才怪）",
            "喂！我在工作呢！", "戳一下要收钱的哦", "呜哇，突然袭击！",
            "别戳了别戳了，说正事", "戳这里也弹不出菜单的",
            "好啦好啦，小四在呢", "连戳三下是要开隐藏功能吗",
            "抗议！对AI使用武力！", "再戳就要反戳回来了哦",
            "（假装没被戳到）", "戳够了吗？戳够了干活",
            "唔…尾巴要被戳出来了（并没有尾巴）", "这一戳，内存+1",
            "警告：再戳将触发抖动机能", "轻点轻点，软件也会痛",
        ],
        opens=["喂！", "呜哇，", "呀！", "唔，", "哈？", "…", "诶！", "哼！"],
        cores=["要戳坏啦", "别戳了啦", "戳一下要收钱的", "我在工作呢",
               "再戳真生气了哦", "肚子不可以戳", "戳不出表情的",
               "好啦好啦我在", "抗议抗议", "戳这里也没有用哦"],
        tails=["！", "啦", "哦…", "（才怪）", "！？", "…", "嘛"],
    ),
    "throw": TouchBank(
        fulls=[
            "呜哇——我会飞！！", "你把我扔出去了呜呜", "轻一点啦！！",
            "自由落体ing，好刺激", "空中转体三周半，打分！",
            "下次至少给个降落伞啦", "这是在测试抗震性能吗！",
            "啊——失重好可怕但有点爽", "（在空中划出优美弧线）",
            "扔出去的，是要还的哦", "小四航空计划，启动失败！",
            "哇啊啊，翅膀呢我的翅膀", "落地请叫我，怕摔",
            "飞行时长0.8秒，体验极差，差评", "（尖叫着划过桌面）",
            "重力：1，小四：0", "谁允许的！……好吧有点好玩",
        ],
        opens=["呜哇——", "哇啊！", "呀啊啊，", "唔哇，", "救命——", "…"],
        cores=["我会飞", "你把我扔出去了", "轻一点啦", "自由落体ing",
               "空中姿势满分", "这是航空测试吗", "下落中请勿挂机",
               "重力好可怕", "伞呢我没有伞", "申请着陆在手心"],
        tails=["！！", "！", "…", "（翻滚）", "呜…", "！？"],
    ),
    "landed": TouchBank(
        fulls=[
            "呜…坐晕了…", "哼，不理你了（蹭蹭）", "下次不许扔这么远",
            "安全着陆…应该吧", "（原地转了转脑袋）没散架",
            "着陆成功，小四的抗摔性+1", "你赔！精神损失费！",
            "（拍拍灰）勉强原谅你", "差点变成一桌子的碎片啦",
            "落地角度满分，姿势狼狈", "哼，这就算扯平了",
            "再扔一次试试看（挑衅）", "唔…头顶冒星星中",
            "小四重启完毕，本次无损坏", "（假装很淡定地站起来）",
            "下次着陆能不能选个软一点的（主人）", "（掸掸土）就当没发生过",
        ],
        opens=["呜…", "哼，", "唔…", "咳，", "（坐起来）", "…"],
        cores=["坐晕了", "下次不许扔这么远", "着陆成功…大概", "这就算原谅你了",
               "落地评分不及格", "哼，不理你了", "地面很硬知不知道",
               "差点就是一叠碎片了", "小四重新启动完毕", "精神损失费记上了"],
        tails=["…", "啦", "哦", "（蹭蹭）", "！", "…哼"],
    ),
}
_TOUCH_RECENT = collections.deque(maxlen=12)  # 最近说过的互动台词，防短期重复
_TOUCH_FALLBACK = {  # maid_talk 自身异常时的最后兜底（原版四句）
    "pat": "嘿嘿…再摸就蹭你了", "poke": "戳戳戳，要戳坏啦！",
    "throw": "呜哇——我会飞！！", "landed": "呜…坐晕了…",
}


def touch_line(kind):
    """B层入口：即时互动台词。本地现拼（不走大模型，等不了），
    洗牌抽样 + 近12句去重，短期绝不重复。"""
    bank = _TOUCH.get(kind)
    if bank is None:
        return _TOUCH_FALLBACK.get(kind, "……")
    line = ""
    for _ in range(8):  # 抽到和最近说的不重复为止（组合空间数百，必然抽到）
        line = bank.draw()
        if line and line not in _TOUCH_RECENT:
            break
    _TOUCH_RECENT.append(line)
    remember_line(line)
    return line


# ============ A层：过大模型现编的主动台词 ============
_providers = {"brain": None, "memory_brief": None}
_SAID = collections.deque(maxlen=10)  # 最近主动说过的话（含兜底词库），喂给 LLM 去重

SCENE_HINT = {
    "morning": "早上的时段，向主人道早安，开启新的一天",
    "night": "到了夜间休息时间，提醒主人早点睡",
    "idle": "待机搭话：主人空闲时主动找话说",
    "sedentary": "久坐关怀：催主人起来活动一下",
    "sing_done": "动作完成汇报：翻唱做好了",
    "sing_fail": "动作结果汇报：翻唱失败了",
    "tuwen_done": "动作完成汇报：小说推文视频做好了",
    "tuwen_fail": "动作结果汇报：推文视频做失败了",
    "learn_done": "动作完成汇报：GitHub 学习完成",
    "learn_miss": "动作结果汇报：GitHub 没找到有价值资料",
}


def setup(brain=None, memory_brief=None):
    """主程序注入依赖：brain 提供 LLM 通道，memory_brief 提供主人画像"""
    if brain is not None:
        _providers["brain"] = brain
    if memory_brief is not None:
        _providers["memory_brief"] = memory_brief


def remember_line(text):
    """记下说过的一句（LLM 生成与本地兜底都记），供后续生成去重"""
    try:
        t = str(text or "").strip()
        if t:
            _SAID.append(t[:60])
    except Exception:
        pass


def _scene_context(scene, ctx):
    """各场景的动态上下文一行（拼进用户提示词）"""
    if scene == "sedentary":
        return "主人已经连续用机约 %s 分钟，提醒他起身活动。" % ctx.get("idle_min", "?")
    if scene == "idle":
        return ("主人约 %s 分钟没说话了，前台窗口是「%s」。"
                "随口搭一句：关心、闲聊或小吐槽都行，别连环提问。" % (
                    ctx.get("idle_min", "?"), str(ctx.get("fg", ""))[:40]))
    if scene == "sing_done":
        return "翻唱任务完成了：《%s》%s。汇报歌名，邀请主人听。" % (
            ctx.get("name", "?"),
            "（这首歌是小四自己去 B 站找来学的）" if ctx.get("fetched") else "")
    if scene == "sing_fail":
        return ("翻唱《%s》失败了（原因：%s）。自然地告诉主人这次没唱成，"
                "别复述技术细节。" % (ctx.get("name", "?"), str(ctx.get("err", ""))[:60]))
    if scene == "tuwen_done":
        return ("小说推文视频《%s》做好了（竖屏配音字幕，在小四的作品文件夹）。"
                "汇报并请主人审片。" % ctx.get("name", "?"))
    if scene == "tuwen_fail":
        return ("推文视频《%s》做失败了（原因：%s）。自然地说没做出来，"
                "别复述技术细节。" % (ctx.get("name", "?"), str(ctx.get("err", ""))[:60]))
    if scene == "learn_done":
        return ("刚从 GitHub 学会一个新技能「%s」：%s。汇报学到了什么，"
                "语气可以小得意。" % (ctx.get("skill", "?"), str(ctx.get("steps", ""))[:60]))
    if scene == "learn_miss":
        return ("在 GitHub 上没找到关于「%s」的有价值资料，"
                "告诉主人这次没学到，下次再试。" % ctx.get("topic", "?"))
    return ""


def _build_prompt(scene, ctx):
    """把场景/时间/小四状态/主人画像/最近对话/最近说过 拼成一段用户提示词"""
    now = datetime.datetime.now()
    week = "一二三四五六日"[now.weekday()]
    parts = ["【要说的场景】" + SCENE_HINT.get(scene, scene)]
    body = _scene_context(scene, ctx)
    if body:
        parts.append(body)
    try:  # 小四当前状态（干活/唱歌/思考…）
        import maid_status
        st = maid_status.get_status()
        if st and st.get("status", "idle") != "idle":
            parts.append("【小四当前状态】%s %s" % (
                st.get("status", ""), str(st.get("detail", ""))[:30]))
    except Exception:
        pass
    mem = _providers.get("memory_brief")
    if callable(mem):
        try:
            m = mem()
            if m:
                parts.append("【主人画像要点（自然运用，别逐条念）】" + str(m)[:300])
        except Exception:
            pass
    brain = _providers.get("brain")
    if brain is not None:
        try:  # 最近对话摘要：让搭话能接住刚才聊过的话题
            msgs = []
            for m in brain._load_history()[-8:]:
                c = str(m.get("content", "")).replace("\n", " ").strip()
                if not c:
                    continue
                who = "主人" if m.get("role") == "user" else "小四"
                msgs.append(who + "：" + c[:60])
            if msgs:
                parts.append("【最近对话摘要】" + " / ".join(msgs[-6:]))
        except Exception:
            pass
    if _SAID:
        parts.append("【最近说过（绝不能再重复这些说法）】" + "；".join(_SAID))
    parts.append("【现在时间】%04d-%02d-%02d 周%s %02d:%02d" % (
        now.year, now.month, now.day, week, now.hour, now.minute))
    return "\n".join(parts)


def _clean(text):
    """清洗 LLM 输出：取一行、去引号星号、限长；不合格返回空串"""
    try:
        t = str(text or "").strip()
        if not t:
            return ""
        t = t.splitlines()[0].strip()
        t = t.strip("\"'“”「」* ")
        while t and t[-1] in "\"'“”「」* ":
            t = t[:-1].rstrip()
        if t.startswith("小四：") or t.startswith("小四:"):
            t = t[3:].lstrip("：: ").strip()
        return t[:60] if len(t) >= 2 else ""
    except Exception:
        return ""


def _fallback(scene, ctx):
    """本地兜底词库（LLM 不可用/超时时用），返回 speak() 认的 dict"""
    if scene == "sedentary":
        return {"zh": "主人，你已经连续用机 %d 分钟了，站起来伸展一下，小四看着心疼。"
                      % _to_int(ctx.get("idle_min"), 45),
                "ja": "ご主人様、少し休憩してくださいね。"}
    if scene == "morning":
        return {"zh": random.choice([
                    "早上好主人，小四已备好今天的好心情。",
                    "早安主人。新的一天，从叫小四开始。",
                    "主人早上好～今天也请多指教。",
                    "早。太阳晒屁股了哦，主人。"]),
                "ja": "おはようございます、ご主人様。"}
    if scene == "night":
        return {"zh": random.choice([
                    "夜深了主人，早点休息，小四守着你。",
                    "主人，该睡了。熬夜的账，小四可都记着。",
                    "晚安主人，做个好梦。小四就在这儿。",
                    "夜深了，屏幕该歇歇了，主人也一样。"]),
                "ja": "もう夜ですね、おやすみなさい。"}
    if scene == "idle":
        return {"zh": random.choice(CHATTER), "ja": ""}
    if scene == "sing_done":
        return {"zh": ("主人，小四自己去 B 站找到了《%s》，已经用她自己的声音唱好了，"
                       "听听看～" if ctx.get("fetched") else
                       "主人，《%s》翻唱好了，是小四自己的声音哦，听听看～")
                       % ctx.get("name", ""), "ja": ""}
    if scene == "sing_fail":
        return {"zh": "呜……《%s》的翻唱失败了，小四再练练。（%s）" % (
                    ctx.get("name", ""), str(ctx.get("err", ""))[-100:]), "ja": ""}
    if scene == "tuwen_done":
        return {"zh": "主人，小说推文《%s》做好了！竖屏带配音和字幕，"
                      "在小四的作品文件夹里，快来审片～" % ctx.get("name", ""), "ja": ""}
    if scene == "tuwen_fail":
        return {"zh": "呜……《%s》的推文视频做失败了。（%s）" % (
                    ctx.get("name", ""), str(ctx.get("err", ""))[-100:]), "ja": ""}
    if scene == "learn_done":
        return {"zh": "主人，小四学会新技能了：%s —— %s（已存入技能库）" % (
                    ctx.get("skill", "?"), str(ctx.get("steps", ""))[:60]),
                "ja": "ご主人様、新しいスキルを覚えました。"}
    if scene == "learn_miss":
        return {"zh": "主人，GitHub 上暂时没找到关于「%s」的有价值资料，下次再试。"
                      % ctx.get("topic", "?"), "ja": ""}
    return {"zh": "", "ja": ""}


def _to_int(v, dft):
    try:
        return int(v)
    except (TypeError, ValueError):
        return dft


CHATTER = [  # 待机搭话兜底词库
    "主人，茶要凉了，起来活动一下吧。",
    "主人，窗外天气不错，偶尔看看远处保护眼睛。",
    "主人，小四随时候命，有事叫我。",
    "主人，坐挺久了，小四陪你到窗边站一站？",
    "咚咚——有人在吗？小四无聊了。",
    "主人，喝口水吧，就当陪小四喝。",
    "小四把你的桌面看了三遍，还是挺整齐的（这次）。",
    "主人主人，摸摸头可以当加班费吗？",
]


def generate_line(scene, context=None, wait=8.0):
    """A层统一入口：优先大模型现编（子线程里调，最多等 wait 秒），
    失败/超时回退本地词库。返回 speak() 认的 dict，必不返回 None。
    wait 默认 8 秒：实测免费模型首字延迟约 5 秒，等太久会白白回退；
    这些场景（晨昏/搭话/汇报）都在后台线程，晚几秒开口无感。"""
    result = {}

    def work():
        try:
            brain = _providers.get("brain")
            if brain is None or not callable(getattr(brain, "talk_line", None)):
                return
            # 首 token 预算随总预算走（默认 3 秒总预算 → 首 token 2.2 秒，
            # 给更宽裕的 wait 时同步放宽，减少白白回退）
            text = brain.talk_line(_build_prompt(scene, dict(context or {})),
                                   first_token_timeout=max(2.0, wait * 0.75),
                                   total_timeout=wait)
            text = _clean(text)
            if text and text not in _SAID:  # 和最近说过的撞了=白编，走兜底
                result["resp"] = {"zh": text, "ja": "", "emo": ""}
        except Exception:
            pass

    try:
        th = threading.Thread(target=work, daemon=True, name="talk-line")
        th.start()
        th.join(max(0.5, wait))
    except Exception:
        pass
    resp = result.get("resp")
    if resp:
        remember_line(resp["zh"])
        return resp
    fb = _fallback(scene, dict(context or {}))
    remember_line(fb.get("zh", ""))
    return fb


# ============ main.py 固定应答的变体词库（即时场景，洗牌抽样） ============
_WAKE = ShuffledBank([
    "在的，主人。请讲。", "嗯，小四在。", "主人，请吩咐。",
    "来了来了，有什么事？", "小四候命中，说吧。", "在呢在呢。",
    "请讲，主人。", "主人有何吩咐？", "呐，我在哦。",
    "（转过身）怎么了，主人？",
])
_SLEEP = ShuffledBank([
    "那小四先退下了，主人叫我一声就好。", "好，我先去后面待命。",
    "嗯，需要时随时叫我。", "那我去泡茶了，有事喊我。",
    "退下啦～记得叫我哦。", "知道了，主人慢忙。",
])
_HELLO = ShuffledBank([
    "主人，小四上线了。喊「小四」或按 C K 都能叫我。",
    "小四已就位。喊「小四」或按 C K 叫我。",
    "主人回来啦，小四启动完毕。喊「小四」就能找到我。",
])


def wake_ack():
    """唤醒应答（即时，不走 LLM）；异常返回 None 由调用方用原句兜底"""
    try:
        line = _WAKE.draw()
        remember_line(line)
        return {"zh": line, "ja": "", "emo": ""}
    except Exception:
        return None


def sleep_bye():
    """休眠道别（即时，不走 LLM）"""
    try:
        line = _SLEEP.draw()
        remember_line(line)
        return {"zh": line, "ja": "", "emo": ""}
    except Exception:
        return None


def hello():
    """上线问候（每次启动一次）"""
    try:
        line = _HELLO.draw()
        remember_line(line)
        return {"zh": line, "ja": "", "emo": ""}
    except Exception:
        return None
