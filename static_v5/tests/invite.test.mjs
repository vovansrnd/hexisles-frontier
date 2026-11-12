import test from "node:test";
import assert from "node:assert/strict";

import { installDom } from "./dom_stub.mjs";
installDom();

const { inviteUrl, copyText } = await import("../js/lib/invite.js");
const { createLobby } = await import("../js/ui/lobby.js");

test("inviteUrl builds a full link with the room code", () => {
  assert.equal(
    inviteUrl("http://127.0.0.1:8080", "/", "pqnihr"),
    "http://127.0.0.1:8080/?join=PQNIHR"
  );
  assert.equal(
    inviteUrl("http://192.168.1.193:8080", "/index.html", "AB12CD", { ws: "ws://192.168.1.193:8765" }),
    "http://192.168.1.193:8080/index.html?ws=ws%3A%2F%2F192.168.1.193%3A8765&join=AB12CD"
  );
  assert.equal(inviteUrl("http://x", "/", ""), "http://x/?join=");
});

test("copyText: clipboard API success uses no legacy fallback", async () => {
  const calls = { api: 0, exec: 0, notify: [] };
  await copyText("L1", {
    clipboard: { writeText: (t) => { calls.api++; return Promise.resolve(); } },
    execCommand: () => { calls.exec++; return true; },
    notify: (t, ok) => calls.notify.push([t, ok]),
  });
  await Promise.resolve();
  assert.deepEqual(calls, { api: 1, exec: 0, notify: [["L1", true]] });
});

test("copyText: clipboard API rejection falls back to execCommand", async () => {
  const calls = { api: 0, exec: 0, notify: [] };
  const rejected = copyText("L2", {
    clipboard: { writeText: () => Promise.reject(new Error("denied")) },
    execCommand: (t) => { calls.exec++; assert.equal(t, "L2"); return true; },
    notify: (t, ok) => calls.notify.push([t, ok]),
  });
  await rejected; await Promise.resolve(); await Promise.resolve();
  assert.equal(calls.exec, 1);
  assert.deepEqual(calls.notify, [["L2", true]]);
});

test("copyText: missing clipboard API still copies via execCommand (LAN HTTP)", () => {
  // The regression: with navigator.clipboard entirely absent the copy must
  // still be attempted through the legacy path, not just displayed.
  const calls = { exec: 0, notify: [] };
  copyText("L3", {
    clipboard: null,
    execCommand: (t) => { calls.exec++; assert.equal(t, "L3"); return true; },
    notify: (t, ok) => calls.notify.push([t, ok]),
  });
  assert.equal(calls.exec, 1);
  assert.deepEqual(calls.notify, [["L3", true]]);
});

test("copyText: every path failing notifies with the text as the last resort", () => {
  const calls = { notify: [] };
  copyText("L4", {
    clipboard: undefined,
    execCommand: () => false,
    notify: (t, ok) => calls.notify.push([t, ok]),
  });
  copyText("L5", { clipboard: undefined, execCommand: null, notify: (t, ok) => calls.notify.push([t, ok]) });
  assert.deepEqual(calls.notify, [["L4", false], ["L5", false]]);
});

test("lobby: setInvite and setResume drive the UI", async () => {
  const { FakeElement } = await import("./dom_stub.mjs");
  const host = new FakeElement("main");
  const events = { join: null, resume: 0 };
  const lobby = createLobby(host, {
    getName: () => "Хост",
    onCreate: () => {},
    onJoin: (payload) => { events.join = payload; },
    onResume: () => { events.resume++; },
  });

  lobby.setInvite("PQNIHR");
  const roomInput = host.querySelector("#room");
  assert.ok(roomInput, "join tab room input rendered");
  assert.equal(roomInput.getAttribute("value"), "PQNIHR");
  const note = host.querySelector(".invite-note");
  assert.ok(note, "invite note rendered");
  assert.equal(note.getAttribute("hidden"), null, "invite note visible");
  assert.match(host.textContent, /PQNIHR/);

  // Typing a name and pressing the button joins with the prefilled code.
  const nameInput = host.querySelector("#pname");
  nameInput.value = "Гость";
  nameInput.dispatch("input");
  const joinBtn = [...host.querySelectorAll("button")].find((b) => /Присоединиться/.test(b.textContent));
  joinBtn.click();
  assert.deepEqual(events.join, { name: "Гость", room_id: "PQNIHR" });

  lobby.setResume({ room_id: "KZ01", player_id: 0 });
  assert.match(host.textContent, /KZ01/);
  const resumeBtn = [...host.querySelectorAll("button")].find((b) => /Продолжить игру/.test(b.textContent));
  assert.ok(resumeBtn, "resume button rendered after setResume");
  resumeBtn.click();
  assert.equal(events.resume, 1);
});


test("lobby describes the deduction experiment honestly", async () => {
  const { FakeElement } = await import("./dom_stub.mjs");
  const host = new FakeElement("main");
  createLobby(host, { getName: () => "Проверка", onCreate: () => {}, onJoin: () => {}, onResume: () => {} });

  const mode = host.querySelector("#blight-mode");
  assert.ok(mode);
  mode.dispatch("change", { target: { value: "factions" } });
  const visibility = host.querySelector("#cult-visibility");
  assert.ok(visibility, "faction visibility choice becomes available");
  visibility.dispatch("change", { target: { value: "hidden" } });

  assert.match(host.textContent, /Дедукция · эксперимент/);
  assert.match(host.textContent, /не тайная роль/);
  assert.match(host.textContent, /можно вычислить по публичным действиям/);
  assert.doesNotMatch(host.textContent, /Скрытая · обличение/);
});


test("lobby: friends mode creates a room (no bots, no auto-start); bots mode starts at once", async () => {
  const { FakeElement } = await import("./dom_stub.mjs");
  const host = new FakeElement("main"); const calls = [];
  createLobby(host, { getName: () => "Тест", onCreate: (cfg) => calls.push(cfg), onJoin: () => {}, onResume: () => {} });
  host.querySelector("#create-btn").click();
  host.querySelector("#mode-friends").click();
  host.querySelector("#create-btn").click();
  assert.equal(calls[0].start_immediately, true);
  assert.equal(calls[1].start_immediately, false);
  assert.deepEqual(calls[1].bot_types, []);
  assert.equal(calls[1].blight_mode, "factions");
  assert.equal(calls[1].cult_visibility, "open");
});
