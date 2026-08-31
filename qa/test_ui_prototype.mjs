import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import test from 'node:test';

await import('../frontend/web/prototype.js');
const prototype = globalThis.ArchbroPrototype;

function memoryStorage(seed = {}) {
  const values = new Map(Object.entries(seed));
  return {
    getItem(key) { return values.has(key) ? values.get(key) : null; },
    setItem(key, value) { values.set(key, String(value)); },
    removeItem(key) { values.delete(key); },
  };
}

test('sign-up validation requires name, email, eight characters, and matching confirmation', () => {
  assert.deepEqual(prototype.validateSignUp({name: '', email: 'bad', password: 'short', confirmPassword: 'other'}), {
    name: 'Enter your name.',
    email: 'Enter a valid email address.',
    password: 'Use at least 8 characters.',
    confirmPassword: 'Passwords do not match.',
  });
});

test('demo profiles are keyed by identity and survive session changes', () => {
  const storage = memoryStorage();
  prototype.startSession(storage, {provider: 'password', email: 'Human@Example.com', name: 'Human'});
  prototype.updateCurrentProfile(storage, {onboardingComplete: true, defaultLens: 'software'});
  prototype.endSession(storage);
  prototype.startSession(storage, {provider: 'password', email: 'human@example.com', name: 'Human'});
  assert.equal(prototype.currentProfile(storage).defaultLens, 'software');
  assert.equal(prototype.currentProfile(storage).onboardingComplete, true);
});

test('malformed stored JSON falls back to a safe signed-out state', () => {
  const storage = memoryStorage({'archbro-demo-session': '{broken'});
  assert.equal(prototype.loadSession(storage), null);
  assert.equal(prototype.currentProfile(storage), null);
});

test('valid JSON with the wrong session shape is rejected and removed', () => {
  for (const value of [null, [], 'session', 7, {}, {id: 42}]) {
    const storage = memoryStorage({'archbro-demo-session': JSON.stringify(value)});
    assert.equal(prototype.loadSession(storage), null);
    assert.equal(storage.getItem('archbro-demo-session'), null);
  }
});

test('starting a session replaces malformed profile maps with a safe identity map', () => {
  for (const value of [null, [], 'profiles', 7]) {
    const storage = memoryStorage({'archbro-demo-profiles': JSON.stringify(value)});
    assert.doesNotThrow(() => prototype.startSession(storage, {
      provider: 'password', email: 'safe@example.com', name: 'Safe User',
    }));
    assert.equal(prototype.currentProfile(storage).name, 'Safe User');
  }
});

test('a session without a matching profile signs out without deleting profile data', () => {
  const profiles = {'email:other@example.com': {name: 'Other User'}};
  const storage = memoryStorage({
    'archbro-demo-session': JSON.stringify({
      id: 'email:missing@example.com', provider: 'password', email: 'missing@example.com', name: 'Missing User',
    }),
    'archbro-demo-profiles': JSON.stringify(profiles),
  });

  assert.equal(prototype.currentProfile(storage), null);
  assert.equal(storage.getItem('archbro-demo-session'), null);
  assert.deepEqual(JSON.parse(storage.getItem('archbro-demo-profiles')), profiles);
});

test('the current profile normalizes display fields, notifications, and incomplete lenses', () => {
  const id = 'email:reviewer@example.com';
  const storage = memoryStorage({
    'archbro-demo-session': JSON.stringify({id, provider: 'password', email: 'reviewer@example.com', name: 'Review User'}),
    'archbro-demo-profiles': JSON.stringify({
      [id]: {
        id,
        email: 42,
        name: {unexpected: true},
        onboardingComplete: true,
        defaultLens: '',
        notifications: {architectureApprovals: 'yes', blockedTasks: false},
      },
    }),
  });

  assert.deepEqual(prototype.currentProfile(storage), {
    id,
    email: 'reviewer@example.com',
    name: 'Review User',
    onboardingComplete: false,
    defaultLens: null,
    notifications: {architectureApprovals: true, blockedTasks: false},
  });
});

