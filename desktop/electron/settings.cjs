// Local settings. The access token is encrypted with the OS keychain (Electron safeStorage) before it is written.
const fs = require('node:fs');
const path = require('node:path');
const { app, safeStorage } = require('electron');

const DEFAULTS = { backendUrl: 'http://127.0.0.1:8765', profile: 'default', micEnabled: true, opacity: 0.92 };

// Returns the settings file path in the app's user-data folder. — pradeep.singh@rodicconsultants.com
function file() {
  return path.join(app.getPath('userData'), 'settings.json');
}

// Reads all stored settings (including the encrypted token blob). — pradeep.singh@rodicconsultants.com
function load() {
  try {
    return { ...DEFAULTS, ...JSON.parse(fs.readFileSync(file(), 'utf8')) };
  } catch {
    return { ...DEFAULTS };
  }
}

// Merges a patch into stored settings, written with owner-only permissions. — pradeep.singh@rodicconsultants.com
function save(patch) {
  const next = { ...load(), ...patch };
  fs.mkdirSync(path.dirname(file()), { recursive: true });
  fs.writeFileSync(file(), JSON.stringify(next, null, 2), { mode: 0o600 });
  return next;
}

// Returns settings that are safe to give the renderer (no token blob). — pradeep.singh@rodicconsultants.com
function publicSettings() {
  const { tokenEnc, bounds, ...rest } = load();
  return { ...rest, hasToken: Boolean(tokenEnc) };
}

// Saves only the fields the renderer may change. — pradeep.singh@rodicconsultants.com
function savePublic(patch) {
  const allowed = {};
  for (const key of ['backendUrl', 'profile', 'micEnabled', 'opacity']) if (key in patch) allowed[key] = patch[key];
  save(allowed);
  return publicSettings();
}

// Encrypts and stores the access token; refuses if OS encryption is unavailable. — pradeep.singh@rodicconsultants.com
function setToken(token) {
  if (!safeStorage.isEncryptionAvailable()) throw new Error('OS keychain encryption is not available');
  save({ tokenEnc: safeStorage.encryptString(String(token)).toString('base64') });
  return true;
}

// Decrypts and returns the stored access token, or null. — pradeep.singh@rodicconsultants.com
function getToken() {
  const { tokenEnc } = load();
  if (!tokenEnc) return null;
  try {
    return safeStorage.decryptString(Buffer.from(tokenEnc, 'base64'));
  } catch {
    return null;
  }
}

module.exports = { load, save, publicSettings, savePublic, setToken, getToken };
