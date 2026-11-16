// Node test: record_scene.mjs against the static fixture page (no WordPress).
// Run: node tests/test_record_scene.mjs
import { execFileSync } from 'node:child_process';
import { mkdtempSync, writeFileSync, existsSync, statSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, '..');
const fixtureUrl = pathToFileURL(join(__dirname, 'fixtures', 'page.html')).href;

const runDir = mkdtempSync(join(tmpdir(), 'recordtest-'));
mkdirSync(join(runDir, 'audio'), { recursive: true });

const script = {
  title: 'T', resolution: '640x360', fps: 24, voice: 'af_heart',
  scenes: [{
    id: '01', narration: 'Open the menu and enable the sitemap.', intent: 'Enable sitemap',
    actions: [
      { type: 'click', target: 'Sitemaps link', selector: '#sitemaps-link', highlight: true },
      { type: 'click', target: 'Enable toggle', selector: '#enable-toggle', highlight: true },
    ],
    focus_selector: '#panel', hold_after_ms: 200, verify: { expect_on_screen: 'sitemap settings' },
  }],
};
writeFileSync(join(runDir, 'script.discovered.json'), JSON.stringify(script));
writeFileSync(join(runDir, 'audio', 'durations.json'), JSON.stringify({ '01': 1.5 }));

execFileSync('node', [
  join(ROOT, 'scripts', 'record_scene.mjs'),
  '--run-dir', runDir, '--scene-id', '01', '--base-url', fixtureUrl,
], { stdio: 'inherit' });

const clip = join(runDir, 'clips', '01.raw.webm');
const focus = join(runDir, 'clips', '01.focus.json');
if (!existsSync(clip) || statSync(clip).size === 0) {
  console.error('FAIL: clip not produced'); process.exit(1);
}
if (!existsSync(focus)) { console.error('FAIL: focus sidecar missing'); process.exit(1); }
console.log(`PASS: recorded ${statSync(clip).size} bytes + focus sidecar`);

// ---- scenario 2: setup/recorded phases + narration cue + focus scale ----------
import { readFileSync } from 'node:fs';

const run2 = mkdtempSync(join(tmpdir(), 'recordtest2-'));
mkdirSync(join(run2, 'audio'), { recursive: true });
mkdirSync(join(run2, 'verify', 'scenes'), { recursive: true });

writeFileSync(join(run2, 'script.discovered.json'), JSON.stringify({
  title: 'T', resolution: '640x360', fps: 24, voice: 'af_heart',
  scenes: [{
    id: '01', narration: 'Click the enable button now.', intent: 'Phase test',
    actions: [
      { type: 'click', target: 'Sitemaps link', selector: '#sitemaps-link',
        highlight: false, phase: 'setup' },
      { type: 'click', target: 'Enable toggle', selector: '#enable-toggle',
        highlight: true, cue: 'button' },
    ],
    focus_selector: '#panel', hold_after_ms: 200,
    verify: { expect_on_screen: 'sitemap settings' },
  }],
}));
writeFileSync(join(run2, 'config.json'), JSON.stringify({
  capture_scale: 1, action_timeout_ms: 3000,
}));
writeFileSync(join(run2, 'verify', 'scenes', '01.json'), JSON.stringify({
  words: [
    { word: 'Click', start: 0.0, end: 0.2 }, { word: 'the', start: 0.2, end: 0.3 },
    { word: 'enable', start: 0.3, end: 0.5 }, { word: 'button', start: 0.5, end: 0.8 },
    { word: 'now.', start: 0.8, end: 1.0 }],
  wer: 0, ok: true,
}));
writeFileSync(join(run2, 'audio', 'durations.json'), JSON.stringify({ '01': 1.2 }));

execFileSync('node', [
  join(ROOT, 'scripts', 'record_scene.mjs'),
  '--run-dir', run2, '--scene-id', '01', '--base-url', fixtureUrl,
], { stdio: 'inherit' });

