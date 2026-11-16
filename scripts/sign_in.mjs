// sign_in.mjs — open a persistent browser profile in a visible window so the
// user can sign in (WordPress, Google, Bing, …) themselves. Recording later
// reuses these sessions via config `browser_profile`; no credentials ever pass
// through the skill.
//
//   node scripts/sign_in.mjs --profile ~/.wtv-profiles/live \
//        --url https://example.com/wp-admin/ [--url https://search.google.com/search-console] …
//        [--channel chrome]
//
// Sign in on each tab, then quit the browser. The profile keeps the sessions.
// On WordPress, tick "Remember Me": otherwise its login cookies are session-only
// and are discarded when the window closes.
import { chromium } from 'playwright';
import { mkdirSync, chmodSync } from 'node:fs';
import { resolveProfileDir, profileLaunchOpts } from './lib/profile.mjs';

const argv = process.argv.slice(2);
const val = (name) => { const i = argv.indexOf(`--${name}`); return i !== -1 ? argv[i + 1] : undefined; };
const urls = argv.flatMap((a, i) => (a === '--url' && argv[i + 1] ? [argv[i + 1]] : []));
const profileDir = resolveProfileDir(val('profile'), process.cwd());
if (!profileDir || !urls.length) {
  console.error('usage: sign_in.mjs --profile <dir> --url <url> [--url <url> …] [--channel chrome]');
  process.exit(2);
}
mkdirSync(profileDir, { recursive: true });
chmodSync(profileDir, 0o700); // live session cookies: owner-only

const context = await chromium.launchPersistentContext(profileDir, {
  ...profileLaunchOpts({ profile_channel: val('channel') }, { headless: false }),
  viewport: null, // use the real window size
});
const first = context.pages()[0] || await context.newPage();
await first.goto(urls[0]).catch(() => {});
for (const u of urls.slice(1)) {
  const p = await context.newPage();
  await p.goto(u).catch(() => {});
}
console.log(`sign_in: opened ${urls.length} tab(s) in profile ${profileDir}`);
console.log('sign_in: sign in on each tab, then QUIT this browser when done (⌘Q on a Mac —\n'
  + '         closing the tabs or window leaves it running and the profile locked).');
console.log('sign_in: on the WordPress login, tick "Remember Me" — without it WordPress uses session\n'
  + '         cookies that are deleted when this window closes, and recording will find you signed out.');
await new Promise((resolve) => context.on('close', resolve));
console.log('sign_in: window closed — sessions saved to the profile.');
