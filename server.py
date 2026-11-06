"""KakaoTalk (macOS) MCP — 접근성(AX) API로 허용된 채팅방만 읽고 보낸다.

허용 목록: ~/kakao-mcp/config.json 의 "allowed_chats" (호출마다 다시 읽음, 재시작 불필요).
화면이 잠기면 macOS가 AX 창 트리를 숨기므로 모든 도구가 LOCKED 오류를 낸다.
"""
from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path

import Quartz
from ApplicationServices import (
    AXUIElementCopyAttributeValue,
    AXUIElementCreateApplication,
    AXUIElementPerformAction,
    AXUIElementSetAttributeValue,
)
from mcp.server.fastmcp import FastMCP

import notif

CONFIG = Path(__file__).with_name("config.json")
QUEUE = Path(__file__).with_name("queue.jsonl")
SENT_LOG = Path(__file__).with_name("queue_sent.log")
IMG_DIR = Path(__file__).with_name("images")
UI_LOCK = threading.RLock()
QUEUE_LOCK = threading.Lock()
BUNDLE = "com.kakao.KakaoTalkMac"
MAIN_TITLE = "카카오톡"
RETURN_KEY = 36

mcp = FastMCP("kakaotalk")


class KakaoError(Exception):
    pass


# ---------- AX helpers ----------
def ax(el, attr):
    err, v = AXUIElementCopyAttributeValue(el, attr, None)
    return v if err == 0 else None


def kids(el):
    return list(ax(el, "AXChildren") or [])


def role(el):
    return ax(el, "AXRole")


def find_all(el, r, depth=6):
    out = []
    for k in kids(el):
        if role(k) == r:
            out.append(k)
        if depth > 0:
            out.extend(find_all(k, r, depth - 1))
    return out


def first_child(el, r):
    return next((k for k in kids(el) if role(k) == r), None)


def pos_x(el):
    p = ax(el, "AXPosition")
    if p is None:
        return None
    # AXValueRef → "x:.. y:.." ; pyobjc가 CGPoint로 풀어주지 않으면 문자열 파싱
    try:
        from ApplicationServices import AXValueGetValue, kAXValueCGPointType
        ok, pt = AXValueGetValue(p, kAXValueCGPointType, None)
        return pt.x if ok else None
    except Exception:
        s = str(p)
        try:
            return float(s.split("x:")[1].split()[0])
        except Exception:
            return None


def size_w(el):
    s = ax(el, "AXSize")
    if s is None:
        return None
    try:
        from ApplicationServices import AXValueGetValue, kAXValueCGSizeType
        ok, sz = AXValueGetValue(s, kAXValueCGSizeType, None)
        return sz.width if ok else None
    except Exception:
        return None


def size_h(el):
    s = ax(el, "AXSize")
    if s is None:
        return None
    from ApplicationServices import AXValueGetValue, kAXValueCGSizeType
    ok, sz = AXValueGetValue(s, kAXValueCGSizeType, None)
    return sz.height if ok else None


def frame(el):
    from ApplicationServices import AXValueGetValue, kAXValueCGPointType, kAXValueCGSizeType
    _, p = AXValueGetValue(ax(el, "AXPosition"), kAXValueCGPointType, None)
    _, z = AXValueGetValue(ax(el, "AXSize"), kAXValueCGSizeType, None)
    return p.x, p.y, z.width, z.height


def strip_el(msgs):
    return [{k: v for k, v in m.items() if k != "_el"} for m in msgs]


# ---------- 상태 ----------
def load_allowed() -> list[str]:
    try:
        return list(json.loads(CONFIG.read_text())["allowed_chats"])
    except FileNotFoundError:
        raise KakaoError(f"설정 파일이 없습니다: {CONFIG} (config.example.json을 복사해 허용할 방 이름을 적으세요)")


def check_allowed(chat: str):
    if chat not in load_allowed():
        raise KakaoError(f"허용되지 않은 채팅방입니다: {chat!r}")


def screen_locked() -> bool:
    d = Quartz.CGSessionCopyCurrentDictionary() or {}
    return bool(d.get("CGSSessionScreenIsLocked"))


def kakao_pid(launch=True) -> int:
    def pid():
        r = subprocess.run(["pgrep", "-x", "KakaoTalk"], capture_output=True, text=True)
        return int(r.stdout.split()[0]) if r.stdout.strip() else None

    p = pid()
    if p is None and launch:
        subprocess.run(["open", "-b", BUNDLE])
        for _ in range(20):
            time.sleep(0.5)
            if (p := pid()):
                time.sleep(2)
                break
    if p is None:
        raise KakaoError("카카오톡이 실행 중이 아닙니다.")
    return p