const clip2 = join(run2, 'clips', '01.raw.webm');
if (!existsSync(clip2) || statSync(clip2).size === 0) {
  console.error('FAIL: phase/cue clip not produced'); process.exit(1);
}
const focus2 = JSON.parse(readFileSync(join(run2, 'clips', '01.focus.json'), 'utf8'));
if (focus2.scale !== 1 || focus2.viewport.width !== 640) {
  console.error('FAIL: focus sidecar missing scale/viewport'); process.exit(1);
}
console.log('PASS: phase/cue scenario recorded with focus scale sidecar');

// ---- scenario 3: destructive guard blocks risky recorded clicks ---------------
const run3 = mkdtempSync(join(tmpdir(), 'recordtest3-'));
mkdirSync(join(run3, 'audio'), { recursive: true });
writeFileSync(join(run3, 'script.discovered.json'), JSON.stringify({
  title: 'T', resolution: '640x360', fps: 24, voice: 'af_heart',
  scenes: [{
    id: '01', narration: 'Delete everything.', intent: 'Danger',
    actions: [
      { type: 'click', target: 'Delete all posts button', selector: '#enable-toggle',
        highlight: false },
    ],
    focus_selector: '#panel', hold_after_ms: 100,
    verify: { expect_on_screen: 'x' },
  }],
}));
writeFileSync(join(run3, 'audio', 'durations.json'), JSON.stringify({ '01': 1.0 }));

let guardCode = 0;
try {
  execFileSync('node', [
    join(ROOT, 'scripts', 'record_scene.mjs'),
    '--run-dir', run3, '--scene-id', '01', '--base-url', fixtureUrl,
  ], { stdio: 'pipe' });
} catch (e) {
  guardCode = e.status;
}
if (guardCode !== 5) {
  console.error(`FAIL: destructive guard expected exit 5, got ${guardCode}`); process.exit(1);
}
console.log('PASS: destructive guard blocks risky scene (exit 5)');

// ---- scenario 4: select / paste / dblclick / optional + rewrites, hides, redaction --
// Page state is asserted in-band: an `eval` action throws when the DOM is not
// what the earlier actions should have produced, which fails the recording.
const run4 = mkdtempSync(join(tmpdir(), 'recordtest4-'));
mkdirSync(join(run4, 'audio'), { recursive: true });
const check = (name, expr) => ({
  type: 'eval', target: `assert: ${name}`,
  text: `(async () => { const ok = await (${expr}); if (!ok) throw new Error('assert failed: ${name}'); })()`,
});
writeFileSync(join(run4, 'script.discovered.json'), JSON.stringify({
  title: 'T', resolution: '640x360', fps: 24, voice: 'af_heart',
  scenes: [{
    id: '01', narration: 'Choose high, paste the code, and select the shortcode.', intent: 'New actions',
    actions: [
      { type: 'select', target: 'Priority dropdown', selector: '#priority', text: 'High' },
      { type: 'paste', target: 'Code field', selector: '#code-field', text: 'abc123' },
      { type: 'dblclick', target: 'the shortcode', selector: '#shortcode', highlight: true },
      { type: 'click', target: 'Account chooser (not shown here)', selector: '#no-such-screen',
        optional: true, optional_timeout_ms: 400 },
      check('select picked the option by its label', `document.querySelector('#priority').value === 'high'`),
      check('paste inserted the text and fired one input event',
        `document.querySelector('#code-field').value === 'abc123' && document.querySelector('#code-field').dataset.inputs === '1'`),
      check('dblclick reached the element', `document.querySelector('#shortcode').dataset.dbl === '1'`),
      check('display rewrite changed the visible text and descriptive attributes',
        `document.querySelector('#owner').textContent === 'Demo User' && document.querySelector('#owner').title === "Demo User's profile"` +
        ` && document.querySelector('#avatar').alt === 'Demo User' && document.querySelector('#code-field').placeholder === 'Paste the Demo User code'`),
      check('display rewrite left the link target and form values alone',
        `document.querySelector('#owner').getAttribute('href') === 'https://example-garden.com/author/jane'` +
        ` && document.querySelector('#notes').value === 'Jane Smith wrote this draft'`),
      check('hide_selectors hid the avatar', `getComputedStyle(document.querySelector('#avatar')).visibility === 'hidden'`),
      check('redaction blurred the leaf holding the email, not its container',
        `/blur/.test(document.querySelector('#account-email').style.filter) && !document.querySelector('#account-chip').style.filter` +
        ` && !document.querySelector('#account-plan').style.filter`),
      check('redaction and rewrites catch content added later',
        `(async () => { const d = document.createElement('div'); d.id = 'late';` +
        ` d.innerHTML = '<div id="late-mail">bob@example-garden.com</div><div id="late-name">Jane Smith</div>';` +
        ` document.body.appendChild(d); await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));` +
        ` return /blur/.test(document.querySelector('#late-mail').style.filter) && document.querySelector('#late-name').textContent === 'Demo User'; })()`),
      check('exactly one tutorial cursor', `document.querySelectorAll('#__wtv_cursor').length === 1`),
      check('extra_css applied', `getComputedStyle(document.querySelector('#bar')).outlineStyle === 'dashed'`),
    ],
    focus_selector: '#widgets', hold_after_ms: 100, verify: { expect_on_screen: 'widgets' },
  }],
}));
writeFileSync(join(run4, 'config.json'), JSON.stringify({
  capture_scale: 1, action_timeout_ms: 3000, emulate_focus: true, settle_max_ms: 5000,
  display_rewrites: [{ find: 'Jane Smith', replace: 'Demo User' }],
  hide_selectors: ['#avatar'],
  redact_patterns: ['[\\w.]+@example-garden\\.com'],
  extra_css: '#bar{outline:1px dashed red}',
}));
writeFileSync(join(run4, 'audio', 'durations.json'), JSON.stringify({ '01': 1.0 }));
const out4 = execFileSync('node', [
  join(ROOT, 'scripts', 'record_scene.mjs'),
  '--run-dir', run4, '--scene-id', '01', '--base-url', fixtureUrl,
], { stdio: ['ignore', 'pipe', 'inherit'] }).toString();
process.stdout.write(out4);
if (!/optional action "Account chooser \(not shown here\)" skipped/.test(out4)) {
  console.error('FAIL: the optional action was not reported as skipped'); process.exit(1);
}
const ev4 = JSON.parse(readFileSync(join(run4, 'clips', '01.events.json'), 'utf8')).events.map((e) => e.kind);
for (const kind of ['select', 'paste', 'type_end', 'click']) {
  if (!ev4.includes(kind)) { console.error(`FAIL: no '${kind}' event logged (${ev4.join(',')})`); process.exit(1); }
}
console.log('PASS: select/paste/dblclick/optional + rewrites, hides, redaction, extra_css');

