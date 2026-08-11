// record_scene.mjs — Step 7: deterministically record ONE scene to WebM (v2).
//
// v2: setup/recorded action phases (setup runs before capture starts), optional
// narration-cue timing (verify/scenes/NN.json word offsets), 2x device-scale
// capture (4K master), WordPress pre-flight checks (notice dismissal, PHP error
// regex, session-expiry detection), destructive-action guard, optional redaction.
//
//   node record_scene.mjs --run-dir <dir> --scene-id 01 \
//        [--base-url https://site.test] [--force]
//
// Credentials (for real WP sites) are read from the env vars named in
// config.json (wp_user_env / wp_pass_env), never hard-coded.
// When --base-url points at a file:// fixture, login and WP checks are skipped.
//
// Exit codes: 2 usage · 3 login/session · 4 empty recording · 5 destructive guard · 6 PHP error
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
const scale = Number(cfg.capture_scale ?? 2);
const baseUrl = arg('base-url', cfg.base_url || cfg.site_url);
const actionTimeout = Number(cfg.action_timeout_ms ?? 10000);
const isFixture = !!baseUrl && baseUrl.startsWith('file://');

const durations = existsSync(join(runDir, 'audio', 'durations.json'))
  ? JSON.parse(readFileSync(join(runDir, 'audio', 'durations.json'), 'utf8')) : {};
const narrationMs = Math.round((durations[sceneId] || 0) * 1000);

const wordsPath = join(runDir, 'verify', 'scenes', `${sceneId}.json`);
const sceneWords = existsSync(wordsPath)
  ? JSON.parse(readFileSync(wordsPath, 'utf8')).words || [] : [];

const outPath = join(runDir, 'clips', `${sceneId}.raw.webm`);
const focusOut = join(runDir, 'clips', `${sceneId}.focus.json`);
mkdirSync(dirname(outPath), { recursive: true });
if (existsSync(outPath) && statSync(outPath).size > 0 && !force) {
  console.log(`record_scene: scene ${sceneId} already recorded (use --force)`);
  process.exit(0);
}

// ---- destructive-action guard -------------------------------------------------
const DESTRUCTIVE = /delete|remove|trash|deactivate|uninstall|reset/i;
const risky = (scene.actions || []).filter((a) =>
  (a.phase ?? 'recorded') === 'recorded' && a.type === 'click' &&
  (DESTRUCTIVE.test(a.target || '') || DESTRUCTIVE.test(a.selector || '')));
