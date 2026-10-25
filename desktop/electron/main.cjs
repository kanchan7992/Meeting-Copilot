// Electron main process: the overlay window, capture protection, hotkeys, audio permissions and self-test.
const path = require('node:path');
const {
  app, BrowserWindow, globalShortcut, ipcMain, session, desktopCapturer, screen, Tray, Menu, nativeImage, systemPreferences, shell,
} = require('electron');
const settings = require('./settings.cjs');
const { runCaptureSelfTest } = require('./selftest.cjs');

const isMac = process.platform === 'darwin';
let overlay = null;
let tray = null;
let clickThrough = false;

// Creates the transparent, always-on-top overlay that asks the OS to exclude it from screen capture. — pradeep.singh@rodicconsultants.com
function createOverlay() {
  const saved = settings.load().bounds;
  const area = screen.getPrimaryDisplay().workArea;
  const width = 460;
  const height = 560;
  overlay = new BrowserWindow({
    width: saved?.width ?? width,
    height: saved?.height ?? height,
    x: saved?.x ?? Math.round(area.x + (area.width - width) / 2),
    y: saved?.y ?? area.y + 8,
    minWidth: 320,
    minHeight: 220,
    transparent: true,
    backgroundColor: '#00000000',
    frame: false,
    hasShadow: false,
    resizable: true,
    fullscreenable: false,
    skipTaskbar: true,
    alwaysOnTop: true,
    show: false,
    ...(isMac ? { type: 'panel' } : {}),
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
      backgroundThrottling: false,
    },
  });

  // Capture protection: macOS sharingType=none, Windows WDA_EXCLUDEFROMCAPTURE.
  overlay.setContentProtection(true);
  overlay.setAlwaysOnTop(true, 'screen-saver');
  overlay.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true, skipTransformProcessType: true });
  if (isMac && typeof overlay.setHiddenInMissionControl === 'function') overlay.setHiddenInMissionControl(true);

  overlay.loadFile(path.join(__dirname, '..', 'dist', 'index.html'));
  if (process.env.COPILOT_SMOKE) runSmoke();
  // Shows the overlay without taking focus once it has rendered. — pradeep.singh@rodicconsultants.com
  overlay.once('ready-to-show', () => overlay.showInactive());
  // Remembers the overlay position and size. — pradeep.singh@rodicconsultants.com
  overlay.on('close', () => settings.save({ bounds: overlay.getBounds() }));
  // Re-applies protection after the OS re-creates the window surface (e.g. display change). — pradeep.singh@rodicconsultants.com
  const reprotect = () => overlay && overlay.setContentProtection(true);
  overlay.on('show', reprotect);
  screen.on('display-metrics-changed', reprotect);
}

// Smoke test (COPILOT_SMOKE=1): logs renderer errors and the rendered text, then quits. — pradeep.singh@rodicconsultants.com
function runSmoke() {
  // Prints renderer console messages of warning level or above. — pradeep.singh@rodicconsultants.com
  overlay.webContents.on('console-message', (e) => {
    if (e.level === 'warning' || e.level === 'error') console.log(`[renderer ${e.level}] ${e.message}`);
  });
  // Waits for render, prints state and quits. — pradeep.singh@rodicconsultants.com
  overlay.webContents.once('did-finish-load', async () => {
    await new Promise((r) => setTimeout(r, 1500));
    const text = await overlay.webContents.executeJavaScript('document.body.innerText');
    const protectedFlag = overlay.isContentProtected ? overlay.isContentProtected() : 'n/a';
    console.log(`[smoke] contentProtected=${protectedFlag} alwaysOnTop=${overlay.isAlwaysOnTop()}`);
    console.log(`[smoke] text: ${text.replace(/\s+/g, ' ').slice(0, 300)}`);
    app.quit();
  });
}

// Lets the renderer capture system audio (loopback) through getDisplayMedia, and the microphone. — pradeep.singh@rodicconsultants.com
function setupMediaPermissions() {
  const ses = session.defaultSession;
  // Grants the first screen plus loopback system audio to getDisplayMedia. — pradeep.singh@rodicconsultants.com
  ses.setDisplayMediaRequestHandler(async (_request, callback) => {
    try {
      const sources = await desktopCapturer.getSources({ types: ['screen'] });
      if (!sources.length) {
        console.error('no screen sources: Screen Recording permission missing');
        callback(null);
        return;
      }
      callback({ video: sources[0], audio: 'loopback' });
    } catch (err) {
      console.error('display media request failed', err);
      callback(null);
    }
  });
  // Allows only media and display-capture permission requests. — pradeep.singh@rodicconsultants.com
  ses.setPermissionRequestHandler((_wc, permission, callback) => callback(['media', 'display-capture'].includes(permission)));
  // Answers permission checks for media and display-capture only. — pradeep.singh@rodicconsultants.com
  ses.setPermissionCheckHandler((_wc, permission) => ['media', 'display-capture'].includes(permission));
}

