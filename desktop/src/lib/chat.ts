// Chat outside a meeting: streams an answer from POST /v1/ask (server-sent events).
import type { AnswerMode } from './types';

export interface ChatTurn {
  q: string;
  a: string;
}

export interface ChatCallbacks {
  onToken(token: string): void;
  onSources(sources: { title: string; ref: string }[]): void;
  onDone(info: { firstTokenMs?: number }): void;
}

// Sends one question (with recent turns for follow-ups) and streams the answer to the callbacks. — pradeep.singh@rodicconsultants.com
export async function askChat(
  backendUrl: string,
  token: string | null,
  text: string,
  mode: AnswerMode,
  history: ChatTurn[],
  cb: ChatCallbacks,
): Promise<void> {
  const res = await fetch(`${backendUrl.replace(/\/+$/, '')}/v1/ask`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: JSON.stringify({ text, mode, history: history.slice(-6) }),
  });
  if (res.status === 401) throw new Error('Backend needs an access token: open ⚙ Settings.');
  if (!res.ok || !res.body) throw new Error(`Backend error ${res.status}`);

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let cut: number;
    while ((cut = buffer.indexOf('\n\n')) >= 0) {
      const line = buffer.slice(0, cut).trim();
      buffer = buffer.slice(cut + 2);
      if (!line.startsWith('data: ')) continue;
      const ev = JSON.parse(line.slice(6));
      if (ev.kind === 'token') cb.onToken(ev.data);
      else if (ev.kind === 'sources') cb.onSources(ev.data.map((s: { title: string; url: string }) => ({ title: s.title, ref: s.url })));
      else if (ev.kind === 'done') cb.onDone({ firstTokenMs: ev.first_token_ms });
    }
  }
}
