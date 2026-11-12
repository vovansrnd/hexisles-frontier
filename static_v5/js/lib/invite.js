// Invite-link helpers, kept dependency-free so tests can stub the browser APIs.

/** Build a full invite URL: origin + pathname + kept params + ?join=ROOM. */
export function inviteUrl(origin, pathname, roomId, keepParams = {}) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(keepParams)) {
    if (value != null && value !== "") search.set(key, String(value));
  }
  search.set("join", String(roomId || "").toUpperCase());
  return `${origin}${pathname}?${search.toString()}`;
}

/**
 * Copy text with a full fallback chain:
 * 1. async Clipboard API when present and it resolves,
 * 2. document.execCommand("copy") on a temporary textarea when the API is
 *    missing OR rejects (plain HTTP on a LAN address often has no clipboard API),
 * 3. notify(text, false) as the last resort so the user still sees the link.
 *
 * `clipboard` is a navigator.clipboard-like object, `execCommand(text)` must
 * perform the legacy copy attempt (creating the textarea is the caller's job —
 * it needs the real DOM), `notify(text, ok)` reports the outcome.
 */
export function copyText(text, { clipboard = null, execCommand = null, notify = () => {} } = {}) {
  const viaApi = clipboard && typeof clipboard.writeText === "function";
  if (viaApi) {
    clipboard.writeText(text).then(
      () => notify(text, true),
      () => copyViaExecCommand(text, execCommand, notify)
    );
    return;
  }
  copyViaExecCommand(text, execCommand, notify);
}

function copyViaExecCommand(text, execCommand, notify) {
  let ok = false;
  if (typeof execCommand === "function") {
    try {
      ok = !!execCommand(text);
    } catch (e) {
      ok = false;
    }
  }
  notify(text, ok);
}
