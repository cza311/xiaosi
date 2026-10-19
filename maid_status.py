# -*- coding: utf-8 -*-
"""小四全局状态机（W1）：桌面立绘 / 远程查询共用的一份当前状态。
状态：idle 空闲 / thinking 思考中 / working 干活中 / waiting 等主人确认 /
error 出错 / done 刚完成 / singing 唱歌中。
非 sticky 的 done/error 在 8 秒后自动回 idle（惰性检查实现，不开线程不漏 Timer）。"""
import threading
import time

STATUSES = ("idle", "thinking", "working", "waiting", "error", "done", "singing")
EXPIRE_SECONDS = 8.0  # 非 sticky 的 done/error 自动回 idle 的秒数

_lock = threading.Lock()
_state = {"status": "idle", "detail": "", "ts": 0}
_expire_at = 0.0   # 当前状态的过期时间戳（0 = 不过期）
_listener = None   # 状态变化回调（主程序注入：灵动岛胶囊 + 表情联动）


def _lazy_expire():
    """惰性检查：非 sticky 的 done/error 超时自动回 idle（须持锁调用）"""
    global _expire_at
    if _expire_at and time.time() >= _expire_at:
        _state["status"] = "idle"
        _state["detail"] = ""
        _state["ts"] = time.time()
        _expire_at = 0.0


def set_status(status, detail="", sticky=False):
    """更新全局状态并记时间戳；非 sticky 的 done/error 记 8 秒过期点，到期自动回 idle"""
    global _expire_at
    if status not in STATUSES:
        return
    now = time.time()
    with _lock:
        _state["status"] = status
        _state["detail"] = str(detail or "")
        _state["ts"] = now
        _expire_at = (now + EXPIRE_SECONDS
                      if (not sticky and status in ("done", "error")) else 0.0)
        fn = _listener
    if fn:  # 锁外通知，避免回调里再取状态时死锁
        try:
            fn(status, str(detail or ""))
        except Exception:
            pass


def clear_if(status):
    """仅当当前状态恰为 status 时回 idle（原子判断+清除，无竞态）。
    对话回复到手后用 clear_if("thinking") 回空闲——若期间动作已把状态
    推进为 working/singing 等，绝不能把它覆盖回 idle（2026-10-19 实证）。"""
    global _expire_at
    fn = None
    with _lock:
        _lazy_expire()
        if _state["status"] != status:
            return False
        _state["status"] = "idle"
        _state["detail"] = ""
        _state["ts"] = time.time()
        _expire_at = 0.0
        fn = _listener
    if fn:
        try:
            fn("idle", "")
        except Exception:
            pass
    return True


def finish(detail=""):
    """动作收尾置 done（8 秒自动回 idle）。
    长任务（singing/working/waiting）进行中绝不覆盖——
    sing 等动作是后台化立即返回的，收尾时真任务才刚开始
    （2026-10-19 实证：done 把刚置上的 singing 顶掉，8 秒后状态全盲）。"""
    global _expire_at
    fn = None
    with _lock:
        _lazy_expire()
        if _state["status"] in ("singing", "working", "waiting"):
            return False
        _state["status"] = "done"
        _state["detail"] = str(detail or "")
        _state["ts"] = time.time()
        _expire_at = time.time() + EXPIRE_SECONDS
        fn = _listener
    if fn:
        try:
            fn("done", str(detail or ""))
        except Exception:
            pass
    return True


def get_status():
    """读取当前状态（先惰性检查超时回 idle），返回字典副本"""
    with _lock:
        _lazy_expire()
        return dict(_state)


def set_listener(fn):
    """注册状态变化监听（如主程序联动灵动岛胶囊）；传 None 注销"""
    global _listener
    with _lock:
        _listener = fn
