// @ts-check
/**
 * Service worker: cola persistente (chrome.storage.local) + envío al backend.
 *
 *   content.js --mensaje--> cola local --lotes--> POST /events/watch
 *
 * Si el backend está caído, los eventos quedan en la cola y se reintentan
 * cada minuto (y cada vez que llega un lote nuevo). El backend es idempotente
 * (session_id + seq), así que reenviar un lote nunca duplica tiempo.
 */
"use strict";

const DEFAULT_BACKEND_URL = "http://127.0.0.1:8000";
const QUEUE_KEY = "pendingEvents";
const MAX_QUEUE_LENGTH = 100000; // ~ varios días offline
const BATCH_SIZE = 500;
const REQUEST_TIMEOUT_MS = 10000;

// Serializa el acceso a storage para que dos operaciones no se pisen la cola.
/** @type {Promise<unknown>} */
let lock = Promise.resolve();
/** @template T @param {() => Promise<T>} fn @returns {Promise<T>} */
function serialized(fn) {
  const run = lock.then(fn, fn);
  lock = run.catch(() => undefined);
  return run;
}

/** @returns {Promise<string>} */
async function backendUrl() {
  const { backendUrl } = await chrome.storage.local.get("backendUrl");
  return backendUrl || DEFAULT_BACKEND_URL;
}

/** @returns {Promise<any[]>} */
async function readQueue() {
  const stored = await chrome.storage.local.get(QUEUE_KEY);
  return stored[QUEUE_KEY] || [];
}

/** @param {any[]} events */
async function enqueue(events) {
  let queue = (await readQueue()).concat(events);
  if (queue.length > MAX_QUEUE_LENGTH) queue = queue.slice(queue.length - MAX_QUEUE_LENGTH);
  await chrome.storage.local.set({ [QUEUE_KEY]: queue });
}

async function flushQueue() {
  const url = (await backendUrl()) + "/events/watch";
  for (;;) {
    const queue = await readQueue();
    if (queue.length === 0) return;
    const batch = queue.slice(0, BATCH_SIZE);

    let response;
    try {
      response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ events: batch }),
        signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
      });
    } catch (err) {
      console.info("[CI Tracker] backend unreachable, keeping %d events queued", queue.length);
      return;
    }

    if (response.status === 422) {
      // Lote inválido: reintentar no lo va a arreglar. Lo descartamos para no bloquear la cola.
      console.error("[CI Tracker] backend rejected batch", await response.text());
    } else if (!response.ok) {
      console.warn("[CI Tracker] backend error %d, will retry", response.status);
      return;
    }
    const remaining = (await readQueue()).slice(batch.length);
    await chrome.storage.local.set({ [QUEUE_KEY]: remaining });
  }
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type !== "watch-events" || !Array.isArray(message.events)) return false;
  serialized(() => enqueue(message.events))
    .then(() => sendResponse({ ok: true }))
    .then(() => serialized(flushQueue));
  return true; // respuesta asíncrona
});

chrome.alarms.create("flush-queue", { periodInMinutes: 1 });
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "flush-queue") serialized(flushQueue);
});
