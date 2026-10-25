// AudioWorklet: converts the input (already resampled to 16 kHz by the AudioContext) to 20 ms PCM16 mono frames.
class PcmCapture extends AudioWorkletProcessor {
  // Sets up a 320-sample (20 ms) frame buffer. — pradeep.singh@rodicconsultants.com
  constructor() {
    super();
    this.frame = new Int16Array(320);
    this.filled = 0;
  }

  // Downmixes each render quantum to mono, converts to Int16 and posts full frames to the page. — pradeep.singh@rodicconsultants.com
  process(inputs) {
    const input = inputs[0];
    if (!input || input.length === 0) return true;
    const channels = input.length;
    const length = input[0].length;
    for (let i = 0; i < length; i++) {
      let sum = 0;
      for (let c = 0; c < channels; c++) sum += input[c][i];
      const s = Math.max(-1, Math.min(1, sum / channels));
      this.frame[this.filled++] = s < 0 ? s * 0x8000 : s * 0x7fff;
      if (this.filled === this.frame.length) {
        this.port.postMessage(this.frame.buffer.slice(0));
        this.filled = 0;
      }
    }
    return true;
  }
}

registerProcessor('pcm-capture', PcmCapture);
