// record_scene.mjs — Step 7: deterministically record ONE scene to WebM.
//
// Uses Playwright's page.screencast API (>=1.59): real bitrate, animated
// cursor + action overlays (showActions), and click callouts (showOverlay).
// Paces the scene toward its narration duration so audio and video stay locked.
//
//   node record_scene.mjs --run-dir <dir> --scene-id 01 \
//        [--base-url https://site.test/wp-admin] [--force]
//
// Credentials (for real WP sites) are read from the env vars named in
// config.json (wp_user_env / wp_pass_env), never hard-coded.
//
// When --base-url points at a file:// fixture, login is skipped.
import { chromium } from 'playwright';
import { readFileSync, existsSync, mkdirSync, writeFileSync, statSync } from 'node:fs';
import { join, dirname } from 'node:path';

function arg(name, def = undefined) {
  const i = process.argv.indexOf(`--${name}`);
  if (i !== -1 && i + 1 < process.argv.length && !process.argv[i + 1].startsWith('--')) {
    return process.argv[i + 1];
  }
  return process.argv.includes(`--${name}`) ? true : def;
}

const runDir = arg('run-dir');
const sceneId = arg('scene-id');
const force = !!arg('force');
if (!runDir || !sceneId) {
  console.error('record_scene: --run-dir and --scene-id are required');
  process.exit(2);
}

const cfgPath = join(runDir, 'config.json');
const cfg = existsSync(cfgPath) ? JSON.parse(readFileSync(cfgPath, 'utf8')) : {};
const scriptPath = existsSync(join(runDir, 'script.discovered.json'))
  ? join(runDir, 'script.discovered.json')
  : join(runDir, 'script.json');
const script = JSON.parse(readFileSync(scriptPath, 'utf8'));
const scene = script.scenes.find((s) => s.id === sceneId);
if (!scene) { console.error(`record_scene: scene ${sceneId} not found`); process.exit(2); }

const [width, height] = (script.resolution || cfg.resolution || '1920x1080')
  .split('x').map((n) => parseInt(n, 10));
const baseUrl = arg('base-url', cfg.base_url || cfg.site_url);

const durPath = join(runDir, 'audio', 'durations.json');
const durations = existsSync(durPath) ? JSON.parse(readFileSync(durPath, 'utf8')) : {};
const narrationMs = Math.round((durations[sceneId] || 0) * 1000);

const outPath = join(runDir, 'clips', `${sceneId}.raw.webm`);
const focusOut = join(runDir, 'clips', `${sceneId}.focus.json`);
mkdirSync(dirname(outPath), { recursive: true });

if (existsSync(outPath) && statSync(outPath).size > 0 && !force) {
  console.log(`record_scene: scene ${sceneId} already recorded (use --force)`);
  process.exit(0);
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Human-speed typing: type char by char with small jitter-free delays.
async function humanType(locator, text) {
  await locator.click();
  for (const ch of text) {
    await locator.press(ch === ' ' ? 'Space' : ch, { delay: 0 });
    await sleep(55);
  }
}

async function waitSettled(page) {
  try { await page.waitForLoadState('networkidle', { timeout: 8000 }); }
  catch { /* best-effort: some admin pages keep long-poll connections open */ }
}

async function login(page) {
  // Only attempt for http(s) WP sites with creds configured.
  if (!baseUrl || baseUrl.startsWith('file://')) return;
  const userEnv = cfg.wp_user_env || 'WP_ADMIN_USER';
  const passEnv = cfg.wp_pass_env || 'WP_ADMIN_PASS';
  const user = process.env[userEnv];
  const pass = process.env[passEnv];
  if (!user || !pass) {
    console.error(`record_scene: missing creds in env ${userEnv}/${passEnv}`);
    process.exit(3);
  }
  const root = baseUrl.replace(/\/wp-admin\/?$/, '');
  await page.goto(`${root}/wp-login.php`, { waitUntil: 'domcontentloaded' });
  if (await page.locator('#user_login').count()) {
    await page.fill('#user_login', user);
    await page.fill('#user_pass', pass);
    await page.click('#wp-submit');
    await waitSettled(page);
  }
}

async function runAction(page, a) {
  const sel = a.selector;
  switch (a.type) {
    case 'goto': {
      const root = (baseUrl || '').replace(/\/wp-admin\/?$/, '');
      await page.goto(root + (a.target.startsWith('/') ? a.target : '/' + a.target),
        { waitUntil: 'domcontentloaded' });
      break;
    }
    case 'click': {
      const loc = page.locator(sel).first();
      await loc.waitFor({ state: 'visible', timeout: 15000 });
      await loc.scrollIntoViewIfNeeded();
      if (a.highlight) {
        const box = await loc.boundingBox();
        if (box) {
          await page.screencast.showOverlay(
            `<div style="position:fixed;left:${box.x - 6}px;top:${box.y - 6}px;` +
            `width:${box.width + 12}px;height:${box.height + 12}px;` +
            `border:3px solid #4f9dff;border-radius:8px;box-shadow:0 0 0 9999px rgba(0,0,0,.12);` +
            `pointer-events:none;"></div>`, { duration: 1200 });
        }
      }
      await sleep(250);
      await loc.click();
      break;
    }
    case 'type': {
      await humanType(page.locator(sel).first(), a.text || '');
      break;
    }
    case 'hover': {
      await page.locator(sel).first().hover();
      break;
    }
    case 'scroll': {
      await page.locator(sel).first().scrollIntoViewIfNeeded();
      break;
    }
    case 'wait': {
      await sleep(parseInt(a.text || '1000', 10));
      break;
    }
    default:
      console.error(`record_scene: unknown action type '${a.type}'`);
  }
  await waitSettled(page);
}

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width, height } });
const page = await context.newPage();

const started = Date.now();
await login(page);

// Navigate to the scene's starting point if the first action is not a goto and
// we have a base URL (real site). For fixtures, the caller passes file:// base.
if (baseUrl) {
  const target = baseUrl.startsWith('file://') ? baseUrl
    : baseUrl.replace(/\/$/, '') + '/wp-admin/';
  if (scene.actions[0]?.type !== 'goto') {
    await page.goto(target, { waitUntil: 'domcontentloaded' });
    await waitSettled(page);
  }
}

await page.screencast.start({ path: outPath, size: { width, height }, quality: 90 });
await page.screencast.showActions({ cursor: 'pointer', duration: 700 });
await page.screencast.showChapter(scene.intent || `Scene ${sceneId}`);

for (const a of scene.actions) {
  await runAction(page, a);
}

// Record the focus element's bounding box for the post-processor's zoom.
let focusBox = null;
if (scene.focus_selector) {
  try { focusBox = await page.locator(scene.focus_selector).first().boundingBox(); }
  catch { /* focus optional */ }
}
writeFileSync(focusOut, JSON.stringify({ box: focusBox, viewport: { width, height } }, null, 2));

await sleep(scene.hold_after_ms || 800);

// Pace toward the narration duration so the clip is never much shorter than
// the voiceover (post-processing still pads to the exact max).
const elapsed = Date.now() - started;
if (narrationMs && elapsed < narrationMs) {
  await sleep(narrationMs - elapsed);
}

await page.screencast.stop();
await browser.close();

const size = statSync(outPath).size;
if (!size) { console.error('record_scene: empty recording'); process.exit(4); }
console.log(`record_scene: wrote ${outPath} (${size} bytes)`);
