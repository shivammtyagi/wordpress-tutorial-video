// record_scene.mjs — Step 7: deterministically record ONE scene to WebM (v2).
//
// v2: setup/recorded action phases (setup runs before capture starts), optional
// narration-cue timing (verify/scenes/NN.json word offsets), 2x device-scale
// capture (4K master), WordPress pre-flight checks (notice dismissal, PHP error
// regex, session-expiry detection), destructive-action guard, optional redaction.
//
//   node record_scene.mjs --run-dir <dir> --scene-id 01 \
//        [--base-url https://site.test] [--force]
//   node record_scene.mjs --run-dir <dir> --scene-ids 06,07,08 ...   (chained)
//
// Chained scenes (--scene-ids): ONE browser session records several scenes in
// order — the first scene logs in and runs its setup; each later scene runs
// only its own setup-phase actions (typically a `wait` for the state the
// previous scene's action produced) in the same page, then captures. Use it
// when a scene's start state is the *result* of the previous scene (an AI run
// that takes a minute, a wizard) and cannot be re-created off camera. Every
// capture still stays short; only the page persists.
//
// Credentials (for real WP sites) are read from the env vars named in
// config.json (wp_user_env / wp_pass_env), never hard-coded.
// When --base-url points at a file:// fixture, login and WP checks are skipped.
//
// Exit codes: 2 usage · 3 login/session · 4 empty recording · 5 destructive guard · 6 PHP error · 7 browser died mid-capture (scene too long / low memory)
import { chromium } from 'playwright';
import { readFileSync, existsSync, mkdirSync, writeFileSync, statSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { execSync } from 'node:child_process';

// Genuine macOS arrow cursor (extracted from NSCursor.arrow at 5x), logical
// size 28x40 with hotspot (5,5) — displayed at logical size so the capture's
// deviceScaleFactor keeps it razor-sharp.
const CURSOR_PNG_B64 = readFileSync(join(
  dirname(fileURLToPath(import.meta.url)), '..', 'assets', 'cursor-macos.png',
)).toString('base64');

function arg(name, def = undefined) {
  const i = process.argv.indexOf(`--${name}`);
  if (i !== -1 && i + 1 < process.argv.length && !process.argv[i + 1].startsWith('--')) {
    return process.argv[i + 1];
  }
  return process.argv.includes(`--${name}`) ? true : def;
}

const runDir = arg('run-dir');
const sceneIdsArg = arg('scene-ids');
const sceneIds = typeof sceneIdsArg === 'string' && sceneIdsArg
  ? sceneIdsArg.split(',').map((x) => x.trim()).filter(Boolean)
  : (arg('scene-id') ? [arg('scene-id')] : []);
const force = !!arg('force');
if (!runDir || !sceneIds.length) {
  console.error('record_scene: --run-dir and --scene-id (or --scene-ids a,b,c) are required');
  process.exit(2);
}
const chained = sceneIds.length > 1;

const cfgPath = join(runDir, 'config.json');
const cfg = existsSync(cfgPath) ? JSON.parse(readFileSync(cfgPath, 'utf8')) : {};
const scriptPath = existsSync(join(runDir, 'script.discovered.json'))
  ? join(runDir, 'script.discovered.json')
  : join(runDir, 'script.json');
const script = JSON.parse(readFileSync(scriptPath, 'utf8'));
const scenes = sceneIds.map((id) => {
  const sc = script.scenes.find((s) => s.id === id);
  if (!sc) { console.error(`record_scene: scene ${id} not found`); process.exit(2); }
  return sc;
});
// per-scene mutable context (set by beginScene)
let scene = scenes[0];
let sceneId = scene.id;

const [width, height] = (script.resolution || cfg.resolution || '1920x1080')
  .split('x').map((n) => parseInt(n, 10));
const scale = Number(cfg.capture_scale ?? 2);
const baseUrl = arg('base-url', cfg.base_url || cfg.site_url);
const actionTimeout = Number(cfg.action_timeout_ms ?? 10000);
const accent = cfg.accent_color || '#2271b1'; // highlight/ripple color (WP admin blue default)
const isFixture = !!baseUrl && baseUrl.startsWith('file://');

const durations = existsSync(join(runDir, 'audio', 'durations.json'))
  ? JSON.parse(readFileSync(join(runDir, 'audio', 'durations.json'), 'utf8')) : {};
let narrationMs = 0;
let sceneWords = [];
let outPath = '';
let focusOut = '';
mkdirSync(join(runDir, 'clips'), { recursive: true });

function beginScene(sc) {
  scene = sc;
  sceneId = sc.id;
  narrationMs = Math.round((durations[sceneId] || 0) * 1000);
  const wordsPath = join(runDir, 'verify', 'scenes', `${sceneId}.json`);
  sceneWords = existsSync(wordsPath)
    ? JSON.parse(readFileSync(wordsPath, 'utf8')).words || [] : [];
  outPath = join(runDir, 'clips', `${sceneId}.raw.webm`);
  focusOut = join(runDir, 'clips', `${sceneId}.focus.json`);
  cueCursor = 0;
  events.length = 0;
}

// Already-recorded check happens for every listed scene BEFORE any browser
// work: a chain must not spend a minute (or AI credits) and then skip.
for (const sc of scenes) {
  const p = join(runDir, 'clips', `${sc.id}.raw.webm`);
  if (existsSync(p) && statSync(p).size > 0 && !force) {
    console.log(`record_scene: scene ${sc.id} already recorded (use --force)`);
    process.exit(0);
  }
}

// ---- destructive-action guard -------------------------------------------------
const DESTRUCTIVE = /delete|remove|trash|deactivate|uninstall|reset/i;
const risky = scenes.flatMap((sc) => (sc.actions || []).filter((a) =>
  (a.phase ?? 'recorded') === 'recorded' && a.type === 'click' &&
  (DESTRUCTIVE.test(a.target || '') || DESTRUCTIVE.test(a.selector || ''))));
if (risky.length && !cfg.allow_destructive) {
  console.error('record_scene: DESTRUCTIVE actions blocked (set allow_destructive=true to permit):');
  for (const a of risky) console.error(`  - ${a.target} (${a.selector})`);
  process.exit(5);
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const norm = (s) => s.toLowerCase().replace(/[^a-z0-9' ]+/g, ' ').replace(/\s+/g, ' ').trim();

// Action-event log (recorded phase only) → clips/NN.events.json, so a later
// mixing step can lay click sounds exactly where the on-camera clicks landed.
const events = [];
let capturing = false;
let captureT0 = 0;
const logEvent = (kind, extra = {}) => {
  if (capturing) events.push({ kind, t: Date.now() - captureT0, ...extra });
};

// Glide the DOM cursor to an element's center (layout px = zoomed px / scale)
// and wait out the transition. Returns the element's box for coordinate input.
async function glideCursorTo(page, loc) {
  const box = await loc.boundingBox().catch(() => null);
  if (!box) return null;
  const x = (box.x + box.width / 2) / scale;
  const y = (box.y + box.height / 2) / scale;
  await page.evaluate(([cx, cy]) => {
    const c = document.getElementById('__wtv_cursor');
    if (c) { c.style.left = cx + 'px'; c.style.top = cy + 'px'; }
  }, [x, y]).catch(() => {});
  await sleep(650); // let the glide finish before the action lands
  return box;
}

// Visible press feedback AT the moment the real click fires: ripple ring plus
// a quick cursor press-nudge. Must be called right before the mouse click so
// the viewer sees cursor-arrive → press → result in the correct order.
async function pressEffect(page, box) {
  const x = (box.x + box.width / 2) / scale;
  const y = (box.y + box.height / 2) / scale;
  await page.evaluate(([cx, cy, ac]) => {
    const c = document.getElementById('__wtv_cursor');
    if (c) {
      c.style.transition += ',transform .09s ease-out';
      c.style.transform = 'scale(.82)';
      setTimeout(() => { c.style.transform = 'scale(1)'; }, 110);
    }
    const r = document.createElement('div');
    r.style.cssText = `position:fixed;left:${cx - 17}px;top:${cy - 17}px;width:34px;height:34px;` +
      `border-radius:50%;border:3px solid ${ac};z-index:2147483646;pointer-events:none;` +
      'opacity:.95;transform:scale(.35);transition:transform .4s ease-out,opacity .4s ease-out;';
    document.body.appendChild(r);
    requestAnimationFrame(() => { r.style.transform = 'scale(1.7)'; r.style.opacity = '0'; });
    setTimeout(() => r.remove(), 550);
  }, [x, y, accent]).catch(() => {});
}

// Accent-colored callout ring (with page dim) around an element, drawn via the
// screencast overlay so it sits in the recording but never in the DOM.
async function showHighlight(page, loc) {
  const box = await loc.boundingBox().catch(() => null);
  if (!box) return;
  // overlay lives inside the zoomed document → divide by scale
  const [hx, hy, hw, hh] = [box.x / scale, box.y / scale, box.width / scale, box.height / scale];
  await page.screencast.showOverlay(
    `<div style="position:fixed;left:${hx - 6}px;top:${hy - 6}px;` +
    `width:${hw + 12}px;height:${hh + 12}px;` +
    `border:3px solid ${accent};border-radius:10px;` +
    `box-shadow:0 0 0 9999px rgba(0,0,0,.12);pointer-events:none;"></div>`,
    { duration: 1200 });
  await sleep(250);
}

// Fallback for elements a DOM scrollIntoView cannot reach (nested scrollers,
// a modal footer below the fold): Playwright's protocol-level scroll handles
// every scrollable ancestor. Only used when the element is still off-screen,
// so the cinematic scroll stays in charge for the normal case.
async function ensureOnScreen(page, loc) {
  const box = await loc.boundingBox().catch(() => null);
  if (!box) return;
  const vw = width * scale, vh = height * scale;
  const off = box.y < 0 || box.x < 0 || box.y + box.height > vh || box.x + box.width > vw;
  if (!off) return;
  await loc.scrollIntoViewIfNeeded({ timeout: 5000 }).catch(() => {});
  await sleep(450);
  const b2 = await loc.boundingBox().catch(() => null);
  if (b2 && (b2.y < 0 || b2.y + b2.height > vh)) {
    console.error(`record_scene: element still off-screen after scroll (y=${Math.round(b2.y)} of ${vh}) — check the selector/scroll for this action`);
  }
}

// Scroll an element toward the viewport center only when it is not already
// comfortably in view — avoids gratuitous page motion between actions.
async function ensureCentered(page, loc) {
  const scrolled = await loc.evaluate((el) => {
    const r = el.getBoundingClientRect();
    const vh = window.innerHeight;
    if (r.top < vh * 0.15 || r.bottom > vh * 0.85) {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' });
      return true;
    }
    return false;
  }).catch(() => false);
  await sleep(scrolled ? 550 : 120);
}

// Cue → ms offset into the narration; monotonic search from the previous cue.
// Matching is tolerant of transcription word-splits ("Sitemaps" vs "Site Maps"):
// the space-stripped cue is compared against 1..4 adjacent transcript tokens
// concatenated.
let cueCursor = 0;
function cueOffsetMs(cue) {
  if (!cue || !sceneWords.length) return null;
  const target = norm(cue).replace(/ /g, '');
  if (!target) return null;
  const toks = sceneWords.map((w) => norm(w.word).replace(/ /g, ''));
  for (let i = Math.max(cueCursor, 0); i < toks.length; i++) {
    let joined = '';
    for (let k = 0; k < 4 && i + k < toks.length; k++) {
      joined += toks[i + k];
      if (joined === target) {
        cueCursor = i + k + 1;
        return Math.round(sceneWords[i].start * 1000);
      }
      if (joined.length >= target.length) break;
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
      await ensureCentered(page, loc);
      await ensureOnScreen(page, loc);
      if (a.highlight) await showHighlight(page, loc);
      // Coordinate input, never loc.click(): Playwright's actionability loop
      // re-fires its own instant scrollIntoView on every retry, which fights
      // the cinematic smooth scroll and visibly bounces the page on widgets
      // that never pass the stability check (vue-multiselect). We already
      // scrolled, glided, and verified visibility — click where the cursor is.
      const box2 = await glideCursorTo(page, loc);
      // page.screencast FREEZES on renderer-initiated cross-document
      // navigations (verified empirically; API goto records fine). For links
      // that leave the current document: perform the click visually with its
      // navigation prevented, then drive the same navigation via goto — the
      // viewer sees an identical click, and the capture never freezes.
      const href = await loc.getAttribute('href').catch(() => null);
      const crossDoc = href && !href.startsWith('#') && !href.startsWith('javascript:');
      if (crossDoc) {
        await loc.evaluate((el) => el.addEventListener(
          'click', (e) => e.preventDefault(), { capture: true, once: true }));
      }
      if (box2) {
        await pressEffect(page, box2);
        await page.mouse.click(box2.x + box2.width / 2, box2.y + box2.height / 2);
      } else {
        await loc.click({ force: true, timeout: 5000 });
      }
      logEvent('click');
      if (crossDoc) {
        const target = new URL(href, page.url()).toString();
        await page.goto(target, { waitUntil: 'domcontentloaded' });
      } else {
        await page.waitForLoadState('domcontentloaded', { timeout: actionTimeout }).catch(() => {});
      }
      await preflight(page);
      break;
    }
    case 'type': {
      const loc = page.locator(sel).first();
      await loc.waitFor({ state: 'visible', timeout: actionTimeout });
      await ensureCentered(page, loc);
      const box = await glideCursorTo(page, loc);
      if (box) {
        await pressEffect(page, box);
        await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
      } else {
        await loc.click({ force: true, timeout: 5000 });
      }
      logEvent('click');
      await sleep(280); // let the focus/caret land so the click reads on camera
      logEvent('type', { chars: (a.text || '').length, delay: 60 });
      // keyboard.type targets the focused element (our click just focused it)
      // and performs no element re-checks that could scroll the page mid-word.
      await page.keyboard.type(a.text || '', { delay: 60 });
      logEvent('type_end');
      break;
    }
    case 'hover': {
      // Cursor first, ring second: the pointer lands on the element at the cue,
      // then the highlight frames what it is resting on (a click is the other
      // way round — the ring marks where the click will land).
      const loc = page.locator(sel).first();
      await loc.waitFor({ state: 'visible', timeout: actionTimeout });
      await ensureCentered(page, loc);
      await ensureOnScreen(page, loc);
      const box = await glideCursorTo(page, loc);
      if (box) {
        await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
      } else {
        await loc.hover({ force: true, timeout: 5000 });
      }
      if (a.highlight) await showHighlight(page, loc);
      break;
    }
    case 'press': {
      // A key or chord ("Enter", "Meta+A", "Backspace", "Escape") sent to the
      // element that already has focus — for edits a mouse can't express, such
      // as clearing a rich-text field. Focus it first with a click/type action.
      logEvent('press', { key: a.text });
      await page.keyboard.press(a.text);
      await sleep(250);
      break;
    }
    case 'scroll': {
      const loc = page.locator(sel).first();
      await loc.evaluate((el) =>
        el.scrollIntoView({ behavior: 'smooth', block: 'center' })).catch(() => {});
      await sleep(600);
      await ensureOnScreen(page, loc);
      break;
    }
    case 'wait': {
      // plain: sleep `text` ms. With a selector: wait up to `text` ms (default
      // 60s) for that element to be visible — how a chained scene waits for
      // the result of the previous scene's action (an AI run, a page load).
      if (sel) {
        // `hidden: true` inverts it: wait for the element to go away (a modal closing).
        await page.locator(sel).first().waitFor({ state: a.hidden ? 'hidden' : 'visible', timeout: parseInt(a.text || '60000', 10) });
        await sleep(400);
      } else {
        await sleep(parseInt(a.text || '1000', 10));
      }
      break;
    }
    default:
      console.error(`record_scene: unknown action type '${a.type}'`);
  }
}

// 2x capture technique: page.screencast records at CSS pixels regardless of
// deviceScaleFactor (verified empirically), so we open the viewport at the
// MASTER size and CSS-zoom the document by `scale`. Layout matches the delivery
// resolution exactly while every pixel is rendered at scale× density.
// Consequence: element coordinates from boundingBox() come back in zoomed
// (master) pixels — divide by `scale` before positioning injected overlays
// (they live inside the zoomed document and get re-scaled on render).
// Browser binary: the bundled Playwright Chromium by default, or an installed
// channel (`browser_channel: "chrome"` → Google Chrome with a throwaway
// profile — never the user's running instance). Use a channel when the
// bundled headless shell is unstable on the machine (see SKILL.md
// troubleshooting: "browser dies ~30s after launch").
const launchOpts = {};
if (cfg.browser_channel) launchOpts.channel = cfg.browser_channel;
const browser = await chromium.launch(launchOpts);
const context = await browser.newContext({
  viewport: { width: width * scale, height: height * scale },
  ignoreHTTPSErrors: cfg.ignore_https_errors !== false,
});
if (scale !== 1) {
  await context.addInitScript(`(() => {
    const apply = () => { document.documentElement.style.zoom = '${scale}'; };
    document.addEventListener('DOMContentLoaded', apply);
    apply();
  })()`);
}
// Tutorial cursor: a DOM pointer that glides between action targets with an
// eased CSS transition (Playwright's showActions cursor double-scales under
// zoom, so we draw our own — one code path for every capture scale).
await context.addInitScript(`(() => {
  const mk = () => {
    if (document.getElementById('__wtv_cursor') || !document.body) return;
    const c = document.createElement('div');
    c.id = '__wtv_cursor';
    c.style.cssText = 'position:fixed;left:40%;top:40%;width:28px;height:40px;' +
      'margin-left:-5px;margin-top:-5px;' +  // NSCursor.arrow hotspot (5,5)
      'z-index:2147483647;pointer-events:none;' +
      'transition:left .55s cubic-bezier(.25,.1,.25,1),top .55s cubic-bezier(.25,.1,.25,1);';
    // DOM APIs, not innerHTML: pages with a Trusted Types CSP (AIOSEO's
    // settings app, for one) reject innerHTML and the cursor would vanish.
    const img = document.createElement('img');
    img.src = 'data:image/png;base64,${CURSOR_PNG_B64}';
    img.alt = '';
    img.style.cssText = 'width:28px;height:40px;display:block';
    c.appendChild(img);
    document.body.appendChild(c);
  };
  document.addEventListener('DOMContentLoaded', mk);
  mk();
})()`);
if (cfg.inject_css) {
  // Capture-time CSS overrides for layout that misbehaves under the CSS-zoom
  // 4K capture (a modal sized in vh that outgrows the viewport, a dropdown
  // that mis-measures). Applied to every page the recorder opens, never to the
  // site itself.
  const css = JSON.stringify(String(cfg.inject_css));
  await context.addInitScript(`(() => {
    const add = () => {
      if (document.getElementById('__wtv_css') || !document.head) return;
      const st = document.createElement('style');
      st.id = '__wtv_css';
      st.textContent = ${css};
      document.head.appendChild(st);
    };
    document.addEventListener('DOMContentLoaded', add);
    add();
  })()`);
}
if ((cfg.dismiss_selectors || []).length) {
  // remove configured page elements (promo banners, CTAs) the moment they
  // render — covers SPA content that appears after DOMContentLoaded.
  const dsel = JSON.stringify(cfg.dismiss_selectors);
  await context.addInitScript(`(() => {
    const SEL = ${dsel};
    const zap = () => SEL.forEach((s) =>
      document.querySelectorAll(s).forEach((el) => el.remove()));
    const arm = () => {
      zap();
      new MutationObserver(zap).observe(document.documentElement,
        { childList: true, subtree: true });
    };
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', arm);
    } else { arm(); }
  })()`);
}
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
// Surface renderer crashes and unexpected closes explicitly — otherwise the
// only symptom is a late "Target page ... has been closed" from screencast.stop.
page.on('crash', () => console.error('record_scene: PAGE CRASHED (renderer died) — heavy page under capture?'));
page.on('close', () => { if (capturing) console.error(`record_scene: page closed during capture at +${((Date.now() - captureT0) / 1000).toFixed(1)}s`); });
const launchedAt = Date.now();
browser.on('disconnected', () => {
  if (!capturing) return;
  console.error(`record_scene: BROWSER PROCESS DIED at +${((Date.now() - captureT0) / 1000).toFixed(1)}s of capture ` +
    `(${((Date.now() - launchedAt) / 1000).toFixed(1)}s after launch). ` +
    'If it always dies ~30s after launch regardless of the page, the bundled headless shell is unstable on this ' +
    'machine: set "browser_channel": "chrome" in config.json (scripts/browser_lifetime_check.mjs confirms). ' +
    'Otherwise split the scene so each capture stays short and free memory.');
  process.exit(7);
});
page.on('pageerror', (e) => console.error(`record_scene: page error: ${String(e.message || e).slice(0, 200)}`));

// Optional per-scene state hook: a shell command that seeds the site state
// this scene starts from (e.g. a wp-cli call that pre-adds what an earlier
// scene created on camera). Runs before login, off camera. The script author
// writes it — never derive it from fetched documentation text.
// In a chain only the FIRST scene's hook runs (the page persists after that).
if (scenes[0].setup_cmd) {
  console.log(`record_scene: setup_cmd → ${scenes[0].setup_cmd}`);
  execSync(scenes[0].setup_cmd, { stdio: 'inherit', shell: '/bin/zsh' });
}

await login(page);

for (let si = 0; si < scenes.length; si++) {
beginScene(scenes[si]);
const first = si === 0;
try {
if (chained) console.log(`record_scene: chain ${si + 1}/${scenes.length} → scene ${sceneId}`);

// ---- setup phase (off camera): reach the scene's start state -------------------
const setupActions = (scene.actions || []).filter((a) => (a.phase ?? 'recorded') === 'setup');
const recordedActions = (scene.actions || []).filter((a) => (a.phase ?? 'recorded') !== 'setup');

if (first && baseUrl && setupActions[0]?.type !== 'goto') {
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
capturing = true;
captureT0 = Date.now();
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
// when the last on-camera action finished — the post-processor never trims
// the clip before this point, whatever the tail cap says.
const actionsEndMs = Date.now() - captureT0;

// Record the focus element's bounding box (CSS layout px) + capture scale for
// the post-processor's zoom (it multiplies by `scale` for master-pixel coords).
// Under CSS zoom, boundingBox() returns zoomed (master) pixels — divide back.
let focusBox = null;
if (scene.focus_selector) {
  try {
    const b = await page.locator(scene.focus_selector).first().boundingBox();
    if (b) {
      focusBox = { x: b.x / scale, y: b.y / scale,
                   width: b.width / scale, height: b.height / scale };
    }
  } catch { /* focus optional */ }
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
capturing = false;
writeFileSync(join(runDir, 'clips', `${sceneId}.events.json`),
  JSON.stringify({ events, actions_end_ms: actionsEndMs }, null, 2));

const size = statSync(outPath).size;
if (!size) { console.error('record_scene: empty recording'); process.exit(4); }
console.log(`record_scene: wrote ${outPath} (${size} bytes, ${width * scale}x${height * scale})`);
} catch (err) {
  // Evidence before exit: what the page looked like when the action failed.
  const shot = join(runDir, 'clips', `${sceneId}.error.png`);
  console.error(`record_scene: scene ${sceneId} FAILED: ${String(err.message || err).split('\n')[0].slice(0, 200)}`);
  try { await page.screenshot({ path: shot }); console.error(`record_scene: page state saved to ${shot}`); } catch { /* page gone */ }
  if (capturing) { try { await page.screencast.stop(); } catch { /* ignore */ } capturing = false; }
  try { await browser.close(); } catch { /* ignore */ }
  process.exit(1);
}
} // end per-scene loop

await browser.close();
