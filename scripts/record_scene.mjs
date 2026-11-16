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
// config.json (wp_user_env / wp_pass_env), never hard-coded. With
// `browser_profile` set, no credentials are used at all: the recorder reuses a
// browser profile the user signed into by hand (scripts/sign_in.mjs).
// When --base-url points at a file:// fixture, login and WP checks are skipped.
//
// Exit codes: 2 usage · 3 login/session · 4 empty recording · 5 destructive guard · 6 PHP error · 7 browser died mid-capture (scene too long / low memory)
import { chromium } from 'playwright';
import { readFileSync, existsSync, mkdirSync, writeFileSync, statSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { execSync } from 'node:child_process';
import { resolveProfileDir, profileLaunchOpts } from './lib/profile.mjs';
import { makeWriteBlocker, isAbsoluteUrl, sameOrigin } from './lib/guards.mjs';

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
// Document zoom. Defaults to the capture scale (layout = delivery resolution);
// a smaller value zooms the UI out so more fits in the frame — ui_zoom 1.5
// with capture_scale 2 lays the page out at 2560x1440 inside the 4K capture
// (everything appears at 75% of the 1080p-layout size).
const zoom = Number(cfg.ui_zoom ?? scale);
const baseUrl = arg('base-url', cfg.base_url || cfg.site_url);
const actionTimeout = Number(cfg.action_timeout_ms ?? 10000);
const accent = cfg.accent_color || '#2271b1'; // highlight/ripple color (WP admin blue default)
const isFixture = !!baseUrl && baseUrl.startsWith('file://');
// Signed-in persistent profile (config `browser_profile`); null = default mode.
const profileDir = isFixture ? null : resolveProfileDir(cfg.browser_profile, runDir);
// WTV_DEBUG=1 prints per-step timings for each action.
const dbgT0 = Date.now();
const dbg = process.env.WTV_DEBUG
  ? (msg) => console.log(`[dbg +${((Date.now() - dbgT0) / 1000).toFixed(2)}s] ${msg}`) : () => {};

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
function pointIn(box, at) {
  if (at === 'start') return { x: box.x + 2 * zoom, y: box.y + Math.min(box.height / 2, 14 * zoom) }; // inside the first glyph's left half → caret before it
  return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
}

async function glideCursorTo(page, loc, at) {
  const box = await bbox(page, loc);
  if (!box) return null;
  const pt = pointIn(box, at);
  const x = pt.x / zoom;
  const y = pt.y / zoom;
  await page.evaluate(([cx, cy]) => {
    const c = document.getElementById('__wtv_cursor');
    if (c) { c.style.left = cx + 'px'; c.style.top = cy + 'px'; }
  }, [x, y]).catch(() => {});
  await sleep(650); // let the glide finish before the action lands
  return box;
}

// A target inside a scroll container may still be gliding (smooth scroll of
// the pane, a tab switch re-layout) when we read its box; clicking the stale
// point lands on whatever is there now — in the block editor that was the
// "Meta Boxes" pane toggle. Wait until two consecutive readings agree.
async function settledBox(page, loc) {
  let prev = await bbox(page, loc);
  for (let i = 0; i < 8; i++) {
    await sleep(150);
    const cur = await bbox(page, loc);
    if (!cur || !prev) return cur;
    if (Math.abs(cur.y - prev.y) < 2 && Math.abs(cur.x - prev.x) < 2) return cur;
    prev = cur;
  }
  return prev;
}

// Would a click at the box center actually reach the element? (Not checked for
// elements inside an iframe: their document has its own hit-testing.)
async function hitsTarget(loc, box, sel) {
  if (!box || /^frame=/.test(sel || '')) return true;
  return loc.evaluate((el, [x, y]) => {
    const h = document.elementFromPoint(x, y);
    return !!h && (el === h || el.contains(h) || h.contains(el));
  }, [box.x + box.width / 2, box.y + box.height / 2]).catch(() => true);
}

// Settle + verify before a coordinate click; re-center and retry once when the
// point is covered. Returns the box to click (or null).
async function clickableBox(page, loc, sel) {
  let box = await settledBox(page, loc);
  if (await hitsTarget(loc, box, sel)) return box;
  await loc.evaluate((el) => el.scrollIntoView({ behavior: 'instant', block: 'center' })).catch(() => {});
  await sleep(400);
  await glideCursorTo(page, loc);
  box = await settledBox(page, loc);
  if (!(await hitsTarget(loc, box, sel))) {
    console.error(`record_scene: click point on "${sel}" is covered by another element — clicking anyway, check the frame`);
  }
  return box;
}

// Visible press feedback AT the moment the real click fires: ripple ring plus
// a quick cursor press-nudge. Must be called right before the mouse click so
// the viewer sees cursor-arrive → press → result in the correct order.
async function pressEffect(page, box) {
  const x = (box.x + box.width / 2) / zoom;
  const y = (box.y + box.height / 2) / zoom;
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
  const box = await bbox(page, loc);
  if (!box) return;
  // overlay lives inside the zoomed document → divide by scale
  const [hx, hy, hw, hh] = [box.x / zoom, box.y / zoom, box.width / zoom, box.height / zoom];
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
  const box = await bbox(page, loc);
  if (!box) return;
  const vw = width * scale, vh = height * scale;
  const off = box.y < 0 || box.x < 0 || box.y + box.height > vh || box.x + box.width > vw;
  if (!off) return;
  await loc.scrollIntoViewIfNeeded({ timeout: 5000 }).catch(() => {});
  await sleep(450);
  const b2 = await bbox(page, loc);
  if (b2 && (b2.y < 0 || b2.y + b2.height > vh)) {
    console.error(`record_scene: element still off-screen after scroll (y=${Math.round(b2.y)} of ${vh}) — check the selector/scroll for this action`);
  }
}

// Nested scroll boxes (a check list with a fixed max-height, a modal body):
// scrollIntoView on the page alone leaves the element clipped by its scrollable
// ancestor. Scroll each such ancestor so the element sits in its middle first.
async function ensureUnclipped(page, loc) {
  const moved = await loc.evaluate((el) => {
    let moved = false;
    const doc = el.ownerDocument;
    let n = el.parentElement;
    // Only real nested scroll boxes: the document's own scroller (html/body —
    // the block editor canvas iframe scrolls on its root) is left to
    // scrollIntoView, because its rect is the whole content, not its viewport.
    while (n && n !== doc.body && n !== doc.documentElement) {
      const cs = getComputedStyle(n);
      if (/(auto|scroll)/.test(cs.overflowY) && n.scrollHeight > n.clientHeight + 4) {
        const er = el.getBoundingClientRect();
        const ar = n.getBoundingClientRect();
        // center it unless it already sits comfortably inside the box (15% margins)
        const m = ar.height * 0.15;
        if (er.top < ar.top + m || er.bottom > ar.bottom - m) {
          // rects are zoomed (rendered) px, scrollTop is unzoomed CSS px — convert
          const k = ar.height / n.clientHeight || 1;
          n.scrollTo({ top: n.scrollTop + ((er.top - ar.top) - (ar.height / 2 - er.height / 2)) / k, behavior: 'smooth' });
          moved = true;
        }
      }
      n = n.parentElement;
    }
    return moved;
  }).catch(() => false);
  if (moved) await sleep(600);
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

// True when the page is on the WordPress site under test (not e.g. Google or
// Bing during an account-connection flow). WP-specific checks only run there.
function onSite(url) {
  return !!baseUrl && sameOrigin(url, baseUrl);
}

async function preflight(page) {
  if (isFixture) return;
  const url = page.url();
  if (!onSite(url)) return;
  if (url.includes('wp-login.php')) {
    console.error('record_scene: session expired (redirected to wp-login.php)');
    process.exit(3);
  }
  if (cfg.dismiss_notices !== false) {
    await page.evaluate(() => {
      document.querySelectorAll('.notice, .update-nag').forEach((el) => el.remove());
    }).catch(() => {});
  }
  // page.content() throws while a navigation is still in flight; the next
  // preflight (after it lands) does the check instead.
  const html = await page.content().catch(() => '');
  const m = html.match(PHP_ERROR);
  if (m) {
    console.error(`record_scene: PHP error rendered on page: ${m[0].slice(0, 160)}`);
    process.exit(6);
  }
}

async function login(page) {
  if (!baseUrl || isFixture) return;
  if (profileDir) {
    // Signed-in profile: never type credentials — just confirm the session.
    const root = baseUrl.replace(/\/wp-admin\/?$/, '').replace(/\/$/, '');
    // Check the session on the scene's own first page when it is a wp-admin
    // URL (saves a load — live dashboards with heavy widgets can be slow).
    const first = (scene.actions || []).find((a) => a.type === 'goto');
    const probe = first && String(first.target).startsWith('/wp-admin') ? first.target : '/wp-admin/';
    await page.goto(`${root}${probe}`, { waitUntil: 'domcontentloaded' });
    const inAdmin = /\/wp-admin\//.test(page.url()) && !page.url().includes('wp-login.php');
    if (!inAdmin) {
      console.error(`record_scene: browser profile is not signed in to ${root} ` +
        `(landed on ${page.url()}). Run scripts/sign_in.mjs and sign in first — tick ` +
        '"Remember Me", or WordPress drops the session when the browser closes.');
      process.exit(3);
    }
    return;
  }
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
  // WordPress asks every administrator to confirm the site's admin email every
  // six months, on a screen that sits between the login form and wp-admin.
  // "Remind me later" postpones it without changing any setting.
  if (/action=confirm_admin_email/.test(page.url())) {
    const later = page.locator('.admin-email__actions-secondary a').first();
    if (await later.count()) {
      console.error('record_scene: postponing the "confirm admin email" screen (Remind me later)');
      await Promise.all([
        page.waitForURL(/\/wp-admin\//, { timeout: 15000 }).catch(() => {}),
        later.click(),
      ]);
    }
  }
  const inAdmin = /\/wp-admin\//.test(page.url()) ||
    (await page.locator('body.wp-admin').count()) > 0;
  if (!inAdmin) {
    console.error(`record_scene: login failed — landed on ${page.url()}. `
      + 'Check credentials and that the user has admin access.');
    process.exit(3);
  }
}

// Selector resolution. A selector may address an element inside an iframe
// (the block editor's canvas is one): "frame=<iframe css> >> <inner css>"
// resolves <inner css> through page.frameLocator(<iframe css>). boundingBox()
// on a frame locator is reported in page coordinates, so the cursor glide,
// highlight ring and coordinate clicks work unchanged.
function locate(page, sel) {
  const m = /^frame=(.+?)\s*>>\s*(.+)$/s.exec(sel || '');
  if (m) {
    const loc = page.frameLocator(m[1].trim()).locator(m[2].trim()).first();
    loc.__frameSel = m[1].trim();
    return loc;
  }
  return page.locator(sel).first();
}

// Bounding box in page pixels. The capture CSS-zooms every document (the top
// page AND the editor canvas iframe, since the init script runs in both), and
// Playwright's boundingBox for a frame locator ignores the parent zoom applied
// to the iframe box — it reported the intro paragraph 500px above where it was
// drawn. Rebuild it: iframe box + inner rect × scale.
async function bbox(page, loc) {
  if (!loc.__frameSel) return loc.boundingBox().catch(() => null);
  const fb = await page.locator(loc.__frameSel).first().boundingBox().catch(() => null);
  const r = await loc.evaluate((el) => {
    const q = el.getBoundingClientRect();
    return { x: q.x, y: q.y, width: q.width, height: q.height };
  }).catch(() => null);
  if (!fb || !r) return null;
  return { x: fb.x + r.x * zoom, y: fb.y + r.y * zoom, width: r.width * zoom, height: r.height * zoom };
}

async function runAction(page, a) {
  const sel = a.selector;
  // `optional: true` actions give up sooner: the screen they belong to may
  // simply not appear (a Google account chooser), and the scene carries on.
  const visTimeout = a.optional ? Number(a.optional_timeout_ms ?? 8000) : actionTimeout;
  dbg(`action ${a.type} ${a.target}`);
  switch (a.type) {
    case 'goto': {
      const root = (baseUrl || '').replace(/\/wp-admin\/?$/, '').replace(/\/$/, '');
      // Absolute URLs (a third-party site in a connection flow) are used as-is.
      const target = isFixture ? baseUrl
        : isAbsoluteUrl(a.target) ? a.target
        : root + (a.target.startsWith('/') ? a.target : '/' + a.target);
      await page.goto(target, { waitUntil: 'domcontentloaded' });
      await preflight(page);
      break;
    }
    case 'click': {
      const loc = locate(page, sel);
      await loc.waitFor({ state: 'visible', timeout: visTimeout });
      dbg('  visible');
      await ensureUnclipped(page, loc);
      await ensureCentered(page, loc);
      await ensureOnScreen(page, loc);
      await settledBox(page, loc);
      dbg('  centered');
      if (a.highlight) await showHighlight(page, loc);
      // Coordinate input, never loc.click(): Playwright's actionability loop
      // re-fires its own instant scrollIntoView on every retry, which fights
      // the cinematic smooth scroll and visibly bounces the page on widgets
      // that never pass the stability check (vue-multiselect). We already
      // scrolled, glided, and verified visibility — click where the cursor is.
      let box2 = await glideCursorTo(page, loc);
      if (box2) box2 = await clickableBox(page, loc, sel);
      dbg('  glided');
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
      dbg('  clicked');
      if (crossDoc) {
        const target = new URL(href, page.url()).toString();
        const before = blockedWrites.length;
        try {
          await page.goto(target, { waitUntil: 'domcontentloaded' });
        } catch (err) {
          // block_writes aborted the navigation (a trash/delete/deactivate link):
          // say so, instead of surfacing a bare network error.
          if (blockedWrites.length > before) {
            throw new Error(`block_writes stopped this click from reaching the site (${blockedWrites[blockedWrites.length - 1]})`);
          }
          throw err;
        }
      } else {
        await page.waitForLoadState('domcontentloaded', { timeout: actionTimeout }).catch(() => {});
      }
      dbg('  load-state done');
      await preflight(page);
      dbg('  preflight done');
      break;
    }
    case 'dblclick': {
      // Double-click — e.g. to select a code token on screen before "copying" it.
      const loc = locate(page, sel);
      await loc.waitFor({ state: 'visible', timeout: visTimeout });
      await ensureUnclipped(page, loc);
      await ensureCentered(page, loc);
      await ensureOnScreen(page, loc);
      await settledBox(page, loc);
      if (a.highlight) await showHighlight(page, loc);
      let box = await glideCursorTo(page, loc);
      if (box) box = await clickableBox(page, loc, sel);
      if (box) {
        await pressEffect(page, box);
        await page.mouse.dblclick(box.x + box.width / 2, box.y + box.height / 2);
      } else {
        await loc.dblclick({ force: true, timeout: 5000 });
      }
      logEvent('click');
      await sleep(300);
      break;
    }
    case 'paste': {
      // Click the field, then insert `text` the way a real paste does: all at
      // once, firing input events (so Vue/React bindings update). Scenes run in
      // separate browsers, so a clipboard can't carry text from one to the next.
      const loc = locate(page, sel);
      await loc.waitFor({ state: 'visible', timeout: visTimeout });
      await ensureUnclipped(page, loc);
      await ensureCentered(page, loc);
      await ensureOnScreen(page, loc);
      let box = await glideCursorTo(page, loc, a.click_at);
      if (box) box = await clickableBox(page, loc, sel);
      if (box) {
        const pt = pointIn(box, a.click_at);
        await pressEffect(page, { x: pt.x, y: pt.y, width: 0, height: 0 });
        await page.mouse.click(pt.x, pt.y);
      } else {
        await loc.click({ force: true, timeout: 5000 });
      }
      logEvent('click');
      await sleep(350);
      if (a.clear) {
        await loc.fill('').catch(() => {});
        await sleep(150);
      }
      // In the element's own document: the field may live inside an iframe.
      await loc.evaluate((el, t) => el.ownerDocument.execCommand('insertText', false, t), a.text || '');
      logEvent('paste', { chars: (a.text || '').length });
      logEvent('type_end');
      await sleep(250);
      break;
    }
    case 'select': {
      // Native <select> popups are drawn by the OS, not the page, so the
      // screencast never shows them. Show the click on the dropdown, then set
      // the option by its visible label — the closed select reads the new value.
      const loc = locate(page, sel);
      await loc.waitFor({ state: 'visible', timeout: visTimeout });
      await ensureUnclipped(page, loc);
      await ensureCentered(page, loc);
      await ensureOnScreen(page, loc);
      await settledBox(page, loc);
      if (a.highlight) await showHighlight(page, loc);
      const box = await glideCursorTo(page, loc);
      if (box) await pressEffect(page, box);
      logEvent('click');
      await sleep(200);
      await loc.selectOption({ label: a.text }, { timeout: actionTimeout });
      logEvent('select', { label: a.text });
      await sleep(250);
      break;
    }
    case 'type': {
      const loc = locate(page, sel);
      await loc.waitFor({ state: 'visible', timeout: visTimeout });
      await ensureUnclipped(page, loc);
      await ensureCentered(page, loc);
      await ensureOnScreen(page, loc);
      let box = await glideCursorTo(page, loc, a.click_at);
      if (box) box = await clickableBox(page, loc, sel);
      if (box) {
        const pt = pointIn(box, a.click_at);
        await pressEffect(page, { x: pt.x, y: pt.y, width: 0, height: 0 });
        await page.mouse.click(pt.x, pt.y);
      } else {
        await loc.click({ force: true, timeout: 5000 });
      }
      logEvent('click');
      await sleep(280); // let the focus/caret land so the click reads on camera
      if (a.clear) {
        // Empty the field before typing (input, textarea, contenteditable).
        // Cmd+A is not honoured by every editor widget; fill('') is.
        await loc.fill('').catch(() => {});
        await sleep(150);
      }
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
      const loc = locate(page, sel);
      await loc.waitFor({ state: 'visible', timeout: visTimeout });
      await ensureUnclipped(page, loc);
      await ensureCentered(page, loc);
      await ensureOnScreen(page, loc);
      await settledBox(page, loc); // a long smooth scroll outlasts the fixed waits
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
      const loc = locate(page, sel);
      await ensureUnclipped(page, loc);
      await loc.evaluate((el) =>
        el.scrollIntoView({ behavior: 'smooth', block: 'center' })).catch(() => {});
      await sleep(600);
      await ensureOnScreen(page, loc);
      await settledBox(page, loc);
      break;
    }
    case 'eval': {
      // Off-camera page JavaScript from the scene author (never from fetched
      // docs): e.g. point a sidebar link straight at a settings tab so the
      // on-camera click lands on the screen the video is about.
      await page.evaluate(a.text);
      await sleep(150);
      break;
    }
    case 'wait': {
      // plain: sleep `text` ms. With a selector: wait up to `text` ms (default
      // 60s) for that element to be visible — how a chained scene waits for
      // the result of the previous scene's action (an AI run, a page load).
      if (sel) {
        // `hidden: true` inverts it: wait for the element to go away (a modal closing).
        await locate(page, sel).waitFor({ state: a.hidden ? 'hidden' : 'visible', timeout: parseInt(a.text || '60000', 10) });
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
//
// Three launch modes:
//   default                      a throwaway browser that logs in with the
//                                env-var credentials.
//   browser_profile              the profile the user signed into by hand
//                                (scripts/sign_in.mjs) is opened only to READ
//                                its session (cookies + storage, in memory,
//                                never written to disk), and the scene records
//                                in the same throwaway browser as the default
//                                mode. Real Chrome under automation was measured
//                                100-500x slower on heavy admin screens.
//   browser_profile +            record inside the profile's own browser — for
//   record_channel: "profile"    flows that need real Chrome (Google account
//                                screens).
// `headless: false` shows the window; some third-party screens serve headless
// browsers a different (legacy) page, e.g. Google's OAuth consent.
const contextOpts = {
  viewport: { width: width * scale, height: height * scale },
  ignoreHTTPSErrors: cfg.ignore_https_errors !== false,
};
const headless = cfg.headless !== false;
const launchOpts = { headless };
if (cfg.browser_channel) launchOpts.channel = cfg.browser_channel;
if (profileDir && !existsSync(profileDir)) {
  console.error(`record_scene: browser_profile ${profileDir} does not exist. Create it and sign in with:\n` +
    `  node scripts/sign_in.mjs --profile ${cfg.browser_profile} --url ${baseUrl || '<site>/wp-admin/'}`);
  process.exit(3);
}
const recordInProfile = !!profileDir && cfg.record_channel === 'profile';
let browser = null; // stays null when recording inside the profile (persistent context)
let context;
if (recordInProfile) {
  context = await chromium.launchPersistentContext(profileDir, {
    ...contextOpts,
    ...profileLaunchOpts(cfg, { headless }),
  });
} else {
  let storageState;
  if (profileDir) {
    const src = await chromium.launchPersistentContext(profileDir, profileLaunchOpts(cfg, { headless: true }));
    storageState = await src.storageState(); // in memory only — never written to disk
    await src.close();
  }
  browser = await chromium.launch(launchOpts);
  context = await browser.newContext(storageState ? { ...contextOpts, storageState } : contextOpts);
}
const closeBrowser = () => (browser ? browser.close() : context.close());
if (profileDir && !cfg.block_writes) {
  console.error('record_scene: NOTE — recording with a signed-in profile and block_writes is off: anything ' +
    'saved, trashed or deactivated on camera reaches the real site. Set "block_writes": true for a read-only recording.');
}
// Keep every init script so it can be re-applied to frames whose document was
// replaced in place: some page builders (Avada) open their preview iframe as
// about:blank and then write the page into it, which discards init scripts —
// rewrites, hides and dismissals would silently never run inside it.
const initScripts = [];
{
  const add = context.addInitScript.bind(context);
  context.addInitScript = (script, arg) => {
    if (typeof script === 'string') initScripts.push(script);
    return add(script, arg);
  };
}
await context.addInitScript(`(() => {
  const mark = () => document.documentElement && document.documentElement.setAttribute('data-wtv-init', '1');
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mark); else mark();
})()`);
async function reapplyInitScripts(page) {
  for (const frame of page.frames()) {
    try {
      const fresh = await frame.evaluate(() => !!document.documentElement &&
        document.readyState !== 'loading' && document.documentElement.getAttribute('data-wtv-init') !== '1');
      if (!fresh) continue;
      for (const sc of initScripts) await frame.evaluate(sc).catch(() => {});
      await frame.evaluate(() => document.documentElement.setAttribute('data-wtv-init', '1'));
      dbg(`re-applied init scripts in ${frame.url().slice(0, 80)}`);
    } catch { /* detached / cross-origin frame */ }
  }
}

// `block_writes`: read-only safety net for real sites. Aborts every write
// request (POST/PUT/PATCH/DELETE) to the site's origin, except URL patterns the
// run allowlists because the screen needs them to *read* data, and every
// trash/delete/deactivate link whatever its method (rules: lib/guards.mjs).
// Armed only after login, so the default mode's own login POST gets through.
const blockedWrites = [];
let writeGuardArmed = false;
if (cfg.block_writes && baseUrl && !isFixture) {
  const blocked = makeWriteBlocker(baseUrl, cfg.allow_write_patterns);
  await context.route('**/*', (route) => {
    const req = route.request();
    const why = writeGuardArmed ? blocked(req.method(), req.url()) : null;
    if (!why) return route.continue();
    let path = req.url();
    try { const u = new URL(path); path = u.pathname + u.search; } catch { /* keep the raw url */ }
    blockedWrites.push(`${req.method()} ${path.slice(0, 90)}`);
    // A blocked page navigation (a classic form Save, a row-action link) is
    // answered with "204 No Content": the browser stays on the page it was on,
    // with everything the viewer typed still showing. Aborting it instead would
    // replace the screen with the browser's error page mid-recording.
    if (req.isNavigationRequest()) return route.fulfill({ status: 204, body: '' });
    return route.abort();
  });
}
if (zoom !== 1) {
  // Top document only: an iframe (the block editor canvas) already scales with
  // its parent, so zooming it too renders its text at zoom² (the "huge font").
  await context.addInitScript(`(() => {
    if (window !== window.top) return;
    const apply = () => { if (document.documentElement) document.documentElement.style.zoom = '${zoom}'; };
    document.addEventListener('DOMContentLoaded', apply);
    apply();
  })()`);
}
// Tutorial cursor: a DOM pointer that glides between action targets with an
// eased CSS transition (Playwright's showActions cursor double-scales under
// zoom, so we draw our own — one code path for every capture scale).
await context.addInitScript(`(() => {
  // Top document only: the cursor is positioned in page coordinates and drawn
  // above every iframe; a copy inside an iframe would just sit there.
  if (window !== window.top) return;
  const mk = () => {
    if (document.getElementById('__wtv_cursor') || !document.body) return;
    const c = document.createElement('div');
    c.id = '__wtv_cursor';
    c.style.cssText = 'position:fixed;left:40%;top:40%;width:28px;height:40px;' +
      'margin-left:-5px;margin-top:-5px;' +  // NSCursor.arrow hotspot (5,5)
      'z-index:2147483647;pointer-events:none;' +
      'transition:left .55s cubic-bezier(.25,.1,.25,1),top .55s cubic-bezier(.25,.1,.25,1);';
    // DOM APIs, not innerHTML: pages with a Trusted Types CSP (some plugin
    // admin apps) reject innerHTML and the cursor would vanish.
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
if (cfg.inject_css || cfg.extra_css) {
  // Capture-time CSS overrides for layout that misbehaves under the CSS-zoom
  // 4K capture (a modal sized in vh that outgrows the viewport, a dropdown
  // that mis-measures), or for a visible ::selection colour (an unfocused
  // recording window draws selections in grey). Applied to every page and
  // origin the recorder opens, never to the site itself. `extra_css` is an
  // alias of `inject_css`; both are applied when both are set.
  const css = JSON.stringify([cfg.inject_css, cfg.extra_css].filter(Boolean).map(String).join('\n'));
  await context.addInitScript(`(() => {
    const add = () => {
      const host = document.head || document.documentElement;
      if (document.getElementById('__wtv_css') || !host) return;
      const st = document.createElement('style');
      st.id = '__wtv_css';
      st.textContent = ${css};
      host.appendChild(st);
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
// Display-only rewrites for demo recordings on a real site: swap visible text
// (post titles, site name, domain, user name) so the video reads as neutral demo
// content without touching the site's data. Only text nodes and descriptive
// attributes change — never hrefs or form values, so links still work and
// nothing rewritten can be saved. MutationObserver callbacks run before the
// next paint, so re-rendered originals never reach a frame.
if ((cfg.display_rewrites || []).length || (cfg.hide_selectors || []).length) {
  const rewrites = JSON.stringify((cfg.display_rewrites || [])
    .filter((r) => r && r.find).map((r) => [String(r.find), String(r.replace ?? '')]));
  const hide = JSON.stringify(cfg.hide_selectors || []);
  await context.addInitScript(`(() => {
    const RW = ${rewrites}; const HIDE = ${hide};
    const ATTRS = ['title', 'alt', 'placeholder', 'aria-label', 'data-original-title'];
    const swap = (s) => { let o = s; for (const [f, r] of RW) if (o.includes(f)) o = o.split(f).join(r); return o; };
    const fixText = (n) => { const v = n.nodeValue; if (v) { const w = swap(v); if (w !== v) n.nodeValue = w; } };
    const fixEl = (el) => { for (const a of ATTRS) { const v = el.getAttribute && el.getAttribute(a);
      if (v) { const w = swap(v); if (w !== v) el.setAttribute(a, w); } } };
    const SKIP = /^(SCRIPT|STYLE|NOSCRIPT|TEXTAREA)$/;
    const walk = (root) => {
      if (!root) return;
      if (root.nodeType === 3) { const p = root.parentNode; if (!p || !SKIP.test(p.nodeName)) fixText(root); return; }
      if (root.nodeType !== 1 && root.nodeType !== 9 && root.nodeType !== 11) return;
      if (root.nodeType === 1) { if (SKIP.test(root.tagName)) return; fixEl(root); }
      const tw = document.createTreeWalker(root, 5 /* SHOW_ELEMENT|SHOW_TEXT */);
      for (let n = tw.nextNode(); n; n = tw.nextNode()) {
        if (n.nodeType === 3) { const p = n.parentNode; if (!p || !SKIP.test(p.nodeName)) fixText(n); }
        else fixEl(n);
      }
    };
    const css = () => { if (!HIDE.length || document.getElementById('__wtv_hide')) return;
      const st = document.createElement('style'); st.id = '__wtv_hide';
      st.textContent = HIDE.join(',') + '{visibility:hidden !important}';
      (document.head || document.documentElement).appendChild(st); };
    const arm = () => {
      css(); if (RW.length) walk(document.body);
      if (!RW.length) return;
      new MutationObserver((ms) => { for (const m of ms) {
        if (m.type === 'characterData') { const p = m.target.parentNode; if (!p || !SKIP.test(p.nodeName)) fixText(m.target); }
        else if (m.type === 'attributes') fixEl(m.target);
        else m.addedNodes.forEach(walk);
      } }).observe(document.documentElement, { childList: true, subtree: true,
        characterData: true, attributes: true, attributeFilter: ATTRS });
    };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', arm); else arm();
  })()`);
}
if ((cfg.redact_selectors || []).length || (cfg.redact_patterns || []).length) {
  const selectors = JSON.stringify(cfg.redact_selectors || []);
  const patterns = JSON.stringify(cfg.redact_patterns || []);
  // Runs on every origin, including third-party sign-in screens in a
  // connection flow. Incremental: one full pass at load, then only the subtrees
  // that changed, batched per animation frame. (Rescanning the whole document
  // on every mutation starved the main thread on big list screens — each
  // scripted action then ran 10+ seconds late.)
  await context.addInitScript(`(() => {
    const SEL = ${selectors}; const PAT = ${patterns}.map((p) => new RegExp(p));
    const blurEl = (el) => { el.style.filter = 'blur(6px)'; };
    // Text-bearing leaves too: account chips on third-party sign-in screens
    // (Google, Bing) put the email in a bare <div>. Leaves only, so a match
    // never blurs a whole container.
    const LEAF = 'div, p, a, li, strong, b';
    const cand = (el) => el.matches('input, td, code, span') || (el.matches(LEAF) && el.children.length === 0);
    const test = (el) => { const v = el.value || el.textContent || '';
      if (v && PAT.some((r) => r.test(v))) blurEl(el); };
    const scan = (root) => {
      if (!root || root.nodeType !== 1 || !root.isConnected) return;
      for (const s of SEL) {
        if (root.matches && root.matches(s)) blurEl(root);
        root.querySelectorAll(s).forEach(blurEl);
      }
      if (PAT.length) {
        if (root.matches && cand(root)) test(root);
        root.querySelectorAll('input, td, code, span, ' + LEAF).forEach((el) => { if (cand(el)) test(el); });
      }
    };
    let pending = new Set(); let queued = false;
    const flush = () => { queued = false; const roots = pending; pending = new Set(); roots.forEach(scan); };
    const arm = () => {
      scan(document.body);
      new MutationObserver((ms) => {
        for (const m of ms) {
          if (m.type === 'characterData') { if (m.target.parentElement) pending.add(m.target.parentElement); }
          else m.addedNodes.forEach((n) => { if (n.nodeType === 1) pending.add(n); else if (n.parentElement) pending.add(n.parentElement); });
        }
        if (pending.size && !queued) { queued = true; requestAnimationFrame(flush); }
      }).observe(document.documentElement, { childList: true, subtree: true, characterData: true });
    };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', arm); else arm();
  })()`);
}
const page = recordInProfile ? (context.pages()[0] || await context.newPage()) : await context.newPage();
// `emulate_focus`: make the page behave as if its window had focus. A headed
// recording window is usually behind other windows, so Chromium draws text
// selections in inactive grey and some :focus styles never appear. Works where
// a site's CSP blocks injected styles (Bing), unlike a ::selection rule.
if (cfg.emulate_focus) {
  try {
    const cdp = await context.newCDPSession(page);
    await cdp.send('Emulation.setFocusEmulationEnabled', { enabled: true });
  } catch (e) { console.error(`record_scene: focus emulation unavailable (${e.message})`); }
}
// Real sites can be far slower than a local install: allow longer page loads.
page.setDefaultNavigationTimeout(Number(cfg.navigation_timeout_ms ?? (profileDir ? 90000 : 30000)));
// Surface renderer crashes and unexpected closes explicitly — otherwise the
// only symptom is a late "Target page ... has been closed" from screencast.stop.
page.on('crash', () => console.error('record_scene: PAGE CRASHED (renderer died) — heavy page under capture?'));
page.on('close', () => { if (capturing) console.error(`record_scene: page closed during capture at +${((Date.now() - captureT0) / 1000).toFixed(1)}s`); });
const launchedAt = Date.now();
(browser || context).on(browser ? 'disconnected' : 'close', () => {
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
// A chain runs only its FIRST scene's setup_cmd. A later scene that declares a
// different one expects a state this chain will never seed — say so up front,
// before any recording time is spent.
const unseeded = scenes.slice(1).filter((s) => s.setup_cmd && s.setup_cmd !== scenes[0].setup_cmd);
if (unseeded.length) {
  console.error(`record_scene: WARNING — scene(s) ${unseeded.map((s) => s.id).join(', ')} declare a setup_cmd `
    + `that differs from scene ${scenes[0].id}'s. A chain runs only its first scene's setup_cmd, so that state `
    + `will NOT be seeded. Start a new chain at scene ${unseeded[0].id} (--scene-ids ${unseeded[0].id},…).`);
}
if (scenes[0].setup_cmd) {
  console.log(`record_scene: setup_cmd → ${scenes[0].setup_cmd}`);
  execSync(scenes[0].setup_cmd, { stdio: 'inherit', shell: '/bin/zsh' });
}

await login(page);
writeGuardArmed = true; // from here on nothing can be saved when block_writes is set

// Wait until the page's main thread is responsive before filming: heavy admin
// screens (and some plugins' scripts in real Chrome) can freeze the page for
// many seconds after load, and any scripted action sent during a freeze lands
// that much later than its narration cue. Idle = a requestAnimationFrame round
// trip under 250ms, three times in a row (capped by settle_max_ms). Each round
// trip is itself capped: a window that is not being painted never fires
// requestAnimationFrame, and that must not hang the recorder.
async function waitForIdle(page, maxMs) {
  const t0 = Date.now(); let quiet = 0;
  while (Date.now() - t0 < maxMs) {
    const s = Date.now();
    const fired = await Promise.race([
      page.evaluate(() => new Promise((r) => requestAnimationFrame(() => r(true)))).catch(() => false),
      sleep(2000).then(() => false),
    ]);
    quiet = fired && Date.now() - s < 250 ? quiet + 1 : 0;
    if (quiet >= 3) return Date.now() - t0;
    await sleep(200);
  }
  return -1;
}

// A failed action leaves more than a screenshot: where the page was and which
// buttons were on it — essential for one-shot flows on third-party screens.
async function describeScreen(page) {
  let where = '';
  try { const u = new URL(page.url()); where = u.host + u.pathname; } catch { /* about:blank */ }
  const buttons = await page.locator('button:visible, [role=button]:visible, input[type=submit]:visible')
    .allInnerTexts().catch(() => []);
  return { where, buttons: buttons.map((t) => t.trim().replace(/\s+/g, ' ')).filter(Boolean).slice(0, 15) };
}

// `optional: true`: the action's element never appeared → skip it and carry on.
const skipOptional = (a, err) => {
  if (!(a.optional && err?.name === 'TimeoutError')) return false;
  console.log(`record_scene: optional action "${a.target}" skipped (not shown)`);
  return true;
};

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
  try {
    await runAction(page, a);
  } catch (err) {
    if (skipOptional(a, err)) continue;
    err.message = `setup ${a.type} "${a.target}" (${a.selector || 'no selector'}): ${err.message}`;
    throw err;
  }
}
await reapplyInitScripts(page);
await sleep(400); // settle before capture
const settled = await waitForIdle(page, Number(cfg.settle_max_ms ?? 60000));
if (settled < 0) console.error('record_scene: page never went idle before capture (continuing)');
else if (settled > 3000) console.log(`record_scene: waited ${(settled / 1000).toFixed(1)}s for the page to go idle`);

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
  try {
    await runAction(page, a);
    await reapplyInitScripts(page);
  } catch (err) {
    if (skipOptional(a, err)) continue;
    err.message = `${a.type} "${a.target}" (${a.selector || 'no selector'}): ${err.message}`;
    throw err;
  }
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
    const b = await bbox(page, locate(page, scene.focus_selector));
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
if (cfg.block_writes && !isFixture) {
  console.log(`record_scene: block_writes — ${blockedWrites.length} write request(s) aborted in scene ${sceneId}` +
    (blockedWrites.length ? `: ${[...new Set(blockedWrites)].slice(0, 6).join('; ')}` : ''));
  blockedWrites.length = 0;
}
} catch (err) {
  // Evidence before exit: what the page looked like when the action failed
  // (redaction and display rewrites still apply to the screenshot).
  const shot = join(runDir, 'clips', `${sceneId}.error.png`);
  console.error(`record_scene: scene ${sceneId} FAILED: ${String(err.message || err).split('\n')[0].slice(0, 200)}`);
  try { await page.screenshot({ path: shot }); console.error(`record_scene: page state saved to ${shot}`); } catch { /* page gone */ }
  try {
    const { where, buttons } = await describeScreen(page);
    console.error(`record_scene: failed on ${where || '(unknown page)'}; visible buttons: ${JSON.stringify(buttons)}`);
  } catch { /* page gone */ }
  if (capturing) { try { await page.screencast.stop(); } catch { /* ignore */ } capturing = false; }
  try { await closeBrowser(); } catch { /* ignore */ }
  process.exit(1);
}
} // end per-scene loop

await closeBrowser();
