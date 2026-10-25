// Captures system audio (meeting, channel 0) and the microphone (channel 1) as 20 ms PCM16 16 kHz frames.
import workletSource from './pcm-worklet.js?raw';

export const CHANNEL_CLIENT = 0;
export const CHANNEL_ME = 1;

type FrameHandler = (channel: number, pcm: ArrayBuffer, peak: number) => void;

export class AudioCapture {
  private ctx: AudioContext | null = null;
  private streams: MediaStream[] = [];

  // Stores the callback that receives each audio frame. — pradeep.singh@rodicconsultants.com
  constructor(private readonly onFrame: FrameHandler) {}

  // Starts system-audio loopback capture, and the microphone when enabled. — pradeep.singh@rodicconsultants.com
  async start(micEnabled: boolean): Promise<void> {
    this.ctx = new AudioContext({ sampleRate: 16000 });
    const url = URL.createObjectURL(new Blob([workletSource], { type: 'text/javascript' }));
    await this.ctx.audioWorklet.addModule(url);
    URL.revokeObjectURL(url);

    const display = await navigator.mediaDevices.getDisplayMedia({ audio: true, video: true });
    this.streams.push(display);
    const systemTrack = display.getAudioTracks()[0];
    if (!systemTrack || systemTrack.readyState === 'ended') {
      throw new Error('No system audio. On macOS allow "System Audio Recording" for Meeting Copilot in Privacy & Security, then restart it.');
    }
    // Video is only needed to obtain the loopback audio; stop it to save CPU.
    display.getVideoTracks().forEach((t) => t.stop());
    this.attach(new MediaStream([systemTrack]), CHANNEL_CLIENT);

    if (micEnabled) {
      const mic = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      this.streams.push(mic);
      this.attach(mic, CHANNEL_ME);
    }
  }

  // Routes one stream through the PCM worklet and forwards its frames with a channel tag. — pradeep.singh@rodicconsultants.com
  private attach(stream: MediaStream, channel: number): void {
    const ctx = this.ctx!;
    const source = ctx.createMediaStreamSource(stream);
    const node = new AudioWorkletNode(ctx, 'pcm-capture');
    // Forwards each 20 ms frame and its peak level. — pradeep.singh@rodicconsultants.com
    node.port.onmessage = (e: MessageEvent<ArrayBuffer>) => this.onFrame(channel, e.data, peakOf(e.data));
    const mute = ctx.createGain();
    mute.gain.value = 0; // keeps the graph running without playing anything
    source.connect(node).connect(mute).connect(ctx.destination);
  }

  // Stops every track and closes the audio graph. — pradeep.singh@rodicconsultants.com
  async stop(): Promise<void> {
    this.streams.forEach((s) => s.getTracks().forEach((t) => t.stop()));
    this.streams = [];
    if (this.ctx) await this.ctx.close();
    this.ctx = null;
  }
}

// Returns the peak absolute sample of a PCM16 frame, from 0 to 1. — pradeep.singh@rodicconsultants.com
function peakOf(buf: ArrayBuffer): number {
  const samples = new Int16Array(buf);
  let peak = 0;
  for (let i = 0; i < samples.length; i += 4) peak = Math.max(peak, Math.abs(samples[i]));
  return peak / 32768;
}
