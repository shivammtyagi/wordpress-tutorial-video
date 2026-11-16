// Persistent signed-in browser profiles, shared by record_scene.mjs and
// sign_in.mjs so both open the profile identically.
//
// A profile is used when a run's config.json sets `browser_profile`. The user
// signs in (WordPress, Google, Bing, …) themselves in a visible window via
// sign_in.mjs; recording then reuses those sessions. Credentials never pass
// through the skill. Profiles hold live session cookies — keep them private.
import { homedir } from 'node:os';
import { isAbsolute, join, resolve } from 'node:path';

// Resolve `browser_profile`: "~/…" expands to home, relative paths resolve
// against the run directory, absolute paths are used as-is.
export function resolveProfileDir(value, runDir) {
  if (!value || typeof value !== 'string') return null;
  if (value === '~') return homedir();
  if (value.startsWith('~/')) return join(homedir(), value.slice(2));
  return isAbsolute(value) ? value : resolve(runDir, value);
}

// Launch options for chromium.launchPersistentContext. The profile opens in
// real Chrome by default (`profile_channel`), with the automation banner and
// flag removed: Google's sign-in refuses a browser that advertises automation,
// and the person signing in here is the account's owner, by hand.
// `profile_channel` is separate from `browser_channel`, which picks the browser
// that RECORDS (see record_scene.mjs).
export function profileLaunchOpts(cfg = {}, { headless = true } = {}) {
  return {
    channel: cfg.profile_channel || 'chrome',
    headless,
    ignoreDefaultArgs: ['--enable-automation'],
    args: ['--disable-blink-features=AutomationControlled'],
  };
}
