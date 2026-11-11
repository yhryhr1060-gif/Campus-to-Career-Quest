import test from "node:test";
import assert from "node:assert/strict";
import { encodePcm16Wav, mixChannels } from "../src/audio/wav.ts";
import { inputMetadata, readInputMetadata, sameInputMetadata } from "../src/audio/voiceDraft.ts";

const limits = { sample_rate: 16000, max_seconds: 20, max_bytes: 640044 };
const ascii = (buffer, offset, length) => new TextDecoder().decode(new Uint8Array(buffer, offset, length));

test("encodes a complete 20-second mono 16k PCM16 WAV within the upload limit", () => {
  const buffer = encodePcm16Wav(new Float32Array(320000), limits);
  const view = new DataView(buffer);
  assert.equal(buffer.byteLength, 640044);
  assert.equal(ascii(buffer, 0, 4), "RIFF");
  assert.equal(ascii(buffer, 8, 4), "WAVE");
  assert.equal(ascii(buffer, 12, 4), "fmt ");
  assert.equal(ascii(buffer, 36, 4), "data");
  assert.equal(view.getUint32(4, true), buffer.byteLength - 8);
  assert.equal(view.getUint32(16, true), 16);
  assert.equal(view.getUint16(20, true), 1);
  assert.equal(view.getUint16(22, true), 1);
  assert.equal(view.getUint32(24, true), 16000);
  assert.equal(view.getUint32(28, true), 32000);
  assert.equal(view.getUint16(32, true), 2);
  assert.equal(view.getUint16(34, true), 16);
  assert.equal(view.getUint32(40, true), 640000);
});

test("uses signed little-endian PCM with correct negative and positive full scale", () => {
  const buffer = encodePcm16Wav(Float32Array.from([-2, -1, -0.5, 0, 0.5, 1, 2]), limits);
  const view = new DataView(buffer);
  assert.deepEqual(Array.from({ length: 7 }, (_, i) => view.getInt16(44 + i * 2, true)),
    [-32768, -32768, -16384, 0, 16384, 32767, 32767]);
});

test("rejects recordings beyond the duration or byte limit without silently cutting samples", () => {
  const tooLong = new Float32Array(320001);
  tooLong[320000] = 0.75;
  assert.throws(() => encodePcm16Wav(tooLong, limits), /longer than 20 seconds/);
  assert.equal(tooLong.length, 320001);
  assert.equal(tooLong[320000], 0.75);
  assert.throws(() => encodePcm16Wav(new Float32Array(320000), { ...limits, max_bytes: 640043 }), /no audio was sent/);
});

test("rejects empty and non-finite audio instead of uploading a misleading recording", () => {
  assert.throws(() => encodePcm16Wav(new Float32Array(), limits), /No audio/);
  assert.throws(() => encodePcm16Wav(Float32Array.from([0, NaN]), limits), /invalid audio/);
  assert.throws(() => encodePcm16Wav(Float32Array.from([Infinity]), limits), /invalid audio/);
});

test("mixes stereo channels to mono without mutating the recorded samples", () => {
  const left = Float32Array.from([1, 0.5, -1]);
  const right = Float32Array.from([-1, 0.5, 0]);
  assert.deepEqual(Array.from(mixChannels([left, right])), [0, 0.5, -0.5]);
  assert.deepEqual(Array.from(left), [1, 0.5, -1]);
  assert.deepEqual(Array.from(right), [-1, 0.5, 0]);
  assert.throws(() => mixChannels([left, Float32Array.from([1])]), /inconsistent/);
});

test("marks an unchanged transcript as voice after trimming, without rewriting the ASR source", () => {
  const original = "  Could I submit it on Sunday?\n";
  assert.deepEqual(inputMetadata("Could I submit it on Sunday?", original), { input_mode: "voice", asr_text: original });
});

test("an edited transcript keeps its complete original source, even when the original exceeds 600", () => {
  const original = "word ".repeat(150);
  assert.deepEqual(inputMetadata("Could I submit the outline first?", original), { input_mode: "voice_edited", asr_text: original });
});

test("typed and cleared input never carries an ASR field", () => {
  assert.deepEqual(inputMetadata("A typed reply", null), { input_mode: "typed" });
  assert.deepEqual(inputMetadata("   ", "an earlier recording"), { input_mode: "typed" });
  assert.equal("asr_text" in inputMetadata("A typed reply", null), false);
});

test("cached request metadata survives refresh exactly and detects changed provenance", () => {
  const frozen = inputMetadata("Sunday evening would help.", "Sunday evening would help.");
  const restored = readInputMetadata(JSON.parse(JSON.stringify(frozen)));
  assert.deepEqual(restored, frozen);
  assert.equal(sameInputMetadata(restored, frozen), true);
  assert.equal(sameInputMetadata(restored, { input_mode: "typed" }), false);
  assert.equal(sameInputMetadata(restored, { input_mode: "voice_edited", asr_text: frozen.asr_text }), false);
  assert.equal(readInputMetadata({ input_mode: "voice" }), null);
  assert.equal(readInputMetadata({ input_mode: "unknown" }), null);
});
