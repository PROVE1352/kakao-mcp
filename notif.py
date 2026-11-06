"""macOS 알림 센터 DB에서 카카오톡 알림 문구를 읽는다(화면 잠금과 무관하게 동작).

DB: ~/Library/Group Containers/group.com.apple.usernoted/db2/db (macOS 15+), 읽기 전용.
카톡 대화 DB는 건드리지 않는다. 알림 레코드의 data(바이너리 plist, req.titl/subt/body)만 해독한다.
한계: 알림이 꺼진 방·지금 보고 있는 방은 알림이 없고, 사용자가 알림을 지우면 기록도 사라진다.
"""
from __future__ import annotations

import plistlib
import shutil
import sqlite3
import tempfile
import urllib.parse
from pathlib import Path

DB = Path.home() / "Library/Group Containers/group.com.apple.usernoted/db2/db"
APP_ID = "com.kakao.kakaotalkmac"
MAC_EPOCH = 978307200


def _connect():
    uri = "file:" + urllib.parse.quote(str(DB)) + "?mode=ro"
    try:
        con = sqlite3.connect(uri, uri=True, timeout=3)
        con.execute("SELECT 1 FROM record LIMIT 1")
        return con, (lambda: None)
    except sqlite3.Error:
        tmp = tempfile.mkdtemp(prefix="kakaomcp.")
        for suf in ("", "-wal", "-shm"):
            src = Path(str(DB) + suf)
            if src.exists():
                shutil.copy2(src, Path(tmp) / ("db" + suf))
        return sqlite3.connect(str(Path(tmp) / "db")), (lambda: shutil.rmtree(tmp, ignore_errors=True))


def _unarchive(archive):
    objs = archive.get("$objects") or []

    def res(v, d=0):
        if d > 40:
            return None
        if isinstance(v, plistlib.UID):
            return res(objs[v.data], d + 1) if 0 <= v.data < len(objs) else None
        if isinstance(v, dict):
            if "NS.keys" in v and "NS.objects" in v:
                return {str(res(k, d + 1)): res(x, d + 1) for k, x in zip(v["NS.keys"], v["NS.objects"])}
            if "NS.objects" in v:
                return [res(x, d + 1) for x in v["NS.objects"]]
            if "NS.string" in v:
                return str(v["NS.string"])
            return {k: res(x, d + 1) for k, x in v.items() if k != "$class"}
        if isinstance(v, list):
            return [res(x, d + 1) for x in v]
        return None if v == "$null" else v

    top = archive.get("$top") or {}
    return res(top.get("root", next(iter(top.values()), None)) if isinstance(top, dict) else None)


def _decode(blob):
    try:
        obj = plistlib.loads(bytes(blob))
        if isinstance(obj, dict) and "$objects" in obj:
            obj = _unarchive(obj)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    req = obj.get("req") if isinstance(obj.get("req"), dict) else {}
    s = lambda k: req.get(k) if isinstance(req.get(k), str) else ""
    return {"title": s("titl"), "subtitle": s("subt"), "body": s("body")}


def kakao_notifications(after_rec_id: int = 0, limit: int = 200) -> list[dict]:
    """rec_id 가 after_rec_id 보다 큰 카톡 알림(오래된 순)."""
    if not DB.exists():
        return []
    con, cleanup = _connect()
    try:
        rows = con.execute(
            "SELECT r.rec_id, r.delivered_date, r.data FROM record r JOIN app a ON a.app_id=r.app_id "
            "WHERE lower(a.identifier)=? AND r.rec_id>? ORDER BY r.rec_id DESC LIMIT ?",
            (APP_ID, after_rec_id, limit),
        ).fetchall()
    finally:
        con.close()
        cleanup()
    out = []
    for rec_id, dd, blob in reversed(rows):
        d = _decode(blob)
        if d:
            d["rec_id"] = rec_id
            d["epoch"] = (dd or 0) + MAC_EPOCH
            out.append(d)
    return out


def latest_rec_id() -> int:
    if not DB.exists():
        return 0
    con, cleanup = _connect()
    try:
        r = con.execute("SELECT max(rec_id) FROM record").fetchone()
        return r[0] or 0
    finally:
        con.close()
        cleanup()


def for_chat(notifs: list[dict], chat: str) -> list[dict]:
    """카톡 단체방 알림은 제목/부제목 중 하나가 방 이름. 나머지 하나를 보낸 사람으로 본다(형식 미확인 → 원문도 남김)."""
    out = []
    for n in notifs:
        if n["title"] == chat:
            sender = n["subtitle"] or None
        elif n["subtitle"] == chat:
            sender = n["title"] or None
        else:
            continue
        out.append({"type": "message", "sender": sender, "text": n["body"], "source": "notification",
                    "rec_id": n["rec_id"], "epoch": round(n["epoch"])})
    return out