def app_el():
    if screen_locked():
        raise KakaoError("LOCKED: 화면이 잠겨 있어 macOS가 카톡 창 접근을 막습니다. 잠금을 풀어 주세요.")
    return AXUIElementCreateApplication(kakao_pid())


def windows(app):
    return [w for w in (ax(app, "AXWindows") or []) if role(w) == "AXWindow"]


def window_by_title(app, title):
    return next((w for w in windows(app) if ax(w, "AXTitle") == title), None)


def press_return(pid):
    for down in (True, False):
        ev = Quartz.CGEventCreateKeyboardEvent(None, RETURN_KEY, down)
        Quartz.CGEventPostToPid(pid, ev)
        time.sleep(0.03)


def activate(app, win):
    AXUIElementSetAttributeValue(app, "AXFrontmost", True)
    AXUIElementPerformAction(win, "AXRaise")
    time.sleep(0.15)


def main_window(app):
    w = window_by_title(app, MAIN_TITLE)
    if w is None:
        subprocess.run(["open", "-b", BUNDLE])  # 메인 창 재오픈
        for _ in range(10):
            time.sleep(0.4)
            if (w := window_by_title(app, MAIN_TITLE)):
                break
    if w is None:
        raise KakaoError("카카오톡 메인 창을 열 수 없습니다.")
    return w


def chat_rows(app):
    mw = main_window(app)
    sa = first_child(mw, "AXScrollArea")
    table = sa and first_child(sa, "AXTable")
    if table is None:
        raise KakaoError("채팅 목록을 찾지 못했습니다. 메인 창이 '채팅' 탭인지 확인해 주세요.")
    return mw, table, [r for r in kids(table) if role(r) == "AXRow"]


def row_texts(row):
    cell = (kids(row) or [row])[0]
    return [ax(t, "AXValue") or ax(t, "AXTitle") or "" for t in kids(cell) if role(t) == "AXStaticText"]


def ensure_chat_window(chat: str):
    """채팅방 창을 돌려준다. 닫혀 있으면 채팅 목록에서 찾아 다시 연다."""
    app = app_el()
    w = window_by_title(app, chat)
    if w is not None:
        return app, w
    mw, table, rows = chat_rows(app)
    target = next((r for r in rows if (row_texts(r) or [""])[0] == chat), None)
    if target is None:
        raise KakaoError(f"채팅 목록에서 {chat!r} 방을 찾지 못했습니다.")
    AXUIElementSetAttributeValue(target, "AXSelected", True)
    activate(app, mw)
    press_return(kakao_pid())
    for _ in range(15):
        time.sleep(0.2)
        if (w := window_by_title(app, chat)):
            return app, w
    raise KakaoError(f"{chat!r} 방 창을 여는 데 실패했습니다.")


# ---------- 메시지 파싱 ----------
def parse_messages(win, limit):
    sa = first_child(win, "AXScrollArea")
    table = sa and first_child(sa, "AXTable")
    if table is None:
        raise KakaoError("메시지 목록을 찾지 못했습니다.")
    wx, ww = pos_x(win), size_w(win)
    rows = [r for r in kids(table) if role(r) == "AXRow"]
    rows = rows[-(limit + 15):]  # 끝부분만 읽음(AX 호출이 느림). 여유분은 연속 메시지 보낸 사람 추적용
    msgs, sender = [], None
    for r in rows:
        cell = (kids(r) or [r])[0]
        items = kids(cell)
        texts = [str(ax(t, "AXValue") or "") for t in items if role(t) == "AXStaticText"]
        bodies = [t for t in items if role(t) == "AXTextArea"]
        has_profile = any(role(t) == "AXButton" and ax(t, "AXDescription") == "프로필" for t in items)
        big_imgs = [t for t in items if role(t) == "AXImage" and (size_h(t) or 0) > 45]
        is_image = not bodies and bool(big_imgs)
        if not bodies and not is_image:
            if texts:  # 날짜 구분선, 입장/퇴장 안내 등
                msgs.append({"type": "system", "text": " ".join(texts)})
            continue
        body = bodies[0] if bodies else big_imgs[-1]
        text = (ax(body, "AXValue") or "") if bodies else "[이미지]"
        # 상대 말풍선은 항상 창 왼쪽 x+52에서 시작, 내 말풍선은 오른쪽 끝에 붙음(왼쪽이 더 안쪽)
        bubble = big_imgs[-1] if is_image else next((t for t in items if role(t) == "AXImage"), body)
        bx = pos_x(bubble)
        mine = bool(wx is not None and bx is not None and bx > wx + 65 and not has_profile)
        time_raw = next((t for t in texts if t.split("\n")[-1].startswith(("오전", "오후"))), None)
        time_s = time_raw.split("\n")[-1] if time_raw else None
        unread = time_raw.split("\n")[0] if time_raw and "\n" in time_raw else None
        bare_num = next((t for t in texts if t.strip().isdigit()), None)  # 시각 없이 안 읽은 수만
        unread = unread or bare_num
        names = [t for t in texts if t not in (time_raw, bare_num)]
        if mine:
            who = "나"
        elif names:
            sender = who = names[0]
        else:
            who = sender  # 연속 메시지는 이름이 생략됨
        msg = {"type": "image" if is_image else "message", "sender": who, "time": time_s, "text": text}
        if unread:
            msg["unread"] = unread
        if is_image:
            msg["_el"] = body  # save_image용, 직렬화 전에 제거
        msgs.append(msg)
    return msgs[-limit:]


