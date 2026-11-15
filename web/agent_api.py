"""Agent 对话端点：SSE 流式 + 运行回放。

为什么用 SSE 而不是 WebSocket：一次请求一条流，问完就断；WebSocket 要自己
管重连和心跳，而这里没有双向需求。前端用 `fetch` 读 `ReadableStream`
（EventSource 不支持 POST）。

为什么 `done` 由本层发而不是 loop 发：done 要带最终 `answer/mode`，
而 loop 的职责到"产出结果"为止；分层清楚，测试也好写。

鉴权边界（2026-11-14 修）
------------------------
`/api/agent/runs/{id}` 以前不在 `_AUTH_PRODUCT_PREFIXES` 里，**不登录也能读**，
而 trace 里有问题原文、歌词、工具结果。现在：
  · 路径前缀进登录守卫（没登录 401）；
  · 非管理员还要校验 trace 的 uid 属于当前演唱者（否则 403）。
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from analysis import coach, coach_agent
from analysis.agent import loop, trace
from web import agent_limit

router = APIRouter()

# 服务端的最后一道等待上限。loop 自己有 25s 预算，但一次慢调用/重试仍可能
# 越过它；过了这个点前端不再等，直接拿规则版（线程会跑完，Python 不能强杀）。
_RUN_TIMEOUT_S = 35.0

# 工具 → 报告页证据标签。报告页的追问现在也走 Agent，但 UI 仍按老契约
# 展示 evidence 小标签；这里把"它查了什么"翻译回去，不额外多传数据。
_EVIDENCE_LABEL = {
    "get_report_overview": "总体测量",
    "get_phrase": "句级细节",
    "list_phrases": "句级排序",
    "search_lyrics": "歌词定位",
    "get_beat_diagnosis": "节拍诊断",
    "get_practice_card": "练习卡",
    "get_profile": "画像",
    "get_recommendations": "曲目推荐",
}


@router.post("/api/session/{uid}/agent/chat")
async def agent_chat(uid: str, req: Request):
    from web import server as srv

    body = await srv._json(req)
    if not isinstance(body, dict):
        return srv.fail("BAD_REQUEST", "请求体必须是对象。")
    message = str(body.get("message") or "").strip()
    if not message:
        return srv.fail("BAD_REQUEST", "问点什么再发送。")
    if len(message) > 400:
        message = message[:400]

    rep = srv._load_report_for_chat(uid)
    if not rep:
        return srv.fail("NOT_FOUND", "这条练习还没有可追问的报告。", 404)
    singer_id = str(body.get("singer_id") or "").strip()
    srv._assert_session_owner(uid, singer_id or None)

    # 限流：窗口次数 + 进程内并发。失败直接 429，不占 LLM 预算。
    allowed, why, retry_after = agent_limit.acquire(singer_id or uid)
    if not allowed:
        return JSONResponse(
            status_code=429,
            content={"ok": False, "error": {
                "code": "RATE_LIMITED", "message": why}},
            headers={"Retry-After": str(retry_after),
                     "Cache-Control": "no-store"})

    try:
        ctx = srv._agent_context(uid, rep, singer_id)
        history = (body.get("history")
                   if isinstance(body.get("history"), list) else [])
    except Exception:
        agent_limit.release()
        raise

    q: asyncio.Queue = asyncio.Queue()
    running = asyncio.get_running_loop()

    def emit(event: str, data: dict) -> None:
        # loop 在工作线程里跑，必须回到事件循环再入队
        running.call_soon_threadsafe(q.put_nowait, {"event": event, "data": data})

    async def worker() -> None:
        made, mode, reason = "", "rule_fallback", ""
        run_id, n_steps = "", 0
        proposals: list[str] = []
        evidence: list[str] = []
        practice = False
        verify_reason = ""
        # 规则版先算好：校验不过时用它兜底，不再多调一次模型。
        # 它自己也可能因为报告结构不全抛异常 —— 那也不能让流挂住不 done。
        try:
            rule_text = coach_agent.rule_only(rep, message)
        except Exception as e:  # noqa: BLE001
            rule_text = "这次没能把报告读出来，先按报告里的练习卡练。"
            reason = f"规则版失败：{str(e)[:80]}"
        key = coach.load_key()
        if not key:
            reason = reason or "没配 API key"
        else:
            try:
                res = await asyncio.wait_for(
                    asyncio.to_thread(
                        loop.run, ctx, message, history,
                        key=key, rule_answer=rule_text, emit=emit),
                    timeout=_RUN_TIMEOUT_S)
                made, mode, reason = res.answer, res.mode, res.stop_reason
                run_id, n_steps = res.run_id, len(res.steps)
                verify_reason = res.verify_reason
                # 唯一的写方向：工具只提议，落库由用户点「记住」再打 /agent/apply。
                for c in res.tool_calls:
                    name = c.get("name")
                    data = c.get("data") if isinstance(c.get("data"), dict) else {}
                    if name == "propose_preference" and data.get("proposal"):
                        proposals.append(str(data["proposal"])[:60])
                    # 报告页的 evidence 小标签：把"它查了什么"翻译回老契约。
                    if name == "get_phrase" and isinstance(data.get("line_no"), int):
                        label = f"第 {data['line_no']} 句"
                    else:
                        label = _EVIDENCE_LABEL.get(name or "")
                    if label and label not in evidence:
                        evidence.append(label)
                    if name == "get_practice_card" and data.get("available"):
                        practice = True
                proposals = proposals[:2]
                evidence = evidence[:5]
            except asyncio.TimeoutError:
                reason = f"agent 超过 {_RUN_TIMEOUT_S:.0f}s，先给规则版"
            except Exception as e:  # noqa: BLE001 —— agent 故障不许拖垮报告页
                reason = f"agent 失败：{str(e)[:120]}"
        if not made:
            made, mode = rule_text, "rule_fallback"
        q.put_nowait({"event": "done",
                      "data": {"answer": made, "mode": mode,
                               "stop_reason": reason,
                               "run_id": run_id, "steps": n_steps,
                               "proposals": proposals,
                               "evidence": evidence,
                               "practice": practice,
                               "verify_reason": verify_reason}})

    async def stream():
        task = asyncio.create_task(worker())
        try:
            while True:
                ev = await q.get()
                payload = json.dumps(ev["data"], ensure_ascii=False)
                yield f"event: {ev['event']}\ndata: {payload}\n\n"
                if ev["event"] == "done":
                    break
        finally:
            # 客户端断开时取消 worker：前端不再等、队列不再涨。
            # 注意：正在执行的 loop.run 在 to_thread 里，Python 不能强杀线程，
            # 它会跑完（trace 仍会落盘）；这里只是不再往前端推。
            task.cancel()
            agent_limit.release()

    return StreamingResponse(
        stream(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/api/agent/runs/{run_id}")
async def agent_run(run_id: str, req: Request):
    """回放一次运行：步骤、参数、耗时、错误。前端「查看完整 trace」用它。

    鉴权：登录守卫在前缀上；这里再校验归属，防止拿到别人的 run_id 就能看。
    管理员可看全部；`SINGLENS_AUTH=0` 的本机演示模式保持原行为。
    """
    from web import server as srv
    from web import auth as auth_mod

    row = trace.read(run_id)
    if not row:
        return srv.fail("NOT_FOUND", "没有这次运行（或超过 7 天已滚掉）。", 404)

    user = getattr(req.state, "user", None)
    if user is None:
        if auth_mod.auth_enabled():
            return srv.fail("UNAUTHORIZED", "请先登录后再查看运行记录。", 401)
        return srv.ok(row)
    if user.get("role") != "admin":
        uid = str(row.get("uid") or "")
        try:
            owner = srv.practice_store.session_owner(uid)
        except Exception:  # noqa: BLE001 —— 查不动就按无权处理，不放大权限
            owner = None
        if owner != (user.get("singer_id") or ""):
            return srv.fail("FORBIDDEN", "不能查看其他演唱者的运行记录。", 403)
    return srv.ok(row)


# 唯一允许 Agent 提议、由用户确认后执行的写操作。
# 为什么不让模型直接写：画像会影响之后的每一次建议，写错一次会一直错。
# 所以工具只能 `propose_preference`（返回候选），落库必须用户点"记住"再打这里。
_APPLY_ACTIONS = {"save_preference"}


@router.post("/api/session/{uid}/agent/apply")
async def agent_apply(uid: str, req: Request):
    from web import server as srv

    body = await srv._json(req)
    if not isinstance(body, dict):
        return srv.fail("BAD_REQUEST", "请求体必须是对象。")
    action = str(body.get("action") or "").strip()
    if action not in _APPLY_ACTIONS:
        return srv.fail("BAD_REQUEST",
                        f"不支持的 action：{action or '（空）'}；"
                        f"可用：{', '.join(sorted(_APPLY_ACTIONS))}")
    singer_id = str(body.get("singer_id") or "").strip()
    note = str(body.get("text") or "").strip()[:60]
    if not singer_id or not note:
        return srv.fail("BAD_REQUEST", "缺 singer_id 或 text。")

    srv._require_singer(singer_id)
    srv._assert_session_owner(uid, singer_id)   # 不能借别人的会话写自己的画像
    saved = srv.practice_store.append_profile_note(singer_id, note)
    return srv.ok({"saved": True, "profile": saved})