if (risky.length && !cfg.allow_destructive) {
  console.error('record_scene: DESTRUCTIVE actions blocked (set allow_destructive=true to permit):');
  for (const a of risky) console.error(`  - ${a.target} (${a.selector})`);
  process.exit(5);
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const norm = (s) => s.toLowerCase().replace(/[^a-z0-9' ]+/g, ' ').replace(/\s+/g, ' ').trim();

// Cue → ms offset into the narration; monotonic search from the previous cue.
let cueCursor = 0;
function cueOffsetMs(cue) {
  if (!cue || !sceneWords.length) return null;
  const cueToks = norm(cue).split(' ').filter(Boolean);
  const toks = sceneWords.map((w) => norm(w.word));
  for (let i = Math.max(cueCursor, 0); i <= toks.length - cueToks.length; i++) {
    if (cueToks.every((t, k) => toks[i + k] === t)) {
      cueCursor = i + cueToks.length;
      return Math.round(sceneWords[i].start * 1000);
    }
  }
  return null; // cue not found → sequential pacing (never fails the run)
}

const PHP_ERROR = /(Fatal error|Parse error|Warning|Notice|Deprecated)\b[^<]{0,200}? in [^<]{0,300}? on line \d+/;

async function preflight(page) {
  if (isFixture) return;
  const url = page.url();
  if (url.includes('wp-login.php')) {
    console.error('record_scene: session expired (redirected to wp-login.php)');
    process.exit(3);
  }
  if (cfg.dismiss_notices !== false) {
    await page.evaluate(() => {
      document.querySelectorAll('.notice, .update-nag').forEach((el) => el.remove());
    }).catch(() => {});
  }
  const html = await page.content();
  const m = html.match(PHP_ERROR);
  if (m) {
    console.error(`record_scene: PHP error rendered on page: ${m[0].slice(0, 160)}`);
    process.exit(6);
  }
}

async function login(page) {
  if (!baseUrl || isFixture) return;
  const userEnv = cfg.wp_user_env || 'WP_ADMIN_USER';
  const passEnv = cfg.wp_pass_env || 'WP_ADMIN_PASS';
  const user = process.env[userEnv];
  const pass = process.env[passEnv];
  if (!user || !pass) {
    console.error(`record_scene: missing creds in env ${userEnv}/${passEnv}`);
    process.exit(3);
  }
  const root = baseUrl.replace(/\/wp-admin\/?$/, '').replace(/\/$/, '');
  await page.goto(`${root}/wp-login.php`, { waitUntil: 'domcontentloaded' });
  if (await page.locator('#user_login').count()) {
    await page.fill('#user_login', user);
    await page.fill('#user_pass', pass);
    await Promise.all([
      page.waitForURL(/wp-admin/, { timeout: 15000 }).catch(() => {}),
      page.click('#wp-submit'),
    ]);
  }
  const inAdmin = /\/wp-admin\//.test(page.url()) ||
    (await page.locator('body.wp-admin').count()) > 0;
  if (!inAdmin) {
    console.error(`record_scene: login failed — landed on ${page.url()}. `
      + 'Check credentials and that the user has admin access.');
    process.exit(3);
  }
}

async function runAction(page, a) {
  const sel = a.selector;
  switch (a.type) {
    case 'goto': {
      const root = (baseUrl || '').replace(/\/wp-admin\/?$/, '').replace(/\/$/, '');
      const target = isFixture ? baseUrl
        : root + (a.target.startsWith('/') ? a.target : '/' + a.target);
      await page.goto(target, { waitUntil: 'domcontentloaded' });
      await preflight(page);
      break;
    }
    case 'click': {
      const loc = page.locator(sel).first();
      await loc.waitFor({ state: 'visible', timeout: actionTimeout });
      await loc.evaluate((el) =>
        el.scrollIntoView({ behavior: 'smooth', block: 'center' })).catch(() => {});
      await sleep(350);
      if (a.highlight) {
        const box = await loc.boundingBox();
        if (box) {
          await page.screencast.showOverlay(
            `<div style="position:fixed;left:${box.x - 6}px;top:${box.y - 6}px;` +
            `width:${box.width + 12}px;height:${box.height + 12}px;` +
            `border:3px solid #4f9dff;border-radius:8px;` +
            `box-shadow:0 0 0 9999px rgba(0,0,0,.12);pointer-events:none;"></div>`,
            { duration: 1200 });
          await sleep(250);
        }
      }
      await loc.click({ timeout: actionTimeout });
      await page.waitForLoadState('domcontentloaded', { timeout: actionTimeout }).catch(() => {});
      await preflight(page);
      break;
    }
    case 'type': {
      const loc = page.locator(sel).first();
      await loc.waitFor({ state: 'visible', timeout: actionTimeout });
      await loc.click();
      await loc.pressSequentially(a.text || '', { delay: 60 });
      break;
    }
    case 'hover': {
      await page.locator(sel).first().hover({ timeout: actionTimeout });
      break;
    }
    case 'scroll': {
      await page.locator(sel).first().evaluate((el) =>
        el.scrollIntoView({ behavior: 'smooth', block: 'center' })).catch(() => {});
      await sleep(600);
      break;
    }
    case 'wait': {
      await sleep(parseInt(a.text || '1000', 10));
      break;
    }
    default:
      console.error(`record_scene: unknown action type '${a.type}'`);
  }
}

const browser = await chromium.launch();
const context = await browser.newContext({
  viewport: { width, height },
  deviceScaleFactor: scale,
  ignoreHTTPSErrors: cfg.ignore_https_errors !== false,
});
if ((cfg.redact_selectors || []).length || (cfg.redact_patterns || []).length) {
  const selectors = JSON.stringify(cfg.redact_selectors || []);
  const patterns = JSON.stringify(cfg.redact_patterns || []);
  await context.addInitScript(`(() => {
    const SEL = ${selectors}; const PAT = ${patterns}.map((p) => new RegExp(p, 'g'));
    const blur = () => {
      SEL.forEach((s) => document.querySelectorAll(s).forEach((el) => {
        el.style.filter = 'blur(6px)';
      }));
      if (PAT.length) {
        document.querySelectorAll('input, td, code, span').forEach((el) => {
          const v = el.value || el.textContent || '';
          if (PAT.some((r) => (r.lastIndex = 0, r.test(v)))) el.style.filter = 'blur(6px)';
        });
      }
    };
    new MutationObserver(blur).observe(document.documentElement, { childList: true, subtree: true });
    document.addEventListener('DOMContentLoaded', blur);
  })()`);
}
const page = await context.newPage();

await login(page);

// ---- setup phase (off camera): reach the scene's start state -------------------
const setupActions = (scene.actions || []).filter((a) => (a.phase ?? 'recorded') === 'setup');
const recordedActions = (scene.actions || []).filter((a) => (a.phase ?? 'recorded') !== 'setup');

if (baseUrl && setupActions[0]?.type !== 'goto') {
  const target = isFixture ? baseUrl : baseUrl.replace(/\/$/, '') + '/wp-admin/';
  await page.goto(target, { waitUntil: 'domcontentloaded' });
  await preflight(page);
}
for (const a of setupActions) {
  await runAction(page, a);
}
await sleep(400); // settle before capture

// ---- recorded phase -------------------------------------------------------------
await page.screencast.start({
  path: outPath,
  size: { width: width * scale, height: height * scale },
  quality: 90,
});
await page.screencast.showActions({ cursor: 'pointer', duration: 700 });
if (cfg.chapter_cards) {
  await page.screencast.showChapter(scene.intent || `Scene ${sceneId}`);
}

const recStart = Date.now(); // pacing measured from capture start (v1 bug fix)
for (const a of recordedActions) {
  const at = cueOffsetMs(a.cue);
  if (at !== null) {
    const wait = at - (Date.now() - recStart);
    if (wait > 0) await sleep(wait);
  }
  await runAction(page, a);
}

// Record the focus element's bounding box (CSS px) + capture scale for the
// post-processor's zoom (multiply by `scale` for master-pixel coordinates).
let focusBox = null;
if (scene.focus_selector) {
  try { focusBox = await page.locator(scene.focus_selector).first().boundingBox(); }
  catch { /* focus optional */ }
}
writeFileSync(focusOut, JSON.stringify(
  { box: focusBox, viewport: { width, height }, scale }, null, 2));

await sleep(scene.hold_after_ms || 800);

// Pace toward the narration duration so the clip is never much shorter than
// the voiceover (post-processing still pads to the exact max).
const elapsed = Date.now() - recStart;
if (narrationMs && elapsed < narrationMs) {
  await sleep(narrationMs - elapsed);
}

await page.screencast.stop();
await browser.close();

const size = statSync(outPath).size;
if (!size) { console.error('record_scene: empty recording'); process.exit(4); }
console.log(`record_scene: wrote ${outPath} (${size} bytes, ${width * scale}x${height * scale})`);
