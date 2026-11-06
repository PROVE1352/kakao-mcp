# kakao-mcp

macOS 카카오톡 앱을 **허용한 채팅방에 한해** 읽고 보내는 MCP 서버입니다. Claude Code 같은 MCP 클라이언트에서 씁니다.

스크린샷을 찍고 좌표를 누르는 방식이 아닙니다. macOS 접근성(Accessibility) API로 카톡 창의 UI 요소를 직접 읽고 씁니다. 새 메시지는 알림 센터 DB도 함께 봅니다. 카톡 프로토콜이나 대화 DB는 건드리지 않습니다.

## 도구

| 도구 | 하는 일 |
|---|---|
| `list_chats` | 허용된 방 목록과 채팅 목록에 보이는 정보(인원, 시각, 안 읽은 수) |
| `read_messages(chat, limit)` | 최근 메시지(보낸 사람, 시각, 본문, 이미지 여부). 방 창이 닫혀 있으면 다시 엽니다. |
| `send_message(chat, text)` | 메시지를 보낸 뒤 방을 다시 읽어 실제로 올라갔는지 확인합니다. 화면이 잠겨 있으면 대기열에 넣었다가 잠금이 풀리면 보냅니다. |
| `wait_new_messages(chat, timeout_sec)` | 새 메시지가 올 때까지 기다립니다. 알림 센터 DB(잠금 중에도 동작)와 접근성 읽기를 함께 씁니다. |
| `save_image(chat, index)` | 창에 보이는 이미지 메시지를 PNG로 저장하고 경로를 돌려줍니다. |
| `pending_messages(clear)` | 잠금 때문에 대기 중인 메시지를 보거나 비웁니다. |

`config.json`의 `allowed_chats`에 없는 방은 모든 도구가 거부합니다. 설정은 호출할 때마다 다시 읽어서, 바꿔도 재시작할 필요가 없습니다.

## 설치

```bash
git clone https://github.com/PROVE1352/kakao-mcp.git ~/kakao-mcp
cd ~/kakao-mcp
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp config.example.json config.json   # 허용할 방 이름을 정확히 적기
claude mcp add -s user kakaotalk -- ~/kakao-mcp/.venv/bin/python ~/kakao-mcp/server.py
```

macOS 권한은 MCP를 띄우는 앱(터미널 등)에 줍니다.

- **손쉬운 사용:** 필수. 읽기와 보내기에 씁니다.
- **화면 기록:** `save_image`에만 필요합니다.
- **전체 디스크 접근:** 알림 센터 DB 읽기에 필요합니다.

카카오톡 알림을 켜 두어야 알림 경로가 동작합니다.

## 한계

- **화면이 잠기면 접근성 읽기와 보내기가 안 됩니다.** macOS가 잠금 중에는 다른 앱의 창 트리를 숨기기 때문입니다. 잠금 중에는 다음처럼 동작합니다.
  - 읽기와 새 메시지 감지는 알림 센터 DB로 대신합니다. 알림이 꺼진 방, 지금 보고 있는 방, 지운 알림은 보이지 않습니다.
  - 보내기는 대기열에 넣습니다.
- 메시지는 끝에서부터 `limit`+15줄만 읽습니다. 접근성 호출이 한 줄에 약 0.1초 걸립니다.
- `save_image`는 창 밖으로 스크롤된 이미지를 캡처하지 못합니다.
- 카톡 앱 UI가 바뀌면 파싱이 깨질 수 있습니다. 확인한 버전은 2026년 11월 기준 맥 카카오톡과 macOS 26입니다.
- 비공식 자동화입니다. 카카오 이용약관을 확인하고, 본인 계정으로 개인 용도로만 쓰세요.

## 라이선스

MIT
