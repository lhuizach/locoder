# locoder mobile - plan

Code with locoder from your phone. The model, your files and every command
stay on your PC; the phone is a remote screen and keyboard.

Status: built. `locoder serve` (locoder/serve/), the web app
(locoder/serve/web/index.html) and the Android app (mobile/android) work end
to end. The original clickable prototype (fake data, scripted AI) is kept in
`prototype/` for reference.

What changed from the sketch below while building it:
- Live events are server-sent events over a streaming GET
  (`/api/events?since=<seq>`), not a WebSocket - standard library only, and
  a phone that drops off replays what it missed by sequence number.
- No QR code yet: the Android app finds the PC by UDP broadcast ("locoder?"
  on the serve port), or you type the address; then the 6-digit code.
- `PATCH /api/state {mode, model, temperature, yolo}`; new chats are
  `POST /api/chats {text}`, which creates the chat and starts the task.
- One task at a time per PC (a local model answers one request at once).

## Pieces

1. **`locoder serve`** (Python, on the PC) - runs the same agent loop as the
   CLI behind a small HTTP + WebSocket API. A `WebUI` class implements the
   same UI calls as the terminal UIs (`stream_token`, `tool_call`,
   `confirm`, ...) and forwards them to the phone as events. Uses the same
   `.locoder/sessions/` files, so chats show up in both places.
2. **The web app** (HTML/JS, served by `locoder serve`) - the prototype made
   real. Works in any phone browser and can be added to the home screen.
3. **The Android app** (Kotlin) - a thin wrapper: a WebView showing the web
   app, a native QR scanner for pairing, and the saved pairing key in
   Android's encrypted storage. Builds to an APK.

## Pairing

- `locoder serve` listens on your local network (and on Tailscale if you
  have it) and prints a QR code plus a 6-digit code in the terminal.
- The phone scans the QR (or you type the code); the PC hands back a
  long-lived random key. The code expires after a few minutes and works once.
- Every request needs that key. Unpair from the phone or with
  `locoder serve --unpair-all` on the PC.
- Nothing is exposed to the internet; for use away from home, Tailscale.

## API sketch

| what | call |
|---|---|
| pair | `POST /pair {code}` -> `{key, computer}` |
| chats | `GET /chats`, `POST /chats`, `PATCH /chats/{id} {title}`, `DELETE /chats/{id}` |
| open a chat | `GET /chats/{id}` -> messages |
| send | `POST /chats/{id}/messages {text, files}` |
| live events | `WS /events` -> token, thinking, tool_call, tool_result, confirm_request, done, error |
| approve/decline | `POST /confirm/{request_id} {approve}` |
| stop | `POST /chats/{id}/stop` |
| mode, model, yolo | `PATCH /session {mode, model, yolo}`; `GET /models` (from Ollama) |
| projects, files | `GET /projects`, `POST /projects/switch`, `GET /files/recent` |
| settings | `GET/PATCH /settings` |

New on the PC side: chat titles you can rename (stored in the session file;
today the title is just the first message), and deleting a chat.

## Notifications

The prototype already posts real Android notifications through a bridge
(`window.LocoderAndroid`, `Notifier.kt`): "locoder needs your OK" (high
priority, when an edit or command is waiting) and "finished" (with the start
of the answer), only while the app is in the background, cleared when you
come back. Settings has an on/off switch; Android 13+ asks for permission
right after pairing.

In the real app the work happens on the PC, so notifications must keep
coming with the app closed: a small Android foreground service keeps the
WebSocket to `locoder serve` open while a task is running and turns
`confirm_request` / `done` events into the same notifications. No Google
push servers involved, so it stays local.

## Build order

1. `locoder serve` + pairing + the API above, with tests. (done)
2. The web app wired to the API; usable from the phone browser. (done)
3. Android wrapper: WebView, discovery, notifications with Approve/Decline. (done)
4. Next: QR pairing, voice input, a key store backed by Android Keystore.
