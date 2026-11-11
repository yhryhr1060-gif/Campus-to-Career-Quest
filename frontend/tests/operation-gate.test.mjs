import assert from "node:assert/strict";
import test from "node:test";
import { OperationGate } from "../src/state/operationGate.ts";

test("two same-tick clicks start only one operation before React renders busy", async () => {
  const gate = new OperationGate();
  gate.activate();
  let release;
  const deferred = new Promise((resolve) => { release = resolve; });
  let calls = 0;
  const click = async () => {
    const token = gate.begin();
    if (token === null) return;
    calls++;
    await deferred;
    gate.finish(token);
  };
  const first = click();
  const second = click();
  assert.equal(calls, 1);
  release();
  await Promise.all([first, second]);
  assert.notEqual(gate.begin(), null, "an explicit later retry is allowed");
});

test("a late result cannot replace new state after leaving and reentering the page", async () => {
  const gate = new OperationGate();
  gate.activate();
  const old = gate.begin();
  let release;
  const deferred = new Promise((resolve) => { release = resolve; });
  let displayed = "new version";
  const pending = deferred.then(() => { if (gate.accepts(old)) displayed = "old version"; });
  gate.deactivate();
  gate.activate();
  const current = gate.begin();
  release();
  await pending;
  assert.equal(displayed, "new version");
  assert.equal(gate.accepts(old), false);
  assert.equal(gate.accepts(current), true);
  gate.finish(old);
  assert.equal(gate.begin(), null, "the old completion must not release the newer operation's lock");
});

test("unmounted pages cannot start or accept operations", () => {
  const gate = new OperationGate();
  assert.equal(gate.begin(), null);
  gate.activate();
  const token = gate.begin();
  gate.deactivate();
  assert.equal(gate.accepts(token), false);
  assert.equal(gate.begin(), null);
});
