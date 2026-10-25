// Overlay UI: live transcript, streaming answer cards, search box, controls and settings.
import { useCallback, useEffect, useRef, useState } from 'react';
import { AudioCapture } from './lib/audio';
import { CopilotConnection } from './lib/connection';
import { askChat } from './lib/chat';
import type { AnswerMode, PublicSettings, SelfTestResult, ServerEvent } from './lib/types';

interface AnswerCard {
  id: string;
  text: string;
  mode: AnswerMode;
  trigger: string;
  typed: string | null;
  sources: { title: string; ref: string }[];
  done: boolean;
  firstTokenMs?: number;
}

interface Line {
  id: string;
  channel: 'client' | 'me';
  text: string;
}

type Phase = 'setup' | 'idle' | 'live';

// Root component of the overlay. — pradeep.singh@rodicconsultants.com
export default function App() {
  const bridge = window.copilot;
  const [settings, setSettings] = useState<PublicSettings | null>(null);
  const [phase, setPhase] = useState<Phase>('idle');
  const [status, setStatus] = useState('Ready');
  const [model, setModel] = useState('');
  const [lines, setLines] = useState<Line[]>([]);
  const [answers, setAnswers] = useState<AnswerCard[]>([]);
  const [query, setQuery] = useState('');
  const [web, setWeb] = useState(false);
  const [paused, setPaused] = useState(false);
  const [clickThrough, setClickThrough] = useState(false);
  const [marker, setMarker] = useState(false);
  const [selfTest, setSelfTest] = useState<SelfTestResult | null>(null);
  const [title, setTitle] = useState('');
  const [levels, setLevels] = useState({ client: 0, me: 0 });
  const conn = useRef<CopilotConnection | null>(null);
  const capture = useRef<AudioCapture | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const peaks = useRef({ client: 0, me: 0 });

  // Loads settings once (the token is optional for a local backend). — pradeep.singh@rodicconsultants.com
  useEffect(() => {
    bridge.getSettings().then((s) => {
      setSettings(s);
      bridge.setOpacity(s.opacity);
    });
  }, [bridge]);

  // Applies one server event to the UI state. — pradeep.singh@rodicconsultants.com
  const onEvent = useCallback((ev: ServerEvent) => {
    switch (ev.type) {
      case 'status':
        if (ev.code === 'ready') {
          setStatus('Listening');
          setModel(ev.model ?? '');
        } else if (ev.code === 'paused') setStatus('Paused');
        else if (ev.code === 'listening') setStatus('Listening');
        else if (ev.message) setStatus(ev.message);
        break;
      case 'error':
        setStatus(`Error: ${ev.message}`);
        break;
      case 'transcript':
        setLines((prev) => [...prev, { id: ev.seg_id, channel: ev.channel, text: ev.text }].slice(-4));
        break;
      case 'answer.start':
        setAnswers((prev) => [{ id: ev.q_id, text: '', mode: ev.mode, trigger: ev.trigger, typed: ev.typed, sources: [], done: false }, ...prev].slice(0, 20));
        break;
      case 'answer.delta':
        setAnswers((prev) => prev.map((a) => (a.id === ev.q_id ? { ...a, text: a.text + ev.token } : a)));
        break;
      case 'answer.sources':
        setAnswers((prev) => prev.map((a) => (a.id === ev.q_id ? { ...a, sources: ev.sources } : a)));
        break;
      case 'answer.done':
        setAnswers((prev) => prev.map((a) => (a.id === ev.q_id ? { ...a, done: true, firstTokenMs: ev.first_token_ms } : a)));
        break;
    }
  }, []);

  // Starts a meeting: backend session, WebSocket and audio capture. — pradeep.singh@rodicconsultants.com
  const startMeeting = useCallback(async () => {
    if (!settings) return;
    setStatus('Checking permissions…');
    const perm = await bridge.ensureCapture();
    if (!perm.ok) {
      setStatus(perm.message ?? 'Screen & System Audio Recording permission is needed.');
      return;
    }
    const token = (await bridge.getToken()) ?? '';
    setStatus('Connecting…');
    const c = new CopilotConnection(settings.backendUrl, token, {
      onEvent,
      // Shows connection state changes in the status line. — pradeep.singh@rodicconsultants.com
      onState: (s) => {
        if (s === 'reconnecting') setStatus('Reconnecting…');
        if (s === 'closed') setStatus('Disconnected');
      },
    });
    try {
      await c.start(title || 'Meeting', settings.profile);
      const a = new AudioCapture((channel, pcm, peak) => {
        c.sendAudio(channel, pcm);
        const key = channel === 0 ? 'client' : 'me';
        peaks.current[key] = Math.max(peaks.current[key] * 0.9, peak);
      });
      await a.start(settings.micEnabled);
      conn.current = c;
      capture.current = a;
      setLines([]);
      setPhase('live');
    } catch (err) {
      await c.stop();
      setStatus(friendlyCaptureError(err));
    }
  }, [bridge, settings, title, onEvent]);

  // Stops capture and ends the session. — pradeep.singh@rodicconsultants.com
  const stopMeeting = useCallback(async () => {
    await capture.current?.stop();
    await conn.current?.stop();
    capture.current = null;
    conn.current = null;
    setPaused(false);
    setPhase('idle');
    setStatus('Stopped');
  }, []);

  // Answers a typed question like a chatbot when no meeting is running (with recent turns as context). — pradeep.singh@rodicconsultants.com
  const chat = useCallback(async (text: string, mode: AnswerMode) => {
    if (!settings) return;
    const id = `chat-${Date.now()}`;
    const history = answers.filter((a) => a.typed && a.done).slice(0, 6).reverse().map((a) => ({ q: a.typed!, a: a.text }));
    setAnswers((prev) => [{ id, text: '', mode, trigger: 'chat', typed: text, sources: [], done: false }, ...prev].slice(0, 30));
    // Applies a change to this chat card only. — pradeep.singh@rodicconsultants.com
    const patch = (fn: (a: AnswerCard) => AnswerCard) => setAnswers((prev) => prev.map((a) => (a.id === id ? fn(a) : a)));
    try {
      await askChat(settings.backendUrl, await bridge.getToken(), text, mode, history, {
        // Appends a streamed token. — pradeep.singh@rodicconsultants.com
        onToken: (t) => patch((a) => ({ ...a, text: a.text + t })),
        // Stores web sources. — pradeep.singh@rodicconsultants.com
        onSources: (s) => patch((a) => ({ ...a, sources: s })),
        // Marks the answer finished. — pradeep.singh@rodicconsultants.com
        onDone: (info) => patch((a) => ({ ...a, done: true, firstTokenMs: info.firstTokenMs })),
      });
    } catch (err) {
      const msg = err instanceof Error && err.message.includes('fetch') ? 'Backend is not running (start backend/run-local.sh).' : String(err instanceof Error ? err.message : err);
      patch((a) => ({ ...a, text: `⚠ ${msg}`, done: true }));
    }
  }, [settings, answers, bridge]);

  // Asks for an answer now: in a meeting via the live stream, otherwise as a chat question. — pradeep.singh@rodicconsultants.com
  const askNow = useCallback((forceWeb = false) => {
    const text = query.trim() || null;
    const mode: AnswerMode = forceWeb || web ? 'web' : 'direct';
    if (conn.current) conn.current.ask(text, mode);
    else if (text) chat(text, mode);
    setQuery('');
  }, [query, web, chat]);

  // Toggles listening on and off. — pradeep.singh@rodicconsultants.com
  const togglePause = useCallback(() => {
    setPaused((p) => {
      conn.current?.control(p ? 'resume' : 'pause');
      return !p;
    });
  }, []);

  // Runs the screen-share self-test and shows the result. — pradeep.singh@rodicconsultants.com
  const runSelfTest = useCallback(async () => {
    setSelfTest({ hidden: null, share: 0, detail: 'Testing…' });
    setSelfTest(await bridge.runSelfTest());
  }, [bridge]);

  // Subscribes to hotkeys, click-through and self-test marker events. — pradeep.singh@rodicconsultants.com
  useEffect(() => {
    const offs = [
      bridge.onHotkey((name) => {
        if (name === 'answer') askNow(false);
        else if (name === 'answer-web') askNow(true);
        else if (name === 'pause') togglePause();
        else if (name === 'search') searchRef.current?.focus();
        else if (name === 'selftest') runSelfTest();
      }),
      bridge.onClickThrough(setClickThrough),
      bridge.onSelfTestMarker(setMarker),
    ];
    return () => offs.forEach((off) => off());
  }, [bridge, askNow, togglePause, runSelfTest]);

  // Refreshes the audio level meters five times a second. — pradeep.singh@rodicconsultants.com
  useEffect(() => {
    const id = setInterval(() => setLevels({ ...peaks.current }), 200);
    return () => clearInterval(id);
  }, []);

  if (marker) return <div className="marker" />;

  return (
    <div className={`card ${clickThrough ? 'passive' : ''}`}>
      <header className="bar">
        <span className={`dot ${phase === 'live' && !paused ? 'on' : ''}`} />
        <span className="status" title={status}>{status}</span>
        {model && <span className="badge">{model}</span>}
        {settings?.profile === 'confidential' && <span className="badge warn">confidential</span>}
        <button className="icon" title="Settings" onClick={() => setPhase(phase === 'setup' ? (conn.current ? 'live' : 'idle') : 'setup')}>⚙</button>
        <button className="icon" title="Hide (Ctrl+Alt+H)" onClick={() => bridge.hideOverlay()}>—</button>
      </header>

      {phase === 'setup' && settings && (
        <Settings settings={settings} onSaved={(s) => { setSettings(s); setPhase(conn.current ? 'live' : 'idle'); }} onSelfTest={runSelfTest} selfTest={selfTest} />
      )}

      {phase === 'idle' && (
        <section className="start">
          <div className="row">
            <input placeholder="Meeting title (optional)" value={title} onChange={(e) => setTitle(e.target.value)} />
            <button className="primary" onClick={startMeeting}>Start listening</button>
          </div>
          <div className="row">
            <button onClick={runSelfTest}>Screen-share self-test</button>
            <span className="hint">Ctrl+Alt+A answer · Ctrl+Alt+H hide</span>
          </div>
          {selfTest && <SelfTestView result={selfTest} />}
        </section>
      )}

      {phase === 'live' && (
        <section className="transcript">
          <div className="meters">
            <Meter label="Meeting" value={levels.client} />
            <Meter label="Mic" value={levels.me} />
          </div>
          {lines.map((l) => (
            <div key={l.id} className={`line ${l.channel}`}>{l.channel === 'client' ? 'Client' : 'Me'}: {l.text}</div>
          ))}
        </section>
      )}

      {phase !== 'setup' && (
        <>
          <section className="answers">
            {answers.length === 0 && (
              <p className="hint">
                {phase === 'live'
                  ? 'Answers appear here when a client asks a question. Ctrl+Alt+A answers now.'
                  : 'Ask anything below, like a chatbot. Click Start listening to get automatic answers during a meeting.'}
              </p>
            )}
            {answers.map((a, i) => (
              <article key={a.id} className={`answer ${i === 0 ? 'latest' : ''}`}>
                <div className="meta">
                  {a.mode === 'web' ? 'web' : 'direct'} · {a.trigger}
                  {a.firstTokenMs !== undefined && ` · ${(a.firstTokenMs / 1000).toFixed(1)}s`}
                  {!a.done && ' · …'}
                </div>
                {a.typed && <div className="typed">“{a.typed}”</div>}
                <pre>{a.text}</pre>
                {a.sources.length > 0 && (
                  <ol className="sources">{a.sources.map((s) => <li key={s.ref} title={s.ref}>{s.title || s.ref}</li>)}</ol>
                )}
              </article>
            ))}
          </section>

          <footer className="controls">
            <input
              ref={searchRef}
              placeholder={phase === 'live' ? 'Type a question, or empty = answer last (Enter)' : 'Ask anything… (Enter)'}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') askNow(); }}
            />
            <button className="primary" title="Ask (Ctrl+Alt+A)" onClick={() => askNow()}>Ask</button>
            <button className={web ? 'toggle on' : 'toggle'} title="Use web search (slower)" onClick={() => setWeb(!web)}>Web</button>
            {phase === 'live' && <button title="Pause (Ctrl+Alt+P)" onClick={togglePause}>{paused ? 'Resume' : 'Pause'}</button>}
            {phase === 'live' && <button title="Stop meeting" onClick={stopMeeting}>Stop</button>}
            {answers.length > 0 && phase !== 'live' && <button title="Clear answers" onClick={() => setAnswers([])}>Clear</button>}
          </footer>
        </>
      )}
    </div>
  );
}

