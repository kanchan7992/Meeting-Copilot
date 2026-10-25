export type AnswerMode = 'direct' | 'web' | 'kb';

export interface PublicSettings {
  backendUrl: string;
  profile: 'default' | 'confidential';
  micEnabled: boolean;
  opacity: number;
  hasToken: boolean;
}

export interface SelfTestResult {
  hidden: boolean | null;
  share: number;
  detail: string;
}

export interface CopilotBridge {
  getSettings(): Promise<PublicSettings>;
  saveSettings(patch: Partial<PublicSettings>): Promise<PublicSettings>;
  getToken(): Promise<string | null>;
  setToken(token: string): Promise<boolean>;
  setOpacity(value: number): Promise<void>;
  hideOverlay(): Promise<void>;
  runSelfTest(): Promise<SelfTestResult>;
  ensureCapture(): Promise<{ ok: boolean; message?: string }>;
  permissions(): Promise<{ platform: string; screen: string; microphone: string }>;
  onHotkey(handler: (name: string) => void): () => void;
  onClickThrough(handler: (on: boolean) => void): () => void;
  onSelfTestMarker(handler: (on: boolean) => void): () => void;
}

export type ServerEvent =
  | { type: 'status'; code: string; message?: string; profile?: string; answer_mode?: AnswerMode; model?: string }
  | { type: 'error'; code: string; message: string; retry: boolean }
  | { type: 'transcript'; seg_id: string; channel: 'client' | 'me'; text: string; final: boolean; t0: number; t1: number }
  | { type: 'answer.start'; q_id: string; trigger: string; mode: AnswerMode; typed: string | null }
  | { type: 'answer.delta'; q_id: string; token: string }
  | { type: 'answer.sources'; q_id: string; sources: { kind: string; title: string; ref: string }[] }
  | { type: 'answer.done'; q_id: string; mode?: AnswerMode; model?: string; first_token_ms?: number; total_ms?: number; cancelled?: boolean };

declare global {
  interface Window {
    copilot: CopilotBridge;
  }
}