// Checks (and on first use, requests) the macOS Screen & System Audio Recording permission. — pradeep.singh@rodicconsultants.com
async function ensureCapturePermission() {
  if (!isMac) return { ok: true };
  let status = systemPreferences.getMediaAccessStatus('screen');
  if (status !== 'granted') {
    // Asking for sources makes macOS show its permission prompt the first time.
    await desktopCapturer.getSources({ types: ['screen'] }).catch(() => []);
    status = systemPreferences.getMediaAccessStatus('screen');
  }
  if (status === 'granted') return { ok: true };
  await shell.openExternal('x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture');
  return {
    ok: false,
    message: 'Allow "Meeting Copilot" under Screen & System Audio Recording (System Settings just opened), then quit and reopen the app.',
  };
}

// Sends a named hotkey to the overlay. — pradeep.singh@rodicconsultants.com
function sendHotkey(name) {
  if (overlay) overlay.webContents.send('hotkey', name);
}

// Shows or hides the overlay without stealing focus. — pradeep.singh@rodicconsultants.com
function toggleOverlay() {
  if (!overlay) return;
  if (overlay.isVisible()) overlay.hide();
  else overlay.showInactive();
}

// Turns mouse click-through on or off. — pradeep.singh@rodicconsultants.com
function setClickThrough(on) {
  clickThrough = on;
  overlay.setIgnoreMouseEvents(on, { forward: true });
  overlay.webContents.send('click-through', on);
}

// Registers the global hotkeys (Ctrl+Alt+key on macOS and Windows). — pradeep.singh@rodicconsultants.com
function registerHotkeys() {
  // Each entry maps a global accelerator to the action it runs. — pradeep.singh@rodicconsultants.com
  const keys = {
    'Control+Alt+A': () => sendHotkey('answer'),
    'Control+Alt+W': () => sendHotkey('answer-web'),
    'Control+Alt+P': () => sendHotkey('pause'),
    'Control+Alt+H': toggleOverlay,
    'Control+Alt+T': () => setClickThrough(!clickThrough),
    'Control+Alt+S': () => {
      if (clickThrough) setClickThrough(false);
      overlay.show();
      overlay.focus();
      sendHotkey('search');
    },
  };
  for (const [accelerator, handler] of Object.entries(keys)) {
    if (!globalShortcut.register(accelerator, handler)) console.warn(`hotkey ${accelerator} is taken by another app`);
  }
}

// Creates the menu-bar / tray icon with show, self-test and quit. — pradeep.singh@rodicconsultants.com
function createTray() {
  const icon = nativeImage.createFromPath(path.join(__dirname, '..', 'assets', isMac ? 'trayTemplate.png' : 'tray.png'));
  if (isMac) icon.setTemplateImage(true);
  tray = new Tray(icon);
  tray.setToolTip('Meeting Copilot');
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: 'Show / hide overlay   Ctrl+Alt+H', click: toggleOverlay },
    { label: 'Click-through   Ctrl+Alt+T', click: () => setClickThrough(!clickThrough) },
    { label: 'Run screen-share self-test', click: () => overlay.webContents.send('hotkey', 'selftest') },
    { type: 'separator' },
    { label: 'Quit Meeting Copilot', role: 'quit' },
  ]));
}

// Registers the IPC handlers the renderer may call. — pradeep.singh@rodicconsultants.com
function registerIpc() {
  // Each handler below serves one renderer request through the preload bridge. — pradeep.singh@rodicconsultants.com
  ipcMain.handle('settings:get', () => settings.publicSettings());
  ipcMain.handle('settings:save', (_e, patch) => settings.savePublic(patch));
  ipcMain.handle('token:get', () => settings.getToken());
  ipcMain.handle('token:set', (_e, token) => settings.setToken(token));
  ipcMain.handle('overlay:opacity', (_e, value) => overlay.setOpacity(Math.min(1, Math.max(0.3, Number(value) || 1))));
  ipcMain.handle('overlay:hide', () => overlay.hide());
  ipcMain.handle('selftest:run', async () => {
    const perm = await ensureCapturePermission();
    return perm.ok ? runCaptureSelfTest(overlay) : { hidden: null, share: 0, detail: perm.message };
  });
  ipcMain.handle('capture:ensure', () => ensureCapturePermission());
  ipcMain.handle('permissions:status', () => ({
    platform: process.platform,
    screen: isMac ? systemPreferences.getMediaAccessStatus('screen') : 'granted',
    microphone: isMac ? systemPreferences.getMediaAccessStatus('microphone') : 'granted',
  }));
}

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  // Sets up permissions, IPC, window, hotkeys and tray once Electron is ready. — pradeep.singh@rodicconsultants.com
  app.whenReady().then(() => {
    if (isMac && app.dock) app.dock.hide();
    setupMediaPermissions();
    registerIpc();
    createOverlay();
    registerHotkeys();
    createTray();
  });
  // Brings back the overlay when the app is launched again. — pradeep.singh@rodicconsultants.com
  app.on('second-instance', () => overlay && overlay.showInactive());
  // Releases global hotkeys on quit. — pradeep.singh@rodicconsultants.com
  app.on('will-quit', () => globalShortcut.unregisterAll());
  // Quits when the overlay is closed. — pradeep.singh@rodicconsultants.com
  app.on('window-all-closed', () => app.quit());
}