// Small horizontal audio level meter. — pradeep.singh@rodicconsultants.com
function Meter({ label, value }: { label: string; value: number }) {
  return (
    <div className="meter">
      <span>{label}</span>
      <div className="track"><div className="fill" style={{ width: `${Math.min(100, value * 160)}%` }} /></div>
    </div>
  );
}

// Shows the result of the screen-share self-test. — pradeep.singh@rodicconsultants.com
function SelfTestView({ result }: { result: SelfTestResult }) {
  const cls = result.hidden === null ? '' : result.hidden ? 'ok' : 'bad';
  return <p className={`selftest ${cls}`}>{result.hidden === null ? '' : result.hidden ? 'Hidden ✓ ' : 'VISIBLE ✗ '}{result.detail}</p>;
}

// Settings panel: backend URL, token, profile, microphone, opacity, self-test. — pradeep.singh@rodicconsultants.com
function Settings({ settings, onSaved, onSelfTest, selfTest }: {
  settings: PublicSettings;
  onSaved: (s: PublicSettings) => void;
  onSelfTest: () => void;
  selfTest: SelfTestResult | null;
}) {
  const bridge = window.copilot;
  const [backendUrl, setBackendUrl] = useState(settings.backendUrl);
  const [token, setToken] = useState('');
  const [profile, setProfile] = useState(settings.profile);
  const [mic, setMic] = useState(settings.micEnabled);
  const [opacity, setOpacity] = useState(settings.opacity);
  const [perm, setPerm] = useState('');
  const [error, setError] = useState('');

  // Shows macOS permission status for screen and microphone. — pradeep.singh@rodicconsultants.com
  useEffect(() => {
    bridge.permissions().then((p) => setPerm(p.platform === 'darwin' ? `Screen: ${p.screen} · Mic: ${p.microphone}` : ''));
  }, [bridge]);

  // Saves settings and, if typed, the token. — pradeep.singh@rodicconsultants.com
  const save = async () => {
    try {
      if (token.trim()) await bridge.setToken(token.trim());
      const s = await bridge.saveSettings({ backendUrl: backendUrl.trim(), profile, micEnabled: mic, opacity });
      await bridge.setOpacity(opacity);
      onSaved(s);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <section className="settings">
      <label>Backend URL<input value={backendUrl} onChange={(e) => setBackendUrl(e.target.value)} /></label>
      <label>Access token {settings.hasToken && <em>(saved; leave empty to keep)</em>}
        <input type="password" value={token} onChange={(e) => setToken(e.target.value)} placeholder="from python -m app.tools.make_token" />
      </label>
      <label>Profile
        <select value={profile} onChange={(e) => setProfile(e.target.value as PublicSettings['profile'])}>
          <option value="default">Default (fast, cloud models)</option>
          <option value="confidential">Confidential (self-hosted only)</option>
        </select>
      </label>
      <label className="row"><input type="checkbox" checked={mic} onChange={(e) => setMic(e.target.checked)} /> Capture my microphone too (use headphones)</label>
      <label>Opacity {Math.round(opacity * 100)}%
        <input type="range" min={0.3} max={1} step={0.02} value={opacity} onChange={(e) => { setOpacity(Number(e.target.value)); bridge.setOpacity(Number(e.target.value)); }} />
      </label>
      {perm && <p className="hint">{perm}</p>}
      <div className="row">
        <button className="primary" onClick={save}>Save</button>
        <button onClick={onSelfTest}>Screen-share self-test</button>
      </div>
      {selfTest && <SelfTestView result={selfTest} />}
      {error && <p className="selftest bad">{error}</p>}
    </section>
  );
}

// Turns browser capture errors into an instruction the user can act on. — pradeep.singh@rodicconsultants.com
function friendlyCaptureError(err: unknown): string {
  const msg = err instanceof Error ? `${err.name}: ${err.message}` : String(err);
  if (/constraint|NotAllowed|Permission|NotReadable|AbortError/i.test(msg)) {
    return 'Screen & System Audio Recording is not allowed yet: enable Meeting Copilot in System Settings › Privacy & Security, then quit and reopen the app.';
  }
  if (/Failed to fetch|NetworkError/i.test(msg)) return 'Backend is not running: start backend/run-local.sh.';
  return msg;
}
