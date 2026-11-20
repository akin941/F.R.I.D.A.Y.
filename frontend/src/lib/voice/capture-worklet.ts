/**
 * The capture `AudioWorklet` (Phase 6 branch 3, D3/D6): runs on the audio
 * thread, downsamples the microphone's native rate (44.1/48 kHz) to 16 kHz
 * mono and posts `{ samples: Float32Array, rms: number }` frames to the main
 * thread. The RMS drives the orb's `listening` level (D10).
 *
 * An AudioWorklet module cannot import, so the processor is a
 * self-contained source string loaded through a Blob URL. Tests evaluate
 * this exact string against a fake `AudioWorkletProcessor` (no copy of the
 * algorithm to drift from).
 */
export const CAPTURE_PROCESSOR_NAME = "friday-capture";

export const CAPTURE_WORKLET_SOURCE = `
class FridayCaptureProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const opts = (options && options.processorOptions) || {};
    this.targetRate = opts.targetRate || 16000;
    // \`sampleRate\` is the AudioWorkletGlobalScope's context rate.
    this.ratio = sampleRate / this.targetRate;
    this.position = 0;
    this.acc = 0;
    this.accCount = 0;
    this.out = [];
    this.frameSize = opts.frameSize || 1600;
    this.stopped = false;
    this.port.onmessage = (event) => {
      if (event.data === "stop") {
        // Flush the partial frame so the utterance's tail is not lost.
        this.flush(true);
        this.stopped = true;
      }
    };
  }

  flush(final) {
    if (this.out.length === 0) {
      if (final) this.port.postMessage({ samples: new Float32Array(0), rms: 0, final: true });
      return;
    }
    const samples = Float32Array.from(this.out);
    this.out = [];
    let sum = 0;
    for (let i = 0; i < samples.length; i++) sum += samples[i] * samples[i];
    const rms = Math.sqrt(sum / samples.length);
    this.port.postMessage({ samples, rms, final: !!final }, [samples.buffer]);
  }

  process(inputs) {
    if (this.stopped) return false;
    const input = inputs[0];
    if (!input || input.length === 0 || !input[0]) return true;
    const channels = input.length;
    const length = input[0].length;
    for (let i = 0; i < length; i++) {
      let mixed = 0;
      for (let c = 0; c < channels; c++) mixed += input[c][i];
      this.acc += mixed / channels;
      this.accCount += 1;
      this.position += 1;
      if (this.position >= this.ratio) {
        this.position -= this.ratio;
        this.out.push(this.acc / this.accCount);
        this.acc = 0;
        this.accCount = 0;
      }
    }
    if (this.out.length >= this.frameSize) this.flush(false);
    return true;
  }
}
registerProcessor("${CAPTURE_PROCESSOR_NAME}", FridayCaptureProcessor);
`;

/** RMS of a frame (also used by playback's analyser level). */
export function rms(samples: Float32Array): number {
  if (samples.length === 0) return 0;
  let sum = 0;
  for (const value of samples) sum += value * value;
  return Math.sqrt(sum / samples.length);
}
