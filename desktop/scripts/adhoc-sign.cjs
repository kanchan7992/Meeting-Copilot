// Ad-hoc signs the local macOS build so it launches on Apple silicon (use a Developer ID for distribution).
const { execFileSync } = require('node:child_process');
const path = require('node:path');
const fs = require('node:fs');

// Signs the built app bundle and verifies the signature. — pradeep.singh@rodicconsultants.com
function main() {
  const app = path.join(__dirname, '..', 'release', 'mac-arm64', 'Meeting Copilot.app');
  if (!fs.existsSync(app)) throw new Error(`not found: ${app}`);
  execFileSync('codesign', ['--force', '--deep', '--sign', '-', app], { stdio: 'inherit' });
  execFileSync('codesign', ['--verify', '--deep', '--strict', app], { stdio: 'inherit' });
  console.log(`signed (ad-hoc): ${app}`);
}

main();