# ---------- 도구 ----------
@mcp.tool()
def list_chats() -> str:
    """허용된 채팅방 목록과 목록에 보이는 부가 정보(인원·시각·안 읽은 수 원문)를 돌려준다."""
    allowed = load_allowed()
    try:
        app = app_el()
        _, _, rows = chat_rows(app)
        info = {}
        for r in rows:
            t = row_texts(r)
            if t and t[0] in allowed:
                info[t[0]] = t[1:]
        return json.dumps(
            [{"chat": c, "in_list": c in info, "meta": info.get(c)} for c in allowed],
            ensure_ascii=False,
        )
    except KakaoError as e:
        return f"오류: {e}"


@mcp.tool()
def save_image(chat: str, index: int = -1) -> str:
    """허용된 채팅방에서 화면에 보이는 이미지 메시지를 PNG로 저장하고 경로를 돌려준다.
    index는 read_messages에 나온 이미지들 중 몇 번째인지(-1=가장 최근). 저장된 파일은 Read로 볼 수 있다.
    창 안에 보이는 이미지만 가능(화면 녹화 권한 필요)."""
    try:
        check_allowed(chat)
        with UI_LOCK:
            app, w = ensure_chat_window(chat)
            imgs = [m for m in parse_messages(w, 60) if m["type"] == "image"]
            if not imgs:
                raise KakaoError("창에 보이는 범위에 이미지 메시지가 없습니다.")
            try:
                m = imgs[index]
            except IndexError:
                raise KakaoError(f"이미지는 {len(imgs)}개입니다.")
            x, y, wd, ht = frame(m["_el"])
            wx, wy, ww, wh = frame(w)
            if y < wy or y + ht > wy + wh:
                raise KakaoError("이미지가 창 밖으로 스크롤돼 있어 캡처할 수 없습니다.")
            wins = Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionAll, Quartz.kCGNullWindowID)
            wid = next((i["kCGWindowNumber"] for i in wins
                        if i.get("kCGWindowOwnerPID") == kakao_pid() and i.get("kCGWindowName") == chat), None)
            if wid is None:
                raise KakaoError("창 번호를 찾지 못했습니다.")
            img = Quartz.CGWindowListCreateImage(Quartz.CGRectMake(x, y, wd, ht),
                                                 Quartz.kCGWindowListOptionIncludingWindow, wid,
                                                 Quartz.kCGWindowImageBestResolution)
            if img is None:
                raise KakaoError("캡처 실패(화면 녹화 권한을 확인하세요).")
        IMG_DIR.mkdir(exist_ok=True)
        out = IMG_DIR / f"{chat}_{time.strftime('%m%d_%H%M%S')}.png"
        url = Quartz.CFURLCreateWithFileSystemPath(None, str(out), 0, False)
        dst = Quartz.CGImageDestinationCreateWithURL(url, "public.png", 1, None)
        Quartz.CGImageDestinationAddImage(dst, img, None)
        Quartz.CGImageDestinationFinalize(dst)
        meta = {k: v for k, v in m.items() if k != "_el"}
        return json.dumps({"path": str(out), "message": meta}, ensure_ascii=False)
    except KakaoError as e:
        return f"오류: {e}"


