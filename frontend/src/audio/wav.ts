export interface AudioLimits {
  max_seconds: number;
  max_bytes: number;
  sample_rate: number;
}

export function mixChannels(channels: readonly Float32Array[]): Float32Array {
  if (!channels.length || channels.some((channel) => channel.length !== channels[0].length)) {
    throw new Error("The recording has inconsistent audio channels. Please record again.");
  }
  const mono = new Float32Array(channels[0].length);
  for (const channel of channels) {
    for (let i = 0; i < channel.length; i++) mono[i] += channel[i] / channels.length;
  }
  return mono;
}

export function encodePcm16Wav(samples: Float32Array, limits: AudioLimits): ArrayBuffer {
  if (!samples.length) throw new Error("No audio was recorded. Please try again or type your reply.");
  const size = 44 + samples.length * 2;
  if (samples.length / limits.sample_rate > limits.max_seconds || size > limits.max_bytes) {
    throw new Error(`The recording is longer than ${limits.max_seconds} seconds. Please try a shorter recording; no audio was sent.`);
  }
  const output = new ArrayBuffer(size);
  const view = new DataView(output);
  const ascii = (offset: number, value: string) => {
    for (let i = 0; i < value.length; i++) view.setUint8(offset + i, value.charCodeAt(i));
  };
  ascii(0, "RIFF");
  view.setUint32(4, size - 8, true);
  ascii(8, "WAVE");
  ascii(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, limits.sample_rate, true);
  view.setUint32(28, limits.sample_rate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  ascii(36, "data");
  view.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i++) {
    if (!Number.isFinite(samples[i])) throw new Error("The recording contains invalid audio. Please record again.");
    const sample = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(44 + i * 2, Math.round(sample * (sample < 0 ? 32768 : 32767)), true);
  }
  return output;
}
