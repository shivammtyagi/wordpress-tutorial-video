// Request guards for recording against a real site. Pure functions (no
// Playwright), so tests/test_guards.mjs can exercise every rule directly.

// WordPress performs destructive admin actions through plain GET links
// (Trash, Delete Permanently, Restore, Deactivate, Spam…), so a write-block
// on HTTP methods alone is not enough: these URLs are blocked on any method.
export const DESTRUCTIVE_GET =
  /[?&](action|action2)=(trash|untrash|delete|delete-selected|deactivate|deactivate-selected|spam|unspam|approve|unapprove|remove|bulk-delete)(&|#|$)/i;

export function isAbsoluteUrl(s) {
  return /^https?:\/\//i.test(String(s || ''));
}

// True when `url` is on the same origin as the site under test.
export function sameOrigin(url, baseUrl) {
  try { return new URL(url).origin === new URL(baseUrl).origin; } catch { return false; }
}

// `block_writes`: decide whether a request may leave the browser.
// Returns null (let it through) or the reason it is blocked:
//   'write'           — POST/PUT/PATCH/DELETE to the site's own origin that no
//                       `allow_write_patterns` regex matches
//   'destructive-get' — a trash/delete/deactivate/… link on the site, any method;
//                       the allowlist never overrides this
// Requests to other origins are never blocked (a Google or Bing screen in a
// connection flow has to work), and neither are plain reads.
export function makeWriteBlocker(baseUrl, allowPatterns = []) {
  const allow = (allowPatterns || []).map((p) => new RegExp(p));
  return (method, url) => {
    if (!sameOrigin(url, baseUrl)) return null;
    if (DESTRUCTIVE_GET.test(url)) return 'destructive-get';
    const m = String(method || 'GET').toUpperCase();
    if (m === 'GET' || m === 'HEAD' || m === 'OPTIONS') return null;
    return allow.some((re) => re.test(url)) ? null : 'write';
  };
}