def _msg_key(m):
    return (m.get("sender"), m.get("time"), m.get("text"))


def _norm(t):
    return " ".join((t or "").split())


def _notif_recent(chat, limit):
    try:
        return notif.for_chat(notif.kakao_notifications(0, 400), chat)[-limit:]
    except Exception:
        return []


@mcp.tool()
def read_messages(chat: str, limit: int = 30) -> str:
    """허용된 채팅방의 최근 메시지를 읽는다(창이 닫혀 있으면 다시 연다). limit 최대 200.
    화면이 잠겨 있으면 알림 센터에 남은 카톡 알림으로 대신 돌려준다(source=notification, 일부만 보임)."""
    try:
        check_allowed(chat)
        if screen_locked():
            got = _notif_recent(chat, limit)
            return json.dumps({"note": "화면 잠김: 알림 센터에 남은 알림만 보입니다(알림 꺼진 방·내 메시지·지운 알림은 없음).",
                               "messages": got}, ensure_ascii=False)
        with UI_LOCK:
            _, w = ensure_chat_window(chat)
            msgs = parse_messages(w, max(1, min(limit, 200)))
        return json.dumps(strip_el(msgs), ensure_ascii=False)
    except KakaoError as e:
        return f"오류: {e}"


def _ax_snapshot(chat):
    with UI_LOCK:
        _, w = ensure_chat_window(chat)
        return [m for m in parse_messages(w, 20) if m["type"] in ("message", "image")]


def _ax_new(base, cur):
    keys = [_msg_key(m) for m in cur]
    if not base:
        return cur
    idx = max((i for i, k in enumerate(keys) if k == base[-1]), default=None)
    if idx is not None:
        return cur[idx + 1:]
    seen = set(base)
    return [m for m in cur if _msg_key(m) not in seen]


@mcp.tool()
def wait_new_messages(chat: str, timeout_sec: int = 60, only_others: bool = True) -> str:
    """허용된 채팅방에 새 메시지가 올라올 때까지 최대 timeout_sec(최대 600초) 기다렸다가 새 메시지만 돌려준다.
    두 경로를 함께 본다: 알림 센터 DB(잠금 중에도 동작, 2초 간격)와 화면이 풀려 있을 때의 AX 읽기(알림 없는 경우 대비).
    알림으로 먼저 잡혀도 잠금이 풀려 있으면 AX로 다시 읽어 정확한 목록을 돌려준다. 시간 안에 없으면 빈 목록."""
    try:
        check_allowed(chat)
        timeout_sec = max(1, min(timeout_sec, 600))
        cursor = notif.latest_rec_id()
        base = None
        if not screen_locked():
            try:
                base = [_msg_key(m) for m in _ax_snapshot(chat)]
            except KakaoError:
                base = None
        deadline = time.time() + timeout_sec
        last_ax = time.time()
        while time.time() < deadline:
            time.sleep(2)
            # 1) 알림 DB: 싸고 잠금과 무관
            hits = []
            try:
                new_n = notif.kakao_notifications(cursor)
                if new_n:
                    cursor = new_n[-1]["rec_id"]
                    hits = notif.for_chat(new_n, chat)
            except Exception:
                pass
            locked = screen_locked()
            # 2) AX: 잠금이 풀려 있을 때, 알림이 왔거나 6초마다
            if not locked and (hits or time.time() - last_ax >= 6):
                last_ax = time.time()
                try:
                    cur = _ax_snapshot(chat)
                    if base is None:
                        base = [_msg_key(m) for m in cur]
                        new = []
                    else:
                        new = _ax_new(base, cur)
                    if only_others:
                        new = [m for m in new if m.get("sender") != "나"]
                    if new:
                        return json.dumps({"source": "ax", "messages": strip_el(new)}, ensure_ascii=False)
                except KakaoError:
                    pass
            if hits and locked:
                return json.dumps({"source": "notification",
                                   "note": "화면 잠김이라 알림 문구만 돌려줍니다. 잠금 해제 후 read_messages로 전체를 확인하세요.",
                                   "messages": hits}, ensure_ascii=False)
        return "[]"
    except KakaoError as e:
        return f"오류: {e}"


def _find_input(w):
    sas = [k for k in kids(w) if role(k) == "AXScrollArea"]
    if len(sas) >= 2 and (inp := first_child(sas[1], "AXTextArea")):
        return inp
    raise KakaoError("입력창을 찾지 못했습니다.")