test('an unknown stored lens returns the profile to first-run setup', () => {
  const storage = memoryStorage();
  prototype.startSession(storage, {provider: 'github', email: 'github@demo.archbro.local', name: 'GitHub user'});
  prototype.updateCurrentProfile(storage, {onboardingComplete: true, defaultLens: 'unknown'});
  assert.equal(prototype.currentProfile(storage).onboardingComplete, false);
  assert.equal(prototype.currentProfile(storage).defaultLens, null);
});

test('missing and null stored lenses cannot bypass first-run setup', () => {
  for (const [label, lens] of [['missing', undefined], ['null', null]]) {
    const id = `email:${label}@example.com`;
    const storedProfile = {
      id,
      email: `${label}@example.com`,
      name: `${label} lens`,
      onboardingComplete: true,
      notifications: null,
    };
    if (lens !== undefined) storedProfile.defaultLens = lens;
    const storage = memoryStorage({
      'archbro-demo-session': JSON.stringify({id, provider: 'password', email: storedProfile.email, name: storedProfile.name}),
      'archbro-demo-profiles': JSON.stringify({[id]: storedProfile}),
    });

    const normalized = prototype.currentProfile(storage);
    assert.equal(normalized.onboardingComplete, false);
    assert.equal(normalized.defaultLens, null);
    assert.deepEqual(normalized.notifications, {architectureApprovals: true, blockedTasks: true});
  }
});

const webRoot = new URL('../frontend/web/', import.meta.url);

test('landing opens authentication without collecting a project goal', async () => {
  const html = await readFile(new URL('index.html', webRoot), 'utf8');
  assert.match(html, /id="landingAuthTeaser"/);
  assert.match(html, /id="landingAuthSend"/);
  assert.match(html, /Describe the project you want to build \.\.\./);
  assert.doesNotMatch(html, /Describe the project you want to build after you sign in\./);
  assert.doesNotMatch(html, /id="landingGoal"/);
  assert.doesNotMatch(html, /archbro-pending-goal/);
  assert.match(html, /id="landingLoginBtn"/);
  assert.match(html, /id="authEmail"/);
  assert.match(html, /id="authPassword"/);
  assert.match(html, /data-auth-provider="google"/);
  assert.match(html, /data-auth-provider="github"/);
  assert.ok(html.indexOf('id="authEmail"') < html.indexOf('data-auth-provider="google"'));
});

test('auth provider buttons keep inline logos and visible provider names', async () => {
  const html = await readFile(new URL('index.html', webRoot), 'utf8');
  for (const provider of ['google', 'github']) {
    assert.match(html, new RegExp(`<button[^>]+data-auth-provider="${provider}"[^>]*>\\s*<svg`, 'i'));
  }
  assert.match(html, new RegExp(`data-auth-provider="google"[^>]*>\\s*<svg[\\s\\S]*?</svg>\\s*<span>Google</span>`, 'i'));
  assert.match(html, new RegExp(`data-auth-provider="github"[^>]*>\\s*<svg[\\s\\S]*?</svg>\\s*<span>GitHub</span>`, 'i'));
});

test('auth copy and project overview controls stay stripped down', async () => {
  const html = await readFile(new URL('index.html', webRoot), 'utf8');
  const js = await readFile(new URL('app.js', webRoot), 'utf8');
  assert.doesNotMatch(html, /<small>WELCOME<\/small>/);
  assert.doesNotMatch(html, /prototype-note/);
  assert.doesNotMatch(html, /id="editProjectBtn"/);
  assert.doesNotMatch(html, /id="deleteProjectBtn"/);
  assert.match(html, /id="projectTree"/);
  assert.match(js, /data-project-menu/);
  assert.match(js, /aria-haspopup="menu"/);
  assert.match(js, /aria-expanded="\$\{menuOpen\}"/);
  for (const action of ['Edit project', 'Rename project', 'Delete project']) {
    assert.match(js, new RegExp(action));
  }
});

