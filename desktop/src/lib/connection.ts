// Talks to the backend: creates a meeting session, streams audio over WebSocket, receives events.
import type { AnswerMode, ServerEvent } from './types';

const MAX_BUFFERED = 1_000_000; // drop audio rather than build up delay on a slow link

export interface ConnectionHandlers {
  onEvent(event: ServerEvent): void;
  onState(state: 'connecting' | 'open' | 'closed' | 'reconnecting'): void;
}

export class CopilotConnection {
  private ws: WebSocket | null = null;
  private sessionId: string | null = null;
  private closedByUser = false;
  private retries = 0;

  // Stores the backend address, token and event handlers. — pradeep.singh@rodicconsultants.com
  constructor(private readonly backendUrl: string, private readonly token: string, private readonly handlers: ConnectionHandlers) {}

  // Creates a meeting session on the backend and opens its stream. — pradeep.singh@rodicconsultants.com
  async start(title: string, profile: string): Promise<void> {
    const res = await fetch(`${this.base()}/v1/sessions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...(this.token ? { Authorization: `Bearer ${this.token}` } : {}) },
      body: JSON.stringify({ title, profile }),
    });
    if (!res.ok) throw new Error(`Backend refused the session (${res.status}). Check URL and token.`);
    this.sessionId = (await res.json()).id;
    this.closedByUser = false;
    this.open();
  }

  // Opens (or re-opens) the WebSocket and authenticates. — pradeep.singh@rodicconsultants.com
  private open(): void {
    this.handlers.onState(this.retries ? 'reconnecting' : 'connecting');
    const ws = new WebSocket(`${this.base().replace(/^http/, 'ws')}/v1/sessions/${this.sessionId}/stream`);
    ws.binaryType = 'arraybuffer';
    // Sends the auth message first. — pradeep.singh@rodicconsultants.com
    ws.onopen = () => {
      ws.send(JSON.stringify({ type: 'auth', token: this.token }));
      this.retries = 0;
      this.handlers.onState('open');
    };
    // Parses and forwards server events. — pradeep.singh@rodicconsultants.com
    ws.onmessage = (e) => {
      if (typeof e.data === 'string') this.handlers.onEvent(JSON.parse(e.data) as ServerEvent);
    };
    // Reconnects with back-off unless the user stopped the meeting or the server refused it. — pradeep.singh@rodicconsultants.com
    ws.onclose = (e) => {
      this.ws = null;
      if (this.closedByUser || e.code === 4401 || e.code === 4404) {
        this.handlers.onState('closed');
        return;
      }
      this.retries += 1;
      this.handlers.onState('reconnecting');
      setTimeout(() => this.open(), Math.min(10_000, 500 * 2 ** this.retries));
    };
    this.ws = ws;
  }

  // Sends one audio frame tagged with its channel. — pradeep.singh@rodicconsultants.com
  sendAudio(channel: number, pcm: ArrayBuffer): void {
    const ws = this.ws;
    if (!ws || ws.readyState !== WebSocket.OPEN || ws.bufferedAmount > MAX_BUFFERED) return;
    const frame = new Uint8Array(pcm.byteLength + 1);
    frame[0] = channel;
    frame.set(new Uint8Array(pcm), 1);
    ws.send(frame);
  }

  // Asks for an answer now: typed text, or the latest question when text is empty. — pradeep.singh@rodicconsultants.com
  ask(text: string | null, mode: AnswerMode): void {
    this.sendJson({ type: 'ask', text, mode });
  }

  // Sends pause, resume or cancel. — pradeep.singh@rodicconsultants.com
  control(action: 'pause' | 'resume' | 'cancel_answer'): void {
    this.sendJson({ type: 'control', action });
  }

  // Closes the stream and ends the session on the backend. — pradeep.singh@rodicconsultants.com
  async stop(): Promise<void> {
    this.closedByUser = true;
    this.ws?.close();
    if (this.sessionId) {
      await fetch(`${this.base()}/v1/sessions/${this.sessionId}/end`, {
        method: 'POST',
        headers: this.token ? { Authorization: `Bearer ${this.token}` } : {},
      }).catch(() => undefined);
    }
  }

  // Sends a JSON message if the socket is open. — pradeep.singh@rodicconsultants.com
  private sendJson(msg: object): void {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(msg));
  }

  // Returns the backend URL without a trailing slash. — pradeep.singh@rodicconsultants.com
  private base(): string {
    return this.backendUrl.replace(/\/+$/, '');
  }
}
