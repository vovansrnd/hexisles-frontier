// WebSocket client: JSON envelope {action, ...}, FIFO-matched action results, heartbeat and
// automatic reconnect with exponential backoff (the session token lets the server resume us).

export function createNet({ url, getSession, onState, onEvent, onStatus }) {
  let ws = null, closedByUs = false, retry = 0, timer = null, ping = null, lastPong = 0;
  const pending = [];                       // resolvers for upcoming action_result messages

  function setStatus(s) { onStatus && onStatus(s); }

  function connect() {
    closedByUs = false;
    clearTimeout(timer);
    setStatus("connecting");
    try { ws = new WebSocket(url); } catch (e) { scheduleRetry(); return; }
    ws.onopen = () => {
      retry = 0; lastPong = Date.now(); setStatus("open");
      const s = getSession && getSession();
      if (s) raw({ action: "reconnect", room_id: s.room_id, player_id: s.player_id, reconnect_token: s.token });
      clearInterval(ping);
      ping = setInterval(() => {
        if (Date.now() - lastPong > 45000) { try { ws.close(); } catch (e) { /* ignore */ } return; }
        raw({ action: "ping" });
      }, 15000);
    };
    ws.onmessage = (ev) => {
      let msg; try { msg = JSON.parse(ev.data); } catch (e) { return; }
      lastPong = Date.now();
      if (msg.type === "state") onState && onState(msg.data);
      else if (msg.type === "action_result") { const r = pending.shift(); r && r(msg.result || { ok: false }); }
      else if (msg.type === "pong") { /* heartbeat */ }
      else onEvent && onEvent(msg);
    };
    ws.onclose = () => {
      clearInterval(ping);
      while (pending.length) pending.shift()({ ok: false, error: "Соединение потеряно" });
      setStatus("closed");
      if (!closedByUs) scheduleRetry();
    };
    ws.onerror = () => { /* onclose follows */ };
  }

  function scheduleRetry() {
    retry = Math.min(retry + 1, 6);
    timer = setTimeout(connect, Math.min(8000, 400 * 2 ** retry));
  }

  function raw(obj) {
    if (!ws || ws.readyState !== 1) return false;
    ws.send(JSON.stringify(obj)); return true;
  }

  return {
    connect,
    close() { closedByUs = true; clearTimeout(timer); clearInterval(ping); try { ws && ws.close(); } catch (e) { /* ignore */ } },
    send: raw,
    isOpen: () => !!ws && ws.readyState === 1,
    /** Sends a game action and resolves with the server's {ok, error?} result. */
    gameAction(type, params = {}) {
      return new Promise((resolve) => {
        if (!raw({ action: "game_action", game_action: type, params })) return resolve({ ok: false, error: "Нет соединения" });
        pending.push(resolve);
        setTimeout(() => { const i = pending.indexOf(resolve); if (i >= 0) { pending.splice(i, 1); resolve({ ok: false, error: "Сервер не ответил" }); } }, 8000);
      });
    },
  };
}