test('auth modal restores focus to the control that opened it', async () => {
  const js = await readFile(new URL('app.js', webRoot), 'utf8');
  assert.match(js, /function openAuthentication\(trigger = document\.activeElement\)/);
  for (const triggerId of ['landingLoginBtn', 'landingAuthTeaser', 'landingAuthSend']) {
    assert.match(js, new RegExp(`\\$\\('${triggerId}'\\)\\.addEventListener\\('click', \\(event\\) => openAuthentication\\(event\\.currentTarget\\)\\)`));
  }
  assert.match(js, /if \(state\.experience\.authClosingReturnFocus\) \(trigger \|\| \$\('landingAuthTeaser'\)\)\.focus\(\);/);
});

test('every prototype password field has its own visibility control', async () => {
  const html = await readFile(new URL('index.html', webRoot), 'utf8');
  for (const target of ['authPassword', 'authConfirmPassword']) {
    assert.match(html, new RegExp(`data-password-target="${target}"`));
  }
});

test('preference onboarding offers exactly the three approved primary lenses', async () => {
  const html = await readFile(new URL('index.html', webRoot), 'utf8');
  assert.match(html, /<h1>What kind of project do you want to build\?<\/h1>/);
  const lenses = [...html.matchAll(/data-project-lens="([^"]+)"/g)].map((match) => match[1]);
  assert.deepEqual(lenses, ['software', 'design', 'engineering']);
  assert.match(html, /id="preferenceContinueBtn"[^>]+disabled/);
});

test('prototype persistence surface no longer exposes pending-goal helpers', () => {
  for (const helper of ['loadPendingGoal', 'savePendingGoal', 'clearPendingGoal']) {
    assert.equal(helper in prototype, false);
  }
  assert.equal('pendingGoal' in prototype.KEYS, false);
});

test('workspace uses a per-project tree instead of the old selector and Needs You nav', async () => {
  const [html, js] = await Promise.all([
    readFile(new URL('index.html', webRoot), 'utf8'),
    readFile(new URL('app.js', webRoot), 'utf8'),
  ]);
  assert.match(html, /id="projectTree"/);
  assert.doesNotMatch(html, /id="projectSelect"/);
  assert.doesNotMatch(html, /data-view="attention"/);
  for (const view of ['overview', 'architecture', 'tasks']) {
    assert.match(js, new RegExp(`data-project-view="${view}"`));
  }
});

test('new project journey exposes accessible staged controls', async () => {
  const html = await readFile(new URL('index.html', webRoot), 'utf8');
  for (const id of ['newProjectNameDialog', 'newProjectName', 'newProjectNameError', 'initialGoalStage', 'initialGoal', 'initialGoalError', 'refineGoalStage', 'goalDraftText', 'onboardingAsk']) {
    assert.match(html, new RegExp(`id="${id}"`));
  }
  assert.match(html, /id="newProjectNameDialog"[^>]+aria-labelledby=/);
  assert.match(html, /id="initialGoalStage"[^>]+aria-labelledby=/);
  assert.match(html, /id="refineGoalStage"[^>]+aria-labelledby=/);
  assert.match(html, /id="editOnboardingProjectName"[^>]+type="button"/);
  assert.match(html, /id="initialGoalContinueBtn"[^>]+type="submit"/);
  assert.match(html, /id="useGoalBtn"[^>]+type="button"/);
});

