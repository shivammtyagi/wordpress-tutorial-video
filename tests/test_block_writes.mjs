// Node test: `block_writes` against a mock WordPress served over HTTP.
// Proves, from the SERVER's side, which requests the recorder let through:
// the login POST and reads do, saves and trash links never arrive.
// Run: node tests/test_block_writes.mjs
import { execFile } from 'node:child_process';
import { createServer } from 'node:http';
import { mkdtempSync, writeFileSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const seen = []; // "METHOD /path?query" for every request the server received

const ADMIN = `<!doctype html><html><head><meta charset="utf-8"><title>Mock wp-admin</title></head>
<body class="wp-admin"><h1>Posts</h1>
<button id="save-btn">Save Changes</button> <span id="save-result">idle</span>
<button id="load-btn">Load column</button> <span id="load-result">idle</span>
<p><a id="row-action" href="/wp-admin/post.php?post=7&action=trash&_wpnonce=abc">Bin</a></p>
<form method="post" action="/wp-admin/options.php"><input id="tagline" name="blogdescription" value="Original">
<input id="form-save" type="submit" value="Save Changes"></form>
<script>
  const call = async (url, out) => {
    try { const r = await fetch(url, { method: 'POST', body: 'x=1' }); out.textContent = r.ok ? 'done' : 'http-' + r.status; }
    catch (e) { out.textContent = 'blocked'; }
  };
  document.getElementById('save-btn').addEventListener('click', () => call('/wp-admin/admin-ajax.php', document.getElementById('save-result')));
  document.getElementById('load-btn').addEventListener('click', () => call('/wp-json/demo/v1/load-column/', document.getElementById('load-result')));
</script></body></html>`;
const LOGIN = `<!doctype html><html><body><form method="post" action="/wp-login.php">
<input id="user_login" name="log"><input id="user_pass" name="pwd" type="password">
<input id="wp-submit" type="submit" value="Log In"></form></body></html>`;

const server = createServer((req, res) => {
  seen.push(`${req.method} ${req.url}`);
  req.resume();
  const path = req.url.split('?')[0];
  if (path === '/wp-login.php' && req.method === 'POST') {
    res.writeHead(302, { Location: '/wp-admin/', 'Set-Cookie': 'wordpress_logged_in=1; Path=/' }); return res.end();
  }
  if (path === '/wp-login.php') { res.writeHead(200, { 'Content-Type': 'text/html' }); return res.end(LOGIN); }
  if (path.startsWith('/wp-admin/post.php')) { res.writeHead(200, { 'Content-Type': 'text/html' }); return res.end('<body class="wp-admin">moved to bin</body>'); }
  if (path.startsWith('/wp-admin/') && req.method === 'GET') { res.writeHead(200, { 'Content-Type': 'text/html' }); return res.end(ADMIN); }
  res.writeHead(200, { 'Content-Type': 'application/json' }); res.end('{"ok":true}');
});
await new Promise((r) => server.listen(0, '127.0.0.1', r));
const base = `http://127.0.0.1:${server.address().port}`;
const env = { ...process.env, WP_ADMIN_USER: 'tester', WP_ADMIN_PASS: 'not-a-real-password' };

// async on purpose: the mock server runs in this process and must keep serving
// while the recorder child runs (execFileSync would block the event loop).
function record(scene, cfg) {
  const run = mkdtempSync(join(tmpdir(), 'blockwrites-'));
  mkdirSync(join(run, 'audio'), { recursive: true });
  writeFileSync(join(run, 'script.discovered.json'), JSON.stringify({
    title: 'T', resolution: '640x360', fps: 24, voice: 'af_heart', scenes: [scene] }));
  writeFileSync(join(run, 'config.json'), JSON.stringify({ capture_scale: 1, action_timeout_ms: 3000, ...cfg }));
  writeFileSync(join(run, 'audio', 'durations.json'), JSON.stringify({ '01': 0.5 }));
  seen.length = 0;
  return new Promise((resolve) => execFile('node', [join(ROOT, 'scripts', 'record_scene.mjs'),
    '--run-dir', run, '--scene-id', '01', '--base-url', base], { env, timeout: 120000 },
  (e, stdout, stderr) => resolve({ status: e ? (e.code ?? 1) : 0, out: String(stdout), err: String(stderr) })));
}
const fail = (msg, r) => { console.error(`FAIL: ${msg}\n${r ? r.out + r.err : ''}\nserver saw: ${seen.join(' | ')}`); server.close(); process.exit(1); };
const check = (name, expr) => ({ type: 'eval', target: `assert: ${name}`,
  text: `(() => { if (!(${expr})) throw new Error('assert failed: ${name}'); })()` });
const text = (id) => `document.getElementById('${id}').textContent`;
const saveScene = (expectSave) => ({
  id: '01', narration: 'Save.', intent: 'Writes',
  actions: [
    { type: 'click', target: 'Save Changes', selector: '#save-btn' },
    { type: 'click', target: 'Load column', selector: '#load-btn' },
    { type: 'wait', target: 'let the requests finish', text: '600' },
    check('save result', `${text('save-result')} === '${expectSave}'`),
    check('allowlisted read-over-POST still works', `${text('load-result')} === 'done'`),
  ],
  focus_selector: 'h1', hold_after_ms: 100, verify: { expect_on_screen: 'x' },
});

// 1. control: without block_writes the save reaches the server
let r = await record(saveScene('done'), {});
if (r.status !== 0 || !seen.includes('POST /wp-admin/admin-ajax.php')) fail('control run should save', r);

// 2. block_writes: login still works, the save never arrives, the allowlisted POST does
r = await record(saveScene('blocked'), { block_writes: true, allow_write_patterns: ['/wp-json/demo/v1/load-column/'] });
if (r.status !== 0) fail('block_writes run should record cleanly', r);
if (!seen.includes('POST /wp-login.php')) fail('the login POST must get through before the guard is armed', r);
if (seen.includes('POST /wp-admin/admin-ajax.php')) fail('the save reached the server', r);
if (!seen.includes('POST /wp-json/demo/v1/load-column/')) fail('the allowlisted POST was blocked', r);
if (!/block_writes — 1 write request\(s\) aborted in scene 01: POST \/wp-admin\/admin-ajax\.php/.test(r.out)) fail('blocked writes not reported', r);

// 2b. a classic settings form (a full-page POST): nothing arrives, and the
//     recording stays on the form with the typed value still showing
const formScene = {
  id: '01', narration: 'Save the form.', intent: 'Classic form',
  actions: [
    { type: 'paste', target: 'Tagline field', selector: '#tagline', text: 'Typed on camera', clear: true },
    { type: 'click', target: 'Save Changes', selector: '#form-save' },
    { type: 'wait', target: 'let the navigation settle', text: '600' },
    check('still on the settings screen with the typed value',
      `location.pathname === '/wp-admin/' && document.getElementById('tagline').value === 'Typed on camera'`),
  ],
  focus_selector: 'h1', hold_after_ms: 100, verify: { expect_on_screen: 'x' },
};
r = await record(formScene, { block_writes: true });
if (r.status !== 0) fail('a blocked form save should not break the recording', r);
if (seen.includes('POST /wp-admin/options.php')) fail('the form save reached the server', r);
r = await record({ ...formScene, actions: formScene.actions.slice(0, 3) }, {});
if (!seen.includes('POST /wp-admin/options.php')) fail('control form save should reach the server', r);

// 3. a row-action link that trashes over GET: never reaches the server, even
//    with the destructive-action guard switched off and the allowlist wide open
const trashScene = {
  id: '01', narration: 'Oops.', intent: 'Mis-click',
  actions: [{ type: 'click', target: 'row action link', selector: '#row-action' }],
  focus_selector: 'h1', hold_after_ms: 100, verify: { expect_on_screen: 'x' },
};
r = await record(trashScene, { block_writes: true, allow_destructive: true, allow_write_patterns: ['.*'] });
if (seen.some((q) => q.includes('action=trash'))) fail('the trash link reached the server', r);
if (r.status !== 1 || !/block_writes stopped this click/.test(r.err)) fail('a blocked trash click should fail the scene and say why', r);

// 4. control: without block_writes the same click does reach the server
r = await record(trashScene, { allow_destructive: true });
if (!seen.some((q) => q.includes('action=trash'))) fail('control trash click should reach the server', r);

server.close();
console.log('PASS: block_writes — login allowed, saves and trash links never reach the site, allowlist honoured');
