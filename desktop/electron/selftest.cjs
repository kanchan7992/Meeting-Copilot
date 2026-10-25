// Screen-share self-test: paints a marker in the overlay, captures the screen the way meeting apps do
// (Chromium desktop capture: ScreenCaptureKit on macOS, Windows Graphics Capture on Windows) and looks for the marker.
const { desktopCapturer, screen } = require('electron');

const MARKER = { r: 255, g: 0, b: 255 }; // magenta

// Waits for the given number of milliseconds. — pradeep.singh@rodicconsultants.com
function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// Returns the share of sampled pixels in a BGRA bitmap region that match the marker colour. — pradeep.singh@rodicconsultants.com
function markerShare(bitmap, imgWidth, rect) {
  let hits = 0;
  let total = 0;
  for (let y = rect.y; y < rect.y + rect.height; y += 4) {
    for (let x = rect.x; x < rect.x + rect.width; x += 4) {
      const i = (y * imgWidth + x) * 4;
      const b = bitmap[i], g = bitmap[i + 1], r = bitmap[i + 2];
      total += 1;
      if (r > 200 && g < 80 && b > 200) hits += 1;
    }
  }
  return total ? hits / total : 0;
}

// Runs the test and returns { hidden, share, detail }. — pradeep.singh@rodicconsultants.com
async function runCaptureSelfTest(overlay) {
  if (!overlay || !overlay.isVisible()) overlay.showInactive();
  const bounds = overlay.getBounds();
  const display = screen.getDisplayMatching(bounds);
  const scale = display.scaleFactor;
  overlay.webContents.send('selftest-marker', true);
  await sleep(400);
  try {
    const sources = await desktopCapturer.getSources({
      types: ['screen'],
      thumbnailSize: { width: Math.round(display.size.width * scale), height: Math.round(display.size.height * scale) },
    });
    const source = sources.find((s) => String(s.display_id) === String(display.id)) || sources[0];
    if (!source || source.thumbnail.isEmpty()) {
      return { hidden: null, share: 0, detail: 'Could not capture the screen. Grant Screen Recording permission and retry.' };
    }
    const img = source.thumbnail;
    const size = img.getSize();
    const fx = size.width / display.size.width;
    const fy = size.height / display.size.height;
    const rect = {
      x: Math.max(0, Math.round((bounds.x - display.bounds.x) * fx) + 8),
      y: Math.max(0, Math.round((bounds.y - display.bounds.y) * fy) + 8),
      width: Math.max(1, Math.round(bounds.width * fx) - 16),
      height: Math.max(1, Math.round(bounds.height * fy) - 16),
    };
    rect.width = Math.min(rect.width, size.width - rect.x);
    rect.height = Math.min(rect.height, size.height - rect.y);
    const share = markerShare(img.toBitmap(), size.width, rect);
    const hidden = share < 0.05;
    return {
      hidden,
      share: Math.round(share * 1000) / 10,
      detail: hidden
        ? 'Overlay was NOT visible in a screen capture. Still confirm once in a real Teams/Meet share.'
        : 'Overlay WAS visible in a screen capture. Share a single window (e.g. PowerPoint) instead of the entire screen.',
    };
  } finally {
    overlay.webContents.send('selftest-marker', false);
  }
}

module.exports = { runCaptureSelfTest, MARKER };
