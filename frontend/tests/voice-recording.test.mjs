import test from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";

// Bundle the browser module for Node's native test runner; no browser or microphone is opened.
const bundled = await build({ entryPoints: [fileURLToPath(new URL("../src/audio/recording.ts", import.meta.url))],
  bundle: true, write: false, format: "esm", platform: "node" });
const { recordStream } = await import(`data:text/javascript;base64,${Buffer.from(bundled.outputFiles[0].text).toString("base64")}`);

function fakeCapture(t) {
  class Track extends EventTarget {
    stopped = false;
    stop() { this.stopped = true; }
  }
  class Recorder {
    static latest;
    static isTypeSupported() { return true; }
    state = "inactive";
    mimeType = "audio/webm";
    stopCalls = 0;
    constructor() { Recorder.latest = this; }
    start() { this.state = "recording"; }
    stop() {
      this.stopCalls++;
      this.state = "inactive";
      this.ondataavailable?.({ data: new Blob(["complete encoded recording"]) });
      this.onstop?.();
    }
  }
  const previous = globalThis.MediaRecorder;
  globalThis.MediaRecorder = Recorder;
  t.after(() => { if (previous === undefined) delete globalThis.MediaRecorder; else globalThis.MediaRecorder = previous; });
  t.mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
  const track = new Track();
  const completed = [];
  const errors = [];
  const control = recordStream({ getTracks: () => [track] }, {
    maxSeconds: 20, onElapsed: () => undefined,
    onComplete: (blob) => completed.push(blob), onError: (error) => errors.push(error),
  });
  return { control, track, completed, errors, recorder: Recorder.latest };
}

test("automatically stops at 19.5 seconds, leaving a 0.5-second encoding margin", (t) => {
  const capture = fakeCapture(t);
  t.mock.timers.tick(19499);
  assert.equal(capture.recorder.stopCalls, 0);
  assert.equal(capture.track.stopped, false);
  t.mock.timers.tick(1);
  assert.equal(capture.recorder.stopCalls, 1);
  assert.equal(capture.track.stopped, true);
  assert.equal(capture.completed.length, 1);
  assert.equal(capture.errors.length, 0);
  t.mock.timers.tick(20000);
  assert.equal(capture.recorder.stopCalls, 1);
});

test("cancelling releases the microphone and never transcribes a late stop event", (t) => {
  const capture = fakeCapture(t);
  capture.control.cancel();
  t.mock.timers.tick(30000);
  assert.equal(capture.track.stopped, true);
  assert.equal(capture.completed.length, 0);
  assert.equal(capture.errors.length, 0);
});

test("manual stop finalizes once and clears the automatic-stop timer", (t) => {
  const capture = fakeCapture(t);
  t.mock.timers.tick(2000);
  capture.control.stop();
  capture.control.stop();
  t.mock.timers.tick(30000);
  assert.equal(capture.recorder.stopCalls, 1);
  assert.equal(capture.track.stopped, true);
  assert.equal(capture.completed.length, 1);
});

test("a disconnected microphone reports an error and releases recording resources", (t) => {
  const capture = fakeCapture(t);
  capture.track.dispatchEvent(new Event("ended"));
  t.mock.timers.tick(30000);
  assert.equal(capture.track.stopped, true);
  assert.equal(capture.completed.length, 0);
  assert.equal(capture.errors.length, 1);
  assert.match(capture.errors[0].message, /microphone disconnected/);
});
