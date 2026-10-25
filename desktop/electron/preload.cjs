// Preload: exposes a small, typed bridge to the renderer (no Node access in the page).
const { contextBridge, ipcRenderer } = require('electron');

// Subscribes to a main-process channel and returns an unsubscribe function. — pradeep.singh@rodicconsultants.com
function on(channel, handler) {
  // Forwards only the payload, not the IPC event object. — pradeep.singh@rodicconsultants.com
  const listener = (_event, value) => handler(value);
  ipcRenderer.on(channel, listener);
  return () => ipcRenderer.removeListener(channel, listener);
}

contextBridge.exposeInMainWorld('copilot', {
  // Returns renderer-safe settings. — pradeep.singh@rodicconsultants.com
  getSettings: () => ipcRenderer.invoke('settings:get'),
  // Saves backend URL, profile, mic and opacity settings. — pradeep.singh@rodicconsultants.com
  saveSettings: (patch) => ipcRenderer.invoke('settings:save', patch),
  // Returns the decrypted access token. — pradeep.singh@rodicconsultants.com
  getToken: () => ipcRenderer.invoke('token:get'),
  // Encrypts and stores the access token. — pradeep.singh@rodicconsultants.com
  setToken: (token) => ipcRenderer.invoke('token:set', token),
  // Sets the overlay window opacity. — pradeep.singh@rodicconsultants.com
  setOpacity: (value) => ipcRenderer.invoke('overlay:opacity', value),
  // Hides the overlay window. — pradeep.singh@rodicconsultants.com
  hideOverlay: () => ipcRenderer.invoke('overlay:hide'),
  // Runs the screen-share self-test. — pradeep.singh@rodicconsultants.com
  runSelfTest: () => ipcRenderer.invoke('selftest:run'),
  // Checks and requests the screen/system-audio permission before capture. — pradeep.singh@rodicconsultants.com
  ensureCapture: () => ipcRenderer.invoke('capture:ensure'),
  // Returns macOS privacy permission status. — pradeep.singh@rodicconsultants.com
  permissions: () => ipcRenderer.invoke('permissions:status'),
  // Subscribes to global hotkeys. — pradeep.singh@rodicconsultants.com
  onHotkey: (handler) => on('hotkey', handler),
  // Subscribes to click-through changes. — pradeep.singh@rodicconsultants.com
  onClickThrough: (handler) => on('click-through', handler),
  // Subscribes to the self-test marker switch. — pradeep.singh@rodicconsultants.com
  onSelfTestMarker: (handler) => on('selftest-marker', handler),
});