test('goal refinement ask composer exposes a reduced-motion-safe vibrant full-perimeter rainbow typing state', async () => {
  const [html, js, css] = await Promise.all([
    readFile(new URL('index.html', webRoot), 'utf8'),
    readFile(new URL('app.js', webRoot), 'utf8'),
    readFile(new URL('styles.css', webRoot), 'utf8'),
  ]);
  assert.match(html, /id="onboardingAsk"/);
  assert.match(html, /app\.js\?v=20260830-mcp-graph/);
  assert.match(html, /styles\.css\?v=20260830-5/);
  assert.match(js, /onboardingAsk.*classList\.toggle\('rainbow-active'/s);
  assert.match(css, /\.onboarding-ask\.rainbow-active/);
  assert.match(css, /\.onboarding-ask::before[,{][^}]*-webkit-mask:conic-gradient/);
  assert.match(css, /-webkit-mask-composite:xor/);
  assert.match(css, /mask-composite:exclude/);
  assert.match(css, /\.onboarding-ask::before[,{][^}]*#ff2d55/);
  assert.match(css, /\.onboarding-ask\.rainbow-active[,{][^}]*border-color:transparent;/);
  assert.match(css, /\.onboarding-ask\.rainbow-active::before[,{][^}]*opacity:1;/);
  assert.doesNotMatch(css, /@keyframes ask-rainbow-halo\{[^}]*transform:rotate/);
  assert.match(css, /@media\(prefers-reduced-motion:reduce\)[\s\S]*\.onboarding-ask\.rainbow-active/);
});

test('human approvals and account settings live in top-right controls', async () => {
  const html = await readFile(new URL('index.html', webRoot), 'utf8');
  for (const id of ['notificationBtn', 'notificationBadge', 'notificationMenu', 'accountBtn', 'accountMenu', 'accountSettingsDialog']) {
    assert.match(html, new RegExp(`id="${id}"`));
  }
  for (const label of ['Profile', 'Preferences', 'Settings', 'Log out']) assert.match(html, new RegExp(`>${label}<`));
});

test('Needs You derives pending proposals before blocked tasks', () => {
  const items = prototype.deriveNeedsYou(
    [{id: 'proposal-1', status: 'PENDING', reason: 'Review data boundary'}],
    [{id: 'task-1', status: 'BLOCKED', title: 'Choose provider'}],
  );
  assert.deepEqual(items.map((item) => item.kind), ['proposal', 'task']);
});

test('prototype CSS keeps the Product Canvas responsive and motion-aware', async () => {
  const css = await readFile(new URL('styles.css', webRoot), 'utf8');

  assert.match(css, /@media\(max-width:760px\)/);
  assert.match(css, /@media\(prefers-reduced-motion:reduce\)/);
  assert.match(css, /:focus-visible/);
  assert.match(css, /\.sidebar-backdrop/);
  assert.doesNotMatch(css, /linear-gradient|radial-gradient|backdrop-filter/i);
});

test('workspace exposes accessible project navigation, overlays, and project-lens controls', async () => {
  const [html, js] = await Promise.all([
    readFile(new URL('index.html', webRoot), 'utf8'),
    readFile(new URL('app.js', webRoot), 'utf8'),
  ]);

  assert.match(html, /role="radiogroup"/);
  assert.match(html, /role="radio" aria-checked="false"/);
  assert.match(html, /id="notificationMenu"[^>]+role="dialog"/);
  assert.match(html, /id="accountMenu"[^>]+role="menu"/);
  assert.match(js, /function wireLensRadioGroup\(/);
  assert.match(js, /event\.key === 'Home'|event\.key === "Home"/);
  assert.match(js, /event\.key === 'End'|event\.key === "End"/);
  assert.match(js, /aria-current="page"/);
  assert.match(js, /function closeOverlay\(/);
  assert.match(js, /event\.metaKey \|\| event\.ctrlKey/);
});

test('onboarding Ask and account actions expose their form and menu semantics', async () => {
  const html = await readFile(new URL('index.html', webRoot), 'utf8');

  assert.match(html, /<label[^>]+for="onboardingAsk"[^>]*>Ask the Agent<\/label>/);
  const accountMenu = html.match(/<div id="accountMenu"[\s\S]*?<\/div>/)?.[0] || '';
  for (const section of ['profile', 'preferences', 'settings']) {
    assert.match(accountMenu, new RegExp(`<button[^>]+role="menuitem"[^>]+data-account-section="${section}"`));
  }
  assert.match(accountMenu, /<button id="logoutBtn"[^>]+role="menuitem"/);
});

test('project view composer shares the typing-only rainbow edge state', async () => {
  const [html, js, css] = await Promise.all([
    readFile(new URL('index.html', webRoot), 'utf8'),
    readFile(new URL('app.js', webRoot), 'utf8'),
    readFile(new URL('styles.css', webRoot), 'utf8'),
  ]);

  assert.match(html, /<form id="instructionForm"[^>]+global-agent-composer/);
  assert.match(html, /id="instruction"/);
  assert.match(js, /instruction.*classList\.toggle\('rainbow-active'/s);
  assert.match(js, /if \(input\.value\.trim\(\) === message\) \{\s*input\.value = '';\s*syncInstructionRainbowState\(\);/);
  assert.match(css, /\.global-agent-composer::before/);
  assert.match(css, /\.global-agent-composer\.rainbow-active::before/);
});

test('automatic project recovery expands the first project before selecting it', async () => {
  const js = await readFile(new URL('app.js', webRoot), 'utf8');

  assert.match(js, /state\.expandedProjectIds\.add\(state\.projects\[0\]\.id\);\s*await selectProject\(state\.projects\[0\]\.id\);/);
});

test('zero-project workspace stays browsable instead of forcing onboarding', async () => {
  const [html, js] = await Promise.all([
    readFile(new URL('index.html', webRoot), 'utf8'),
    readFile(new URL('app.js', webRoot), 'utf8'),
  ]);
  assert.match(html, /id="workspaceHome"/);
  assert.match(html, /id="workspaceHomeEmpty"/);
  assert.match(html, /id="projectCards"/);
  assert.match(js, /function renderWorkspaceHome\(/);
  assert.match(js, /if \(!state\.projectId\) \{[\s\S]*?renderWorkspaceHome\(\);\s*return true;/s);
  assert.doesNotMatch(js, /else if \(!state\.projectId\) \{\s*startOnboarding\(\);/s);
});

test('project cards use Living Architecture snapshots with a pending fallback', async () => {
  const [html, js] = await Promise.all([
    readFile(new URL('index.html', webRoot), 'utf8'),
    readFile(new URL('app.js', webRoot), 'utf8'),
  ]);
  assert.match(html, /id="projectCards"/);
  assert.match(js, /class="project-card/);
  assert.match(js, /function renderArchitectureSnapshot\(/);
  assert.match(js, /Architecture snapshot pending/);
  assert.match(js, /\/projects\/\$\{project\.id\}\/architecture/);
});

test('expanded projects persist locally alongside the current project', async () => {
  const js = await readFile(new URL('app.js', webRoot), 'utf8');
  assert.match(js, /archbro-expanded-projects/);
  assert.match(js, /function loadExpandedProjectIds\(/);
  assert.match(js, /function persistExpandedProjectIds\(/);
  assert.match(js, /persistExpandedProjectIds\(\);/);
});

test('deleting the last project returns to the personal workspace home', async () => {
  const js = await readFile(new URL('app.js', webRoot), 'utf8');
  assert.match(js, /if \(state\.projects\.length\) \{[\s\S]*?selectProject\(state\.projects\[0\]\.id\);[\s\S]*?\} else \{[\s\S]*?renderWorkspaceHome\(\);/);
});

test('Personal workspace opens the complete project home from an active project', async () => {
  const [html, js] = await Promise.all([
    readFile(new URL('index.html', webRoot), 'utf8'),
    readFile(new URL('app.js', webRoot), 'utf8'),
  ]);
  assert.match(html, /id="workspaceSwitcherBtn"/);
  assert.match(html, /Personal workspace/);
  assert.match(js, /async function openPersonalWorkspace\(/);
  assert.match(js, /state\.projectId = null;/);
  assert.match(js, /localStorage\.removeItem\('archbro-project-id'\);/);
  assert.match(js, /\$\('workspaceSwitcherBtn'\)\.addEventListener\('click', openPersonalWorkspace\);/);
});

test('empty workspace cancellation returns home instead of reopening project naming', async () => {
  const js = await readFile(new URL('app.js', webRoot), 'utf8');
  assert.match(js, /function cancelNewProjectNameDialog\(\)[\s\S]*?state\.projects\.length === 0[\s\S]*?renderWorkspaceHome\(\);/);
  assert.match(js, /function handleNewProjectNameDialogClose\(\)[\s\S]*?state\.projects\.length > 0/);
});
