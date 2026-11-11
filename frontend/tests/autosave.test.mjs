import test from "node:test";
import assert from "node:assert/strict";
import { Autosave, codePointLength } from "../src/state/autosave.ts";

function storage() {
  const values = new Map();
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key),
  };
}
function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const tick = () => new Promise((resolve) => setImmediate(resolve));
let keyNo = 0;
function options(overrides = {}) {
  return { key: `test-${++keyNo}`, initial: "", storage: storage(), isValue: (v) => typeof v === "string",
    valid: (v) => codePointLength(v) <= 20, save: async () => undefined, delay: 60_000, ...overrides };
}

test("synchronously retains an intentionally empty draft across reload", () => {
  const config = options({ initial: "server draft" });
  const first = new Autosave(config);
  first.update("");
  assert.equal(config.storage.getItem(config.key), '""');
  const restored = new Autosave(config);
  assert.equal(restored.getSnapshot().value, "");
  assert.equal(restored.getSnapshot().status, "pending");
  assert.equal(restored.getSnapshot().localSaved, true);
});

test("does not restore another stage's cached input", () => {
  const config = options({ initial: "server email" });
  config.storage.setItem(config.key, '"unfinished local email"');
  const inactive = new Autosave({ ...config, restore: false });
  assert.equal(inactive.getSnapshot().value, "server email");
});

test("flush sends the newest input after an older save, without replacing the editor", async () => {
  const firstRequest = deferred();
  const writes = [];
  const control = new Autosave(options({ save: async (value) => {
    writes.push(value);
    if (writes.length === 1) await firstRequest.promise;
  } }));
  control.setEnabled(true);
  control.update("first draft");
  const flushed = control.flush();
  await tick();
  control.update("newest draft");
  firstRequest.resolve();
  assert.equal(await flushed, "newest draft");
  assert.deepEqual(writes, ["first draft", "newest draft"]);
  assert.equal(control.getSnapshot().value, "newest draft");
  assert.equal(control.getSnapshot().status, "saved");
  control.setEnabled(false);
});

test("remounted editors serialize with an old in-flight save", async () => {
  const pending = deferred();
  const writes = [];
  const config = options({ save: async (value) => { writes.push(value); await pending.promise; } });
  const old = new Autosave(config);
  old.setEnabled(true);
  old.update("old");
  const oldFlush = old.flush();
  await tick();
  old.setEnabled(false);
  const fresh = new Autosave({ ...config, save: async (value) => { writes.push(value); } });
  fresh.setEnabled(true);
  fresh.update("latest");
  const newFlush = fresh.flush();
  await tick();
  assert.deepEqual(writes, ["old"]);
  pending.resolve();
  await Promise.all([oldFlush, newFlush]);
  assert.deepEqual(writes, ["old", "latest"]);
  assert.equal(fresh.getSnapshot().status, "saved");
  fresh.setEnabled(false);
});

test("failed saves retain local text and support an explicit successful retry", async () => {
  let fail = true;
  const config = options({ save: async () => { if (fail) throw new Error("offline"); } });
  const control = new Autosave(config);
  control.setEnabled(true);
  control.update("keep this");
  await assert.rejects(control.flush(), /offline/);
  assert.equal(control.getSnapshot().status, "error");
  assert.equal(control.getSnapshot().localSaved, true);
  assert.equal(JSON.parse(config.storage.getItem(config.key)), "keep this");
  fail = false;
  await control.flush();
  assert.equal(control.getSnapshot().status, "saved");
  control.setEnabled(false);
});

test("counts Unicode code points and keeps over-limit text without sending it", async () => {
  const writes = [];
  const config = options({ valid: (v) => codePointLength(v) <= 2, save: async (v) => writes.push(v) });
  const control = new Autosave(config);
  control.setEnabled(true);
  assert.equal(codePointLength("🙂a"), 2);
  control.update("🙂ab");
  assert.equal(control.getSnapshot().status, "blocked");
  assert.equal(control.getSnapshot().value, "🙂ab");
  await assert.rejects(control.flush(), /Shorten/);
  assert.deepEqual(writes, []);
  assert.equal(JSON.parse(config.storage.getItem(config.key)), "🙂ab");
  control.update("🙂a");
  await control.flush();
  assert.deepEqual(writes, ["🙂a"]);
  control.setEnabled(false);
});

test("pausing cancels pending autosaves when the email has been sent", async () => {
  const writes = [];
  const control = new Autosave(options({ delay: 5, save: async (v) => writes.push(v) }));
  control.setEnabled(true);
  control.update("pending");
  control.setEnabled(false);
  await new Promise((resolve) => setTimeout(resolve, 15));
  assert.deepEqual(writes, []);
});

test("a storage failure is visible while server saving can still succeed", async () => {
  const brokenStorage = { getItem: () => null, setItem: () => { throw new Error("storage disabled"); }, removeItem: () => undefined };
  const control = new Autosave(options({ storage: brokenStorage }));
  control.setEnabled(true);
  control.update("safe on server");
  assert.equal(control.getSnapshot().localSaved, false);
  await control.flush();
  assert.equal(control.getSnapshot().status, "saved");
  control.setEnabled(false);
});

test("prediction and backup are cached and saved as one current pair", async () => {
  const saved = [];
  const config = options({ initial: { prediction: "", backup: "" },
    isValue: (v) => !!v && typeof v.prediction === "string" && typeof v.backup === "string",
    valid: () => true, save: async (v) => saved.push(v) });
  const control = new Autosave(config);
  control.setEnabled(true);
  control.update({ prediction: "A question", backup: "" });
  control.update({ prediction: "A question", backup: "An outline" });
  await control.flush();
  assert.deepEqual(saved, [{ prediction: "A question", backup: "An outline" }]);
  control.discardCache();
  assert.equal(config.storage.getItem(config.key), null);
  control.setEnabled(false);
});
