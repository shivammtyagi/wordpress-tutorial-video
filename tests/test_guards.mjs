// Node test: request guards (block_writes rules) and profile path handling.
// Pure functions, no browser. Run: node tests/test_guards.mjs
import assert from 'node:assert/strict';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { makeWriteBlocker, isAbsoluteUrl, sameOrigin, DESTRUCTIVE_GET } from '../scripts/lib/guards.mjs';
import { resolveProfileDir, profileLaunchOpts } from '../scripts/lib/profile.mjs';

const site = 'https://example.com/wp-admin/';
const blocked = makeWriteBlocker(site, ['/wp-json/aioseo/v1/posts-list/load-details-column']);

// plain reads go through
assert.equal(blocked('GET', 'https://example.com/wp-admin/edit.php'), null);
assert.equal(blocked('HEAD', 'https://example.com/wp-admin/'), null);

// every write method to the site is blocked
for (const m of ['POST', 'PUT', 'PATCH', 'DELETE', 'post']) {
  assert.equal(blocked(m, 'https://example.com/wp-admin/admin-ajax.php'), 'write', m);
}
assert.equal(blocked('POST', 'https://example.com/wp-json/wp/v2/posts/12'), 'write');

// an allowlisted read-over-POST goes through; nothing else does
assert.equal(blocked('POST', 'https://example.com/wp-json/aioseo/v1/posts-list/load-details-column/'), null);

// destructive links are blocked on GET too, and the allowlist never overrides them
const trash = 'https://example.com/wp-admin/post.php?post=12&action=trash&_wpnonce=abc';
assert.equal(blocked('GET', trash), 'destructive-get');
assert.equal(makeWriteBlocker(site, ['.*'])('GET', trash), 'destructive-get');
for (const q of ['action=delete&post=3', 'action=deactivate&plugin=x', 'action2=spam', 'action=untrash',
  'action=delete-selected', 'action=bulk-delete']) {
  assert.equal(blocked('GET', `https://example.com/wp-admin/x.php?${q}`), 'destructive-get', q);
}
assert.ok(DESTRUCTIVE_GET.test('https://example.com/wp-admin/plugins.php?action=deactivate'));

// look-alike parameters and harmless actions are not blocked
for (const q of ['action=edit&post=3', 'transaction=delete', 'action=deleted_notice', 'page=trash-settings']) {
  assert.equal(blocked('GET', `https://example.com/wp-admin/x.php?${q}`), null, q);
}

// other origins are never touched (a Google/Bing screen in a connection flow),
// including a host that merely starts with the site's name
assert.equal(blocked('POST', 'https://accounts.google.com/signin'), null);
assert.equal(blocked('POST', 'https://example.com.evil.test/wp-admin/admin-ajax.php'), null);
assert.equal(blocked('GET', 'https://other.test/x.php?action=trash'), null);

// origin + URL helpers
assert.equal(sameOrigin('https://example.com/a', 'https://example.com/wp-admin/'), true);
assert.equal(sameOrigin('http://example.com/a', 'https://example.com/'), false);
assert.equal(sameOrigin('not a url', 'https://example.com/'), false);
assert.equal(isAbsoluteUrl('https://search.google.com/search-console'), true);
assert.equal(isAbsoluteUrl('/wp-admin/admin.php?page=x'), false);

// profile paths
assert.equal(resolveProfileDir(undefined, '/run'), null);
assert.equal(resolveProfileDir('~/.wtv-profiles/live', '/run'), join(homedir(), '.wtv-profiles/live'));
assert.equal(resolveProfileDir('/abs/profile', '/run'), '/abs/profile');
assert.equal(resolveProfileDir('profile', '/run'), '/run/profile');

// the profile opens in real Chrome unless `profile_channel` says otherwise;
// `browser_channel` (the recording browser) must not leak into it
assert.equal(profileLaunchOpts({}).channel, 'chrome');
assert.equal(profileLaunchOpts({ browser_channel: 'chromium' }).channel, 'chrome');
assert.equal(profileLaunchOpts({ profile_channel: 'msedge' }, { headless: false }).channel, 'msedge');
assert.equal(profileLaunchOpts({}, { headless: false }).headless, false);

console.log('PASS: request guards and profile helpers');