// ---- scenario 5: a required action that never appears still fails, with evidence ----
const run5 = mkdtempSync(join(tmpdir(), 'recordtest5-'));
mkdirSync(join(run5, 'audio'), { recursive: true });
writeFileSync(join(run5, 'script.discovered.json'), JSON.stringify({
  title: 'T', resolution: '640x360', fps: 24, voice: 'af_heart',
  scenes: [{
    id: '01', narration: 'Click a button that is not there.', intent: 'Failure evidence',
    actions: [{ type: 'click', target: 'Missing button', selector: '#no-such-button' }],
    focus_selector: '#panel', hold_after_ms: 100, verify: { expect_on_screen: 'x' },
  }],
}));
writeFileSync(join(run5, 'config.json'), JSON.stringify({ capture_scale: 1, action_timeout_ms: 800 }));
writeFileSync(join(run5, 'audio', 'durations.json'), JSON.stringify({ '01': 0.5 }));
let fail5 = { status: 0, stderr: '' };
try {
  execFileSync('node', [
    join(ROOT, 'scripts', 'record_scene.mjs'),
    '--run-dir', run5, '--scene-id', '01', '--base-url', fixtureUrl,
  ], { stdio: 'pipe' });
} catch (e) { fail5 = { status: e.status, stderr: String(e.stderr) }; }
if (fail5.status !== 1 || !existsSync(join(run5, 'clips', '01.error.png'))
    || !/visible buttons: \[.*Enable Sitemap|visible buttons: \[/.test(fail5.stderr)) {
  console.error(`FAIL: expected exit 1 with a screenshot and a button list, got ${fail5.status}\n${fail5.stderr}`);
  process.exit(1);
}
console.log('PASS: a missing required element fails the scene and leaves evidence');