def _send_now(chat: str, text: str) -> str:
    with UI_LOCK:
        app, w = ensure_chat_window(chat)
        inp = _find_input(w)
        activate(app, w)
        AXUIElementSetAttributeValue(inp, "AXFocused", True)
        AXUIElementSetAttributeValue(inp, "AXValue", text)
        time.sleep(0.15)
        press_return(kakao_pid())
        # 검증: 입력창이 비고, 마지막 내 메시지들 중에 같은 본문이 있어야 성공
        want = _norm(text)
        for _ in range(10):
            time.sleep(0.3)
            if (ax(inp, "AXValue") or "").strip():
                continue
            tail = parse_messages(w, 8)
            if any(m.get("sender") == "나" and _norm(m.get("text")) == want for m in tail):
                return f"보냄(방에서 확인됨): {chat}"
        if (ax(inp, "AXValue") or "").strip():
            raise KakaoError("입력창에 글이 남아 있습니다. 전송되지 않았습니다.")
        raise KakaoError("입력창은 비었지만 방 최근 메시지에서 보낸 글을 확인하지 못했습니다. read_messages로 직접 확인하세요.")


# ---------- 잠금 중 대기열 ----------
def _queue_load():
    try:
        return [json.loads(l) for l in QUEUE.read_text().splitlines() if l.strip()]
    except FileNotFoundError:
        return []


def _queue_save(items):
    QUEUE.write_text("".join(json.dumps(i, ensure_ascii=False) + "\n" for i in items))


def flush_queue() -> list[str]:
    """잠금이 풀려 있으면 대기열을 순서대로 보낸다. 허용 목록에서 빠진 방은 버린다."""
    if screen_locked():
        return []
    with QUEUE_LOCK:
        items = _queue_load()
        if not items:
            return []
        done, left = [], []
        allowed = load_allowed()
        for i, it in enumerate(items):
            if it["chat"] not in allowed:
                done.append(f"버림(허용 목록에서 빠짐): {it['chat']}")
                continue
            try:
                done.append(_send_now(it["chat"], it["text"]) + f" [대기열, {it['queued_at']} 접수]")
            except KakaoError as e:
                if str(e).startswith("LOCKED"):
                    left = items[i:]
                    break
                done.append(f"실패: {it['chat']}: {e}")
        _queue_save(left)
        if done:
            with SENT_LOG.open("a") as f:
                for d in done:
                    f.write(time.strftime("%m/%d %H:%M:%S ") + d + "\n")
        return done


def _queue_worker():
    while True:
        time.sleep(5)
        try:
            flush_queue()
        except Exception:
            pass


@mcp.tool()
def send_message(chat: str, text: str) -> str:
    """허용된 채팅방에 메시지를 보낸다(창이 닫혀 있으면 다시 연다). 보낸 뒤 방을 다시 읽어 실제로 올라갔는지 확인한다.
    화면이 잠겨 있으면 대기열에 넣고, 잠금이 풀리면 자동으로 보낸다."""
    try:
        check_allowed(chat)
        if not text.strip():
            raise KakaoError("빈 메시지는 보낼 수 없습니다.")
        if screen_locked():
            with QUEUE_LOCK:
                items = _queue_load()
                items.append({"chat": chat, "text": text, "queued_at": time.strftime("%m/%d %H:%M")})
                _queue_save(items)
            return f"대기열에 넣음(화면 잠김, 대기 {len(items)}건): 잠금이 풀리면 자동 전송됩니다. 아직 보내지지 않았습니다."
        flush_queue()  # 순서 보장: 먼저 쌓인 것부터
        return _send_now(chat, text)
    except KakaoError as e:
        return f"오류: {e}"


@mcp.tool()
def pending_messages(clear: bool = False) -> str:
    """잠금 때문에 대기 중인 메시지와 최근 대기열 전송 기록을 보여준다. clear=True면 대기열을 비운다(보내지 않음)."""
    with QUEUE_LOCK:
        items = _queue_load()
        if clear:
            _queue_save([])
    log = SENT_LOG.read_text().splitlines()[-10:] if SENT_LOG.exists() else []
    return json.dumps({"pending": items, "cleared": clear, "recent_queue_log": log}, ensure_ascii=False)


if __name__ == "__main__":
    threading.Thread(target=_queue_worker, daemon=True).start()
    mcp.run()
