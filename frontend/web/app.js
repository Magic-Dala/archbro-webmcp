import {
  authenticationErrorMessage,
  createFirebaseEmailAccount,
  getFirebaseIdToken,
  restoreFirebaseIdentity,
  signInWithFirebaseEmail,
  signInWithGitHubAccount,
  signInWithGoogleAccount,
  signOutFromFirebase,
  usesFirebaseAuthentication,
} from './firebase-auth.js?v=20260901-auth-providers';

const prototype = window.ArchbroPrototype;
const storedProjectId = localStorage.getItem('archbro-project-id');
const WEBMCP_AGENT_MODE = new URLSearchParams(window.location.search).get('mode') === 'webmcp';
const AUTH_PROVIDER_SIGN_INS = new Map([
  ['google', signInWithGoogleAccount],
  ['github', signInWithGitHubAccount],
]);

function loadExpandedProjectIds(storage = localStorage) {
  try {
    const raw = storage.getItem('archbro-expanded-projects');
    const ids = raw ? JSON.parse(raw) : [];
    return new Set(Array.isArray(ids) ? ids.filter((id) => typeof id === 'string' && id.trim()) : []);
  } catch {
    return new Set();
  }
}

function persistExpandedProjectIds(storage = localStorage) {
  storage.setItem('archbro-expanded-projects', JSON.stringify([...state.expandedProjectIds]));
}
const state = {
  projectId: storedProjectId,
  projects: [],
  project: null,
  tasks: [],
  architecture: null,
  diagram: null,
  diagramError: null,
  codeArchitecture: null,
  codeDiagram: null,
  architectureGraphKind: 'living',
  selectedCodeNodeId: null,
  scopeComponentId: null,
  readingMode: 'MAP',
  selectedComponentId: null,
  graphFocusMode: 'all',
  proposals: [],
  activity: [],
  lastRun: null,
  selectedTaskId: null,
  selectedProposalId: null,
  currentView: 'overview',
  taskUpdating: new Set(),
  expandedProjectIds: loadExpandedProjectIds(),
  projectSnapshots: new Map(),
  renamingProjectId: null,
  openProjectMenuId: null,
  projectMenuFocusId: null,
  onboarding: {
    active: !storedProjectId,
    stage: 'name',
    projectName: '',
    initialGoal: '',
    messages: [],
    draft: null,
    working: false,
    workingStartedAt: null,
    workingTimer: null,
    lastError: null,
  },
};

if (storedProjectId) state.expandedProjectIds.add(storedProjectId);

state.experience = {
  phase: 'landing',
  authMode: 'signin',
  selectedLens: null,
  workspaceInitialized: false,
  dialogReturnFocus: new Map(),
  authClosingReturnFocus: true,
};

let activeMcpOAuthPopup = null;
let mcpOAuthStatusRequestId = 0;
const MCP_PROVIDER_STATUS_TTL_MS = 5000;
const mcpProviderStatusCache = new Map();
const handledMcpOAuthPopups = new WeakSet();

const $ = (id) => document.getElementById(id);
const views = {
  overview: {title: 'Project Overview', subtitle: 'Keep project reality aligned with the accepted architecture.'},
  tasks: {title: 'Tasks', subtitle: 'Concrete, actionable work shared by humans and the agent.'},
  architecture: {title: 'Architecture', subtitle: 'Compare accepted design intent with revision-pinned implementation evidence.'},
};

function showExperience(phase) {
  state.experience.phase = phase;
  $('entryExperience').classList.toggle('hidden', phase === 'workspace');
  $('workspaceShell').classList.toggle('hidden', phase !== 'workspace');
  for (const viewName of ['landing', 'auth', 'preference']) {
    if (viewName === 'auth') continue;
    const view = $(`${viewName}View`);
    if (!view) continue;
    const shouldHide = phase === 'auth' ? viewName !== 'landing' : phase !== viewName;
    view.classList.toggle('hidden', shouldHide);
  }
}

function authenticationBusy() {
  return $('authForm')?.getAttribute('aria-busy') === 'true';
}

function closeAuthentication({returnFocus = true, force = false} = {}) {
  if (authenticationBusy() && !force) return false;
  const authDialog = $('authView');
  const shouldReturnToLanding = returnFocus && state.experience.phase === 'auth';
  state.experience.authClosingReturnFocus = returnFocus;
  if (authDialog.open) authDialog.close();
  else if (returnFocus) $('landingAuthTeaser').focus();
  if (shouldReturnToLanding) showExperience('landing');
  return true;
}

function openAuthentication(trigger = document.activeElement) {
  setAuthMode('signin');
  showExperience('auth');
  showDialog('authView', trigger);
}

function setAuthMode(mode) {
  state.experience.authMode = mode;
  const signingUp = mode === 'signup';
  $('authForm').dataset.authMode = mode;
  $('authNameField').classList.toggle('hidden', !signingUp);
  $('authConfirmField').classList.toggle('hidden', !signingUp);
  $('authPassword').autocomplete = signingUp ? 'new-password' : 'current-password';
  $('authTitle').textContent = signingUp ? 'Create your account' : 'Welcome back';
  $('authSubtitle').textContent = signingUp
    ? (usesFirebaseAuthentication() ? 'Create your Archbro account with email and password.' : 'Create a local demo profile to continue building with Archbro.')
    : 'Sign in to continue building with Archbro.';
  $('authSubmitBtn').textContent = signingUp ? 'Create account' : 'Continue with email';
  $('authModeToggle').textContent = signingUp ? 'Already have an account? Log in' : 'New to Archbro? Create an account';
  writeAuthErrors({});
  writeAuthMessage('');
}

function togglePasswordVisibility(button) {
  const input = $(button.dataset.passwordTarget);
  if (!input) return;
  const showing = input.type === 'text';
  input.type = showing ? 'password' : 'text';
  button.textContent = showing ? 'Show' : 'Hide';
  button.setAttribute('aria-label', showing ? 'Show password' : 'Hide password');
}

function writeAuthErrors(errors) {
  for (const field of ['name', 'email', 'password', 'confirmPassword']) {
    const target = $(`auth${field[0].toUpperCase()}${field.slice(1)}Error`);
    if (!target) continue;
    const message = errors[field] || '';
    target.textContent = message;
    if (message) target.setAttribute('role', 'alert');
    else target.removeAttribute('role');
  }
}

function writeAuthMessage(message) {
  $('authFormMessage').textContent = message;
}

function setAuthenticationBusy(busy) {
  $('authForm').setAttribute('aria-busy', String(busy));
  $('authSubmitBtn').disabled = busy;
  $('authModeToggle').disabled = busy;
  $('authCloseBtn').disabled = busy;
  $('authBackBtn').disabled = busy;
  document.querySelectorAll('[data-auth-provider]').forEach((button) => { button.disabled = busy; });
  $('authSubmitBtn').textContent = busy
    ? (state.experience.authMode === 'signup' ? 'Creating account…' : 'Signing in…')
    : (state.experience.authMode === 'signup' ? 'Create account' : 'Continue with email');
}

async function routeAfterAuthentication() {
  const profile = prototype.currentProfile(localStorage);
  if (!profile) {
    state.experience.workspaceInitialized = false;
    closeAuthentication();
    showExperience('landing');
    toast('That session could not be restored. Sign in again to continue.', true);
    return false;
  }
  closeAuthentication({returnFocus: false, force: true});
  if (WEBMCP_AGENT_MODE) {
    await enterWorkspace();
    return true;
  }
  if (profile && !profile.onboardingComplete && $('preferenceView')) {
    showExperience('preference');
    return true;
  }
  await enterWorkspace();
  return true;
}

function selectProjectLens(lens) {
  state.experience.selectedLens = lens;
  document.querySelectorAll('[data-project-lens]').forEach((button) => {
    const selected = button.dataset.projectLens === lens;
    button.setAttribute('aria-checked', String(selected));
    button.tabIndex = selected ? 0 : -1;
    button.classList.toggle('selected', selected);
  });
  $('preferenceContinueBtn').disabled = !lens;
}

function wireLensRadioGroup(container, onSelect) {
  const options = [...container.querySelectorAll('[role="radio"]')];
  const checked = options.find((button) => button.getAttribute('aria-checked') === 'true');
  options.forEach((button, index) => { button.tabIndex = button === (checked || options[0]) ? 0 : -1; });
  const move = (current, offset) => options[(options.indexOf(current) + offset + options.length) % options.length];
  options.forEach((button) => {
    button.addEventListener('click', () => onSelect(button.dataset.projectLens || button.dataset.settingsLens));
    button.addEventListener('keydown', (event) => {
      let next = null;
      if (event.key === 'ArrowRight' || event.key === 'ArrowDown') next = move(button, 1);
      if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') next = move(button, -1);
      if (event.key === 'Home') next = options[0];
      if (event.key === 'End') next = options.at(-1);
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        onSelect(button.dataset.projectLens || button.dataset.settingsLens);
        return;
      }
      if (!next) return;
      event.preventDefault();
      onSelect(next.dataset.projectLens || next.dataset.settingsLens);
      next.focus();
    });
  });
}

async function completePreference() {
  if (!state.experience.selectedLens) return;
  prototype.updateCurrentProfile(localStorage, {
    onboardingComplete: true,
    defaultLens: state.experience.selectedLens,
  });
  await enterWorkspace();
}

async function submitAuthentication(event) {
  event.preventDefault();
  const signingUp = state.experience.authMode === 'signup';
  const values = {
    name: $('authName').value,
    email: $('authEmail').value,
    password: $('authPassword').value,
    confirmPassword: $('authConfirmPassword').value,
  };
  const errors = signingUp ? prototype.validateSignUp(values) : prototype.validateSignIn(values);
  writeAuthErrors(errors);
  writeAuthMessage('');
  if (Object.keys(errors).length) return;
  setAuthenticationBusy(true);
  try {
    if (usesFirebaseAuthentication()) {
      const identity = signingUp
        ? await createFirebaseEmailAccount(values)
        : await signInWithFirebaseEmail(values);
      prototype.startSession(localStorage, identity);
      if (identity.profileSynced === false) {
        toast('Your account was created. Your display name is saved in this browser.', false);
      }
    } else {
      prototype.startSession(localStorage, {provider: 'password', email: values.email, name: values.name});
    }
    $('authPassword').value = '';
    $('authConfirmPassword').value = '';
    await routeAfterAuthentication();
  } catch (error) {
    writeAuthMessage(authenticationErrorMessage(error));
  } finally {
    setAuthenticationBusy(false);
  }
}

async function enterWorkspace() {
  showExperience('workspace');
  if (!state.experience.workspaceInitialized) {
    const initialized = await initializeWorkspace();
    if (!initialized) return;
    state.experience.workspaceInitialized = true;
  }
  renderAccountIdentity();
}

async function api(path, options = {}) {
  const {timeoutMs = 0, headers = {}, ...fetchOptions} = options;
  const controller = timeoutMs ? new AbortController() : null;
  const timeout = controller ? setTimeout(() => controller.abort(), timeoutMs) : null;
  try {
    const token = await getFirebaseIdToken();
    const res = await fetch(path, {
      headers: {
        'Content-Type': 'application/json',
        ...(token ? {Authorization: `Bearer ${token}`} : {}),
        ...headers,
      },
      ...fetchOptions,
      ...(controller ? {signal: controller.signal} : {}),
    });
    if (!res.ok) {
      let detail = 'Request failed';
      try {
        const body = await res.json();
        detail = body.detail || JSON.stringify(body);
      } catch {}
      throw new Error(`${res.status}: ${detail}`);
    }
    return res.status === 204 ? null : res.json();
  } catch (err) {
    if (err?.name === 'AbortError') {
      throw new Error(`Request timed out after ${Math.round(timeoutMs / 1000)}s. Your Goal and Ask are preserved; retry when ready.`);
    }
    throw err;
  } finally {
    if (timeout) clearTimeout(timeout);
  }
}

function toast(message, error = false) {
  const el = $('toast');
  el.textContent = message;
  el.classList.toggle('error', error);
  el.classList.remove('hidden');
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => el.classList.add('hidden'), 4500);
}

function setWorking(working, detail = '') {
  const el = $('agentStatus');
  el.classList.toggle('working', working);
  el.innerHTML = `<span class="pulse"></span>${working ? (detail || 'Agent working…') : 'Agent ready'}`;
}

function escapeHtml(value = '') {
  return String(value).replace(/[&<>'"]/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
}

function statusClass(status) {
  return status === 'IN_PROGRESS' ? 'progress' : status.toLowerCase();
}

async function loadProjects() {
  state.projects = await api('/projects');
  renderProjectTree();
  return state.projects;
}

async function loadProjectSnapshots() {
  const snapshots = new Map();
  await Promise.all(state.projects.map(async (project) => {
    try {
      const architecture = await api(`/projects/${project.id}/architecture`);
      let rootDiagram = null;
      if (Number(architecture?.version || 0) > 0 && architecture?.components?.length) {
        try { rootDiagram = await loadArchitectureDiagram(project.id, architecture, null); } catch { rootDiagram = null; }
      }
      snapshots.set(project.id, {architecture, rootDiagram});
    } catch { snapshots.set(project.id, null); }
  }));
  state.projectSnapshots = snapshots;
  return snapshots;
}

function graphPathData(points = [], radius = 8) {
  if (!points.length) return '';
  if (points.length < 3 || radius <= 0) {
    return points.map((point, index) => `${index === 0 ? 'M' : 'L'} ${point.x} ${point.y}`).join(' ');
  }
  const commands = [`M ${points[0].x} ${points[0].y}`];
  for (let index = 1; index < points.length - 1; index += 1) {
    const previous = points[index - 1];
    const current = points[index];
    const next = points[index + 1];
    const previousLength = Math.hypot(current.x - previous.x, current.y - previous.y);
    const nextLength = Math.hypot(next.x - current.x, next.y - current.y);
    const cornerRadius = Math.min(radius, previousLength / 2, nextLength / 2);
    if (cornerRadius < 1) {
      commands.push(`L ${current.x} ${current.y}`);
      continue;
    }
    const before = {
      x: current.x - ((current.x - previous.x) / previousLength) * cornerRadius,
      y: current.y - ((current.y - previous.y) / previousLength) * cornerRadius,
    };
    const after = {
      x: current.x + ((next.x - current.x) / nextLength) * cornerRadius,
      y: current.y + ((next.y - current.y) / nextLength) * cornerRadius,
    };
    commands.push(`L ${before.x} ${before.y}`);
    commands.push(`Q ${current.x} ${current.y} ${after.x} ${after.y}`);
  }
  const end = points[points.length - 1];
  commands.push(`L ${end.x} ${end.y}`);
  return commands.join(' ');
}

function renderArchitectureSnapshot(snapshot) {
  const architecture = snapshot?.architecture || null;
  const graph = snapshot?.rootDiagram || null;
  if (!architecture || Number(architecture.version || 0) < 1 || !graph?.nodes?.length) {
    return '<div class="project-snapshot project-snapshot-pending"><span class="snapshot-icon" aria-hidden="true">⌁</span><strong>Architecture snapshot pending</strong><small>Generate Living Architecture to see the system map here.</small></div>';
  }
  const edges = graph.edges.map((edge) => `<path data-snapshot-edge="${escapeHtml(edge.id)}" d="${graphPathData(edge.points)}"/>`).join('');
  const nodes = graph.nodes.map((node) => `<g class="snapshot-node" data-snapshot-node="${escapeHtml(node.id)}"><rect x="${node.x}" y="${node.y}" width="${node.width}" height="${node.height}" rx="12"/><text x="${node.x + node.width / 2}" y="${node.y + node.height / 2 + 3}" text-anchor="middle">${escapeHtml(node.label || node.component_id || 'Area')}</text></g>`).join('');
  return `<div class="project-snapshot project-snapshot-architecture" role="img" aria-label="Living Architecture root snapshot version ${escapeHtml(architecture.version)}"><span class="snapshot-label">LIVING ARCHITECTURE · v${escapeHtml(architecture.version)}</span><svg viewBox="0 0 ${graph.width} ${graph.height}" preserveAspectRatio="xMidYMid meet" aria-hidden="true"><g class="snapshot-edges">${edges}</g>${nodes}</svg></div>`;
}

function projectCardStatus(project, architecture) {
  if (architecture?.version > 0) return {label: 'ACTIVE', className: 'active'};
  if (project.status === 'DONE') return {label: 'DONE', className: 'done'};
  return {label: 'DRAFT', className: 'draft'};
}

function renderProjectCards() {
  const cards = $('projectCards');
  if (!cards) return;
  cards.innerHTML = state.projects.map((project) => {
    const snapshot = state.projectSnapshots.get(project.id);
    const architecture = snapshot?.architecture || null;
    const status = projectCardStatus(project, architecture);
    const snapshotMeta = architecture?.version > 0
      ? `Architecture v${architecture.version} · click to open Living Graph`
      : 'Goal saved · architecture pending';
    return `<article class="project-card" data-project-card="${escapeHtml(project.id)}"><button class="project-card-open" type="button" data-project-card-open="${escapeHtml(project.id)}"><div class="project-card-preview">${renderArchitectureSnapshot(snapshot)}</div><div class="project-card-body"><div class="project-card-heading"><strong>${escapeHtml(project.name)}</strong><span class="project-card-status ${status.className}">${status.label}</span></div><p>${escapeHtml(snapshotMeta)}</p><span class="project-card-link">Open project <span aria-hidden="true">→</span></span></div></button></article>`;
  }).join('');
  cards.querySelectorAll('[data-project-card-open]').forEach((button) => button.addEventListener('click', async () => {
    if (await selectProject(button.dataset.projectCardOpen)) closeMobileSidebar();
  }));
}

function renderWorkspaceHome() {
  state.onboarding.active = false;
  $('emptyState').classList.add('hidden');
  $('workspace').classList.remove('hidden');
  $('workspaceHome').classList.remove('hidden');
  $('workspaceSwitcherBtn').setAttribute('aria-current', 'page');
  document.querySelectorAll('#workspace > .view').forEach((view) => view.classList.remove('active'));
  $('globalAgentDock').classList.add('hidden');
  $('pageTitle').textContent = 'Personal workspace';
  $('pageSubtitle').textContent = 'Browse your projects and open one when you are ready.';
  $('workspaceHomeCount').textContent = `${state.projects.length} project${state.projects.length === 1 ? '' : 's'}`;
  $('workspaceHomeEmpty').classList.toggle('hidden', state.projects.length > 0);
  $('projectCards').classList.toggle('hidden', state.projects.length === 0);
  renderProjectTree();
  renderProjectCards();
  renderNotifications();
  renderAccountIdentity();
}

async function openPersonalWorkspace() {
  state.projectId = null;
  state.project = null;
  state.tasks = [];
  state.architecture = null;
  state.diagram = null;
  state.diagramError = null;
  state.codeArchitecture = null;
  state.codeDiagram = null;
  state.architectureGraphKind = 'living';
  state.selectedCodeNodeId = null;
  state.graphFocusMode = 'all';
  state.proposals = [];
  state.lastRun = null;
  state.selectedComponentId = null;
  state.scopeComponentId = null;
  state.readingMode = 'MAP';
  state.selectedTaskId = null;
  state.selectedProposalId = null;
  state.currentView = 'overview';
  state.openProjectMenuId = null;
  state.renamingProjectId = null;
  localStorage.removeItem('archbro-project-id');
  await loadProjectSnapshots();
  renderWorkspaceHome();
  closeMobileSidebar();
}

function renderProjectTree() {
  const tree = $('projectTree');
  if (!tree) return;
  const nodes = state.projects.map((project) => {
    const expanded = state.expandedProjectIds.has(project.id);
    const current = project.id === state.projectId;
    const childrenId = `projectChildren-${project.id}`;
    const menuId = `projectMenu-${project.id}`;
    const menuOpen = state.openProjectMenuId === project.id;
    if (state.renamingProjectId === project.id) {
      return `<li class="project-node" data-project-id="${escapeHtml(project.id)}"><form class="project-rename-form" data-project-rename-form="${escapeHtml(project.id)}"><label class="sr-only" for="projectRenameInput">Project name</label><input id="projectRenameInput" data-project-rename-input value="${escapeHtml(project.name)}" /><button type="submit">Save</button><button type="button" data-project-rename-cancel>Cancel</button><span class="field-error" data-project-rename-error></span></form></li>`;
    }
    const children = `<ul id="${escapeHtml(childrenId)}" class="project-children"${expanded ? '' : ' hidden'}><li><button class="${current && state.currentView === 'overview' ? 'active' : ''}" data-project-view="overview"${current && state.currentView === 'overview' ? ' aria-current="page"' : ''}>Project Overview</button></li><li><button class="${current && state.currentView === 'architecture' ? 'active' : ''}" data-project-view="architecture"${current && state.currentView === 'architecture' ? ' aria-current="page"' : ''}>Living Graph</button></li><li><button class="${current && state.currentView === 'tasks' ? 'active' : ''}" data-project-view="tasks"${current && state.currentView === 'tasks' ? ' aria-current="page"' : ''}>Tasks</button></li></ul>`;
    const menu = menuOpen
      ? `<div id="${escapeHtml(menuId)}" class="project-row-menu" data-project-menu-panel role="menu"><button type="button" role="menuitem" data-project-action="edit">Edit project</button><button type="button" role="menuitem" data-project-action="rename">Rename project</button><button type="button" role="menuitem" data-project-action="delete">Delete project</button></div>`
      : '';
    return `<li class="project-node${current ? ' current' : ''}" data-project-id="${escapeHtml(project.id)}"><div class="project-row"><button class="project-toggle" type="button" data-project-toggle aria-label="${expanded ? 'Collapse' : 'Expand'} ${escapeHtml(project.name)}" aria-expanded="${expanded}" aria-controls="${escapeHtml(childrenId)}">${expanded ? '⌄' : '›'}</button><button class="project-name" type="button" data-project-open aria-pressed="${current}"${current ? ' aria-current="location"' : ''}>${escapeHtml(project.name)}</button><div class="project-menu-wrap"><button class="project-menu-trigger" type="button" data-project-menu aria-label="Project actions for ${escapeHtml(project.name)}" aria-haspopup="menu" aria-expanded="${menuOpen}" aria-controls="${escapeHtml(menuId)}">⋯</button>${menu}</div></div>${children}</li>`;
  }).join('');
  tree.innerHTML = nodes ? `<ul class="project-list">${nodes}</ul>` : '<p class="project-tree-empty">No projects yet.</p>';
  wireProjectTree();
  restoreProjectMenuFocus();
}

function queueProjectMenuFocus(projectId) {
  state.projectMenuFocusId = projectId;
}

function clearProjectMenuFocusQueue() {
  state.projectMenuFocusId = null;
}

function restoreProjectMenuFocus() {
  if (!state.projectMenuFocusId) return;
  const projectId = state.projectMenuFocusId;
  state.projectMenuFocusId = null;
  setTimeout(() => document.querySelector(`[data-project-id="${CSS.escape(projectId)}"] [data-project-menu]`)?.focus(), 0);
}

function projectMenuTrigger(projectId) {
  return document.querySelector(`[data-project-id="${CSS.escape(projectId)}"] [data-project-menu]`);
}

function closeProjectMenu({returnFocus = false} = {}) {
  if (!state.openProjectMenuId) return;
  const projectId = state.openProjectMenuId;
  state.openProjectMenuId = null;
  if (returnFocus) queueProjectMenuFocus(projectId);
  else clearProjectMenuFocusQueue();
  renderProjectTree();
}

function closeProjectMenuAfterPointerEvent({returnFocus = false} = {}) {
  if (!state.openProjectMenuId) return;
  const projectId = state.openProjectMenuId;
  if (returnFocus) queueProjectMenuFocus(projectId);
  else clearProjectMenuFocusQueue();
  setTimeout(() => {
    if (state.openProjectMenuId !== projectId) return;
    state.openProjectMenuId = null;
    renderProjectTree();
  }, 0);
}

function toggleProjectMenu(projectId) {
  if (state.renamingProjectId) return;
  const opening = state.openProjectMenuId !== projectId;
  state.openProjectMenuId = opening ? projectId : null;
  if (!opening) queueProjectMenuFocus(projectId);
  renderProjectTree();
  if (opening) setTimeout(() => document.querySelector(`[data-project-id="${CSS.escape(projectId)}"] [data-project-action]`)?.focus(), 0);
}

function toggleProjectExpanded(projectId) {
  if (state.expandedProjectIds.has(projectId)) state.expandedProjectIds.delete(projectId);
  else state.expandedProjectIds.add(projectId);
  persistExpandedProjectIds();
  renderProjectTree();
  setTimeout(() => [...document.querySelectorAll('[data-project-id]')]
    .find((node) => node.dataset.projectId === projectId)
    ?.querySelector('[data-project-toggle]')?.focus(), 0);
}

function mobileSidebarEnabled() {
  return window.matchMedia('(max-width: 760px)').matches;
}

function syncMobileSidebarLayers() {
  const mobile = mobileSidebarEnabled();
  const open = mobile && document.body.classList.contains('sidebar-open');
  $('workspaceSidebar').inert = mobile && !open;
  if (mobile && !open) $('workspaceSidebar').setAttribute('aria-hidden', 'true');
  else $('workspaceSidebar').removeAttribute('aria-hidden');
  $('workspaceMain').inert = open;
  if (!mobile) {
    document.body.classList.remove('sidebar-open');
    $('sidebarBackdrop').classList.add('hidden');
    $('mobileSidebarBtn').setAttribute('aria-expanded', 'false');
  }
}

function openMobileSidebar() {
  if (!mobileSidebarEnabled()) return;
  document.body.classList.add('sidebar-open');
  $('sidebarBackdrop').classList.remove('hidden');
  $('mobileSidebarBtn').setAttribute('aria-expanded', 'true');
  syncMobileSidebarLayers();
  setTimeout(() => ($('newProjectBtn') || document.querySelector('[data-project-open]'))?.focus(), 0);
}

function closeMobileSidebar({returnFocus = true} = {}) {
  const wasOpen = mobileSidebarEnabled() && document.body.classList.contains('sidebar-open');
  document.body.classList.remove('sidebar-open');
  $('sidebarBackdrop').classList.add('hidden');
  $('mobileSidebarBtn').setAttribute('aria-expanded', 'false');
  syncMobileSidebarLayers();
  if (wasOpen && returnFocus) $('mobileSidebarBtn').focus();
}

function beginInlineRename(projectId) {
  state.openProjectMenuId = null;
  state.renamingProjectId = projectId;
  renderProjectTree();
  document.querySelector('[data-project-rename-input]')?.select();
}

async function commitInlineRename(projectId) {
  const input = document.querySelector('[data-project-rename-input]');
  const name = input?.value.trim() || '';
  const error = document.querySelector('[data-project-rename-error]');
  if (!name) {
    if (error) {
      error.textContent = 'Enter a project name.';
      error.setAttribute('role', 'alert');
    }
    return;
  }
  try {
    await api(`/projects/${projectId}`, {method: 'PATCH', body: JSON.stringify({name})});
    const project = state.projects.find((item) => item.id === projectId);
    if (project) project.name = name;
    if (projectId === state.projectId && state.project) {
      state.project = {...state.project, name};
      $('welcomeTitle').textContent = name;
    }
    state.renamingProjectId = null;
    queueProjectMenuFocus(projectId);
    await loadProjects();
    if (projectId === state.projectId) await refresh();
    toast('Project renamed.');
  } catch (err) {
    if (error) {
      error.textContent = err.message;
      error.setAttribute('role', 'alert');
    }
  }
}

function wireProjectTree() {
  const cancelInlineRename = (projectId) => {
    state.renamingProjectId = null;
    queueProjectMenuFocus(projectId);
    renderProjectTree();
  };
  document.querySelectorAll('[data-project-id]').forEach((node) => {
    const projectId = node.dataset.projectId;
    node.querySelector('[data-project-toggle]')?.addEventListener('click', () => toggleProjectExpanded(projectId));
    node.querySelector('[data-project-open]')?.addEventListener('click', async () => {
      if (await selectProject(projectId)) closeMobileSidebar();
    });
    node.querySelector('[data-project-menu]')?.addEventListener('click', (event) => {
      event.stopPropagation();
      toggleProjectMenu(projectId);
    });
    node.querySelector('[data-project-menu]')?.addEventListener('keydown', (event) => {
      if (!['ArrowDown', 'Enter', ' '].includes(event.key)) return;
      event.preventDefault();
      if (state.openProjectMenuId !== projectId) toggleProjectMenu(projectId);
    });
    node.querySelectorAll('[data-project-action]').forEach((button) => button.addEventListener('click', async () => {
      const action = button.dataset.projectAction;
      const menuTrigger = projectMenuTrigger(projectId);
      closeProjectMenu({returnFocus: false});
      if (action === 'rename') {
        beginInlineRename(projectId);
        return;
      }
      if (projectId !== state.projectId && !(await selectProject(projectId))) return;
      const dialogTrigger = projectMenuTrigger(projectId) || menuTrigger;
      if (action === 'edit') openEditProject(dialogTrigger);
      if (action === 'delete') openDeleteProject(dialogTrigger);
    }));
    node.querySelector('[data-project-menu-panel]')?.addEventListener('keydown', (event) => {
      const items = [...node.querySelectorAll('[data-project-action]')];
      const current = items.indexOf(document.activeElement);
      let target = null;
      if (event.key === 'ArrowDown') target = items[(current + 1 + items.length) % items.length];
      if (event.key === 'ArrowUp') target = items[(current - 1 + items.length) % items.length];
      if (event.key === 'Home') target = items[0];
      if (event.key === 'End') target = items.at(-1);
      if (event.key === 'Escape') {
        event.preventDefault();
        event.stopPropagation();
        closeProjectMenu({returnFocus: true});
        return;
      }
      if (event.key === 'Tab') {
        closeProjectMenu({returnFocus: false});
        return;
      }
      if (!target) return;
      event.preventDefault();
      target.focus();
    });
    node.querySelector('[data-project-rename-cancel]')?.addEventListener('click', () => cancelInlineRename(projectId));
    node.querySelector('[data-project-rename-form]')?.addEventListener('submit', async (event) => {
      event.preventDefault();
      await commitInlineRename(projectId);
    });
    node.querySelector('[data-project-rename-input]')?.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        event.stopPropagation();
        cancelInlineRename(projectId);
      }
    });
    node.querySelectorAll('[data-project-view]').forEach((button) => button.addEventListener('click', async () => {
      if (projectId !== state.projectId && !(await selectProject(projectId))) return;
      switchView(button.dataset.projectView);
      closeMobileSidebar();
    }));
  });
}

function normalizeDiagramGraph(payload) {
  const diagram = payload?.diagram || payload?.diagram_view || payload?.diagramView;
  const layout = payload?.positioned_graph || payload?.positionedGraph || payload?.layout;
  if (!diagram || !layout) throw new Error('Positioned diagram response must include DiagramView and PositionedGraph.');
  if (diagram.diagram_version !== 'archbro.diagram.v1') throw new Error(`Unsupported diagram contract: ${diagram.diagram_version || 'missing'}`);
  if (layout.layout_version !== 'archbro.layout.v1') throw new Error(`Unsupported layout contract: ${layout.layout_version || 'missing'}`);
  if (Number(diagram.architecture_version) !== Number(layout.architecture_version)) throw new Error('Diagram and layout architecture versions do not match.');
  const positionedById = new Map((layout.nodes || []).map((node) => [node.node_id, node]));
  const nodes = (diagram.nodes || []).map((node) => {
    const positioned = positionedById.get(node.id);
    if (!positioned) throw new Error(`PositionedGraph is missing node ${node.id}.`);
    const numbers = [positioned.x, positioned.y, positioned.width, positioned.height].map(Number);
    if (numbers.some((value) => !Number.isFinite(value)) || numbers[2] <= 0 || numbers[3] <= 0) throw new Error(`Invalid positioned node ${node.id}.`);
    const childCount = Number(node.child_count || 0);
    if (!Number.isInteger(childCount) || childCount < 0) throw new Error(`Invalid child_count for ${node.id}.`);
    const projectionRole = node.projection_role || 'PRIMARY';
    if (!['SCOPE','PRIMARY','CONTEXT'].includes(projectionRole)) throw new Error(`Invalid projection_role for ${node.id}.`);
    return {...node, projectionRole, childCount, x:numbers[0], y:numbers[1], width:numbers[2], height:numbers[3], layer:Number(positioned.layer || 0), order:Number(positioned.order || 0), hierarchyPath:positioned.hierarchy_path || []};
  });
  if (nodes.length !== positionedById.size) throw new Error('DiagramView and PositionedGraph node sets do not match.');
  const routesById = new Map((layout.edges || []).map((edge) => [edge.edge_id, edge]));
  const edges = (diagram.edges || []).map((edge) => {
    const route = routesById.get(edge.id);
    if (!route) throw new Error(`PositionedGraph is missing edge ${edge.id}.`);
    if (route.source !== edge.source || route.target !== edge.target) throw new Error(`Edge route endpoints do not match DiagramView for ${edge.id}.`);
    const points = (route.points || []).map((point) => ({x:Number(point.x), y:Number(point.y)}));
    if (points.length < 2 || points.some((point) => !Number.isFinite(point.x) || !Number.isFinite(point.y))) throw new Error(`Invalid route points for ${edge.id}.`);
    return {...edge, points, routing:route.routing || '', order:Number(route.order || 0)};
  });
  if (edges.length !== routesById.size) throw new Error('DiagramView and PositionedGraph edge sets do not match.');
  const width=Number(layout.width), height=Number(layout.height);
  if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) throw new Error('PositionedGraph requires positive graph dimensions.');
  return {diagramVersion:diagram.diagram_version, layoutVersion:layout.layout_version, architectureVersion:Number(diagram.architecture_version), summary:diagram.summary || '', width, height, nodes:nodes.sort((a,b)=>a.order-b.order || a.id.localeCompare(b.id)), edges:edges.sort((a,b)=>a.order-b.order || a.id.localeCompare(b.id))};
}

function normalizeScopedDiagramResponse(payload, requestedScopeComponentId = null) {
  if (payload?.schema && payload.schema !== 'archbro.scoped_diagram.v1') throw new Error(`Unsupported scoped diagram envelope: ${payload.schema}`);
  const graph = normalizeDiagramGraph(payload);
  const raw = payload?.scope || null;
  if (requestedScopeComponentId && !raw) throw new Error('Scoped diagram response is missing scope metadata.');
  const componentId = raw?.component_id ?? null;
  if (requestedScopeComponentId && componentId !== requestedScopeComponentId) throw new Error(`Scoped diagram response returned ${componentId || 'ROOT'} instead of ${requestedScopeComponentId}.`);
  const ancestorPath = Array.isArray(raw?.ancestor_path) ? raw.ancestor_path.map((item) => ({componentId:item.component_id ?? null,nodeId:item.node_id ?? null,label:String(item.label || item.component_id || 'Overview')})).filter((item) => item.componentId) : [];
  return {...graph, scope:{componentId,nodeId:raw?.node_id ?? (componentId ? `node:${componentId}` : null),label:String(raw?.label || (componentId ? componentId : 'Overview')),isLeaf:Boolean(raw?.is_leaf),ancestorPath,directRelationships:Array.isArray(raw?.direct_relationships) ? raw.direct_relationships : []}};
}

function normalizeCodeArchitectureSnapshot(payload) {
  if (!payload || payload.schema !== 'archbro.code_architecture.v1') throw new Error(`Unsupported code architecture envelope: ${payload?.schema || 'missing'}`);
  const repository = payload.repository || {};
  const revision = String(repository.revision || '').toLowerCase();
  if (repository.provider !== 'github' || repository.revision_pinned !== true || !/^[0-9a-f]{40}$/.test(revision)) throw new Error('Code architecture requires an exact pinned GitHub commit SHA.');
  const diagram = payload.diagram;
  const layout = payload.positioned_graph;
  if (!diagram || !layout) throw new Error('Code architecture must include a diagram and positioned graph.');
  if (diagram.diagram_version !== 'archbro.code_diagram.v1') throw new Error(`Unsupported code diagram contract: ${diagram.diagram_version || 'missing'}`);
  if (layout.layout_version !== 'archbro.layout.v1') throw new Error(`Unsupported code layout contract: ${layout.layout_version || 'missing'}`);
  const positionedById = new Map((layout.nodes || []).map((node) => [node.node_id, node]));
  const nodes = (diagram.nodes || []).map((node) => {
    const positioned = positionedById.get(node.id);
    if (!positioned) throw new Error(`Code PositionedGraph is missing node ${node.id}.`);
    if (!String(node.id || '').startsWith('code-node:')) throw new Error(`Code graph node must use the code-node namespace: ${node.id}.`);
    const numbers = [positioned.x, positioned.y, positioned.width, positioned.height].map(Number);
    if (numbers.some((value) => !Number.isFinite(value)) || numbers[2] <= 0 || numbers[3] <= 0) throw new Error(`Invalid positioned code node ${node.id}.`);
    const sources = Array.isArray(node.sources) ? node.sources.map((source) => {
      const href = String(source.href || '');
      const expectedPrefix = `https://github.com/${repository.slug}/blob/${revision}/`;
      if (!href.startsWith(expectedPrefix)) throw new Error(`Code evidence for ${node.id} is not pinned to the snapshot revision.`);
      return {...source, href};
    }) : [];
    return {
      ...node,
      childCount:Number(node.child_count || 0),
      sources,
      x:numbers[0], y:numbers[1], width:numbers[2], height:numbers[3],
      layer:Number(positioned.layer || 0), order:Number(positioned.order || 0),
      hierarchyPath:positioned.hierarchy_path || [],
    };
  });
  if (nodes.length !== positionedById.size) throw new Error('Code Diagram and PositionedGraph node sets do not match.');
  const routesById = new Map((layout.edges || []).map((edge) => [edge.edge_id, edge]));
  const edges = (diagram.edges || []).map((edge) => {
    const route = routesById.get(edge.id);
    if (!route || route.source !== edge.source || route.target !== edge.target) throw new Error(`Code edge route does not match ${edge.id}.`);
    const points = (route.points || []).map((point) => ({x:Number(point.x), y:Number(point.y)}));
    if (points.length < 2 || points.some((point) => !Number.isFinite(point.x) || !Number.isFinite(point.y))) throw new Error(`Invalid code edge route ${edge.id}.`);
    return {...edge, points, routing:route.routing || '', order:Number(route.order || 0)};
  });
  if (edges.length !== routesById.size) throw new Error('Code Diagram and PositionedGraph edge sets do not match.');
  const width=Number(layout.width), height=Number(layout.height);
  if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) throw new Error('Code PositionedGraph requires positive dimensions.');
  return {
    schema:payload.schema,
    classification:payload.classification,
    canonicalStateMutated:Boolean(payload.canonical_state_mutated),
    repository:{...repository, revision},
    evidenceVerification:payload.evidence_verification || {},
    summary:payload.summary || diagram.summary || '',
    eventId:payload.event_id || null,
    publishedAt:payload.published_at || null,
    width, height,
    nodes:nodes.sort((a,b)=>a.order-b.order || a.id.localeCompare(b.id)),
    edges:edges.sort((a,b)=>a.order-b.order || a.id.localeCompare(b.id)),
  };
}

function architectureDiagramPath(projectId, architectureVersion, scopeComponentId = null, readingMode = 'MAP') {
  const params = new URLSearchParams();
  if (scopeComponentId) params.set('scope', scopeComponentId);
  if (Number(architectureVersion) > 0) params.set('expected_architecture_version', String(Number(architectureVersion)));
  if (['MAP','READ','FULL'].includes(readingMode)) params.set('reading_mode', readingMode);
  const query=params.toString();
  return `/projects/${projectId}/architecture/diagram${query ? `?${query}` : ''}`;
}

async function loadArchitectureDiagram(projectId, architecture, scopeComponentId = null, readingMode = 'MAP') {
  if (!architecture?.components?.length) return null;
  const payload = await api(architectureDiagramPath(projectId, architecture.version, scopeComponentId, readingMode));
  return normalizeScopedDiagramResponse(payload, scopeComponentId);
}

async function loadLatestCodeArchitecture(projectId) {
  return api(`/projects/${projectId}/code-architecture/latest`);
}

async function loadProjectContext(projectId, {scopeComponentId = null} = {}) {
  const [project,tasks,architecture,proposals,activity,codeArchitecture] = await Promise.all([
    api(`/projects/${projectId}`), api(`/projects/${projectId}/tasks`), api(`/projects/${projectId}/architecture`), api(`/projects/${projectId}/architecture/proposals`), api(`/projects/${projectId}/events?limit=12`), loadLatestCodeArchitecture(projectId),
  ]);
  let diagram=null, diagramError=null, codeDiagram=null;
  try { diagram = await loadArchitectureDiagram(projectId, architecture, scopeComponentId); } catch (err) { diagramError=err?.message || String(err); }
  if (codeArchitecture) codeDiagram = normalizeCodeArchitectureSnapshot(codeArchitecture);
  return {project,tasks,architecture,diagram,diagramError,proposals,activity,codeArchitecture,codeDiagram};
}

async function selectProject(projectId) {
  if (!projectId) return false;
  state.openProjectMenuId = null;
  if (projectId === state.projectId && state.project) {
    state.onboarding.active = false;
    state.currentView = 'overview';
    render();
    return true;
  }
  $('projectTree').setAttribute('aria-busy', 'true');
  try {
    const context = await loadProjectContext(projectId);
    Object.assign(state, context, {
      projectId,
      lastRun: null,
      scopeComponentId: null,
      readingMode: 'MAP',
      selectedComponentId: null,
      selectedCodeNodeId: null,
      architectureGraphKind: 'living',
      graphFocusMode: 'all',
      selectedTaskId: null,
      selectedProposalId: null,
      currentView: 'overview',
    });
    state.onboarding.active = false;
    state.expandedProjectIds.add(projectId);
    persistExpandedProjectIds();
    localStorage.setItem('archbro-project-id', projectId);
    render();
    return true;
  } catch (err) {
    toast(`Could not open that project. ${err.message}`, true);
    return false;
  } finally {
    $('projectTree').removeAttribute('aria-busy');
  }
}

async function refresh() {
  if (state.onboarding.active) {
    renderOnboarding();
    return true;
  }
  if (!state.projectId) {
    await loadProjectSnapshots();
    renderWorkspaceHome();
    return true;
  }
  try {
    Object.assign(state, await loadProjectContext(state.projectId, {scopeComponentId: state.scopeComponentId}));
    render();
    return true;
  } catch (err) {
    if (String(err.message).startsWith('404:')) {
      localStorage.removeItem('archbro-project-id');
      state.projectId = null;
      state.project = null;
      await loadProjects();
      if (state.projects.length) {
        state.expandedProjectIds.add(state.projects[0].id);
        persistExpandedProjectIds();
        await selectProject(state.projects[0].id);
      } else {
        await loadProjectSnapshots();
        renderWorkspaceHome();
      }
      return true;
    } else {
      toast(err.message, true);
      return false;
    }
  }
}

function startOnboarding() {
  if (state.onboarding.workingTimer) clearInterval(state.onboarding.workingTimer);
  state.currentView = 'overview';
  state.onboarding = {
    active: true,
    stage: 'name',
    projectName: '',
    initialGoal: '',
    messages: [],
    draft: null,
    working: false,
    workingStartedAt: null,
    workingTimer: null,
    lastError: null,
  };
  state.selectedTaskId = null;
  state.selectedProposalId = null;
  state.selectedComponentId = null;
  state.selectedCodeNodeId = null;
  state.architectureGraphKind = 'living';
  state.scopeComponentId = null;
  state.readingMode = 'MAP';
  renderOnboarding();
  openNewProjectNameDialog();
}

function renderOnboarding() {
  $('emptyState').classList.remove('hidden');
  $('workspace').classList.add('hidden');
  renderProjectTree();
  const agentModePanel = $('webmcpAgentModePanel');
  if (WEBMCP_AGENT_MODE) {
    $('pageTitle').textContent = 'WebMCP Agent Mode';
    $('pageSubtitle').textContent = 'Project mutations in this session must use the registered Site Tools.';
    $('initialGoalStage').classList.add('hidden');
    $('refineGoalStage').classList.add('hidden');
    agentModePanel?.classList.remove('hidden');
    $('onboardingBackBtn').classList.add('hidden');
    renderNotifications();
    renderAccountIdentity();
    return;
  }
  agentModePanel?.classList.add('hidden');
  $('pageTitle').textContent = 'New Project';
  $('pageSubtitle').textContent = state.onboarding.stage === 'refine' ? 'Refine the Goal Draft with the Agent before generating architecture.' : 'Name the project, then write its first goal.';
  $('initialGoalStage').classList.toggle('hidden', state.onboarding.stage !== 'goal');
  $('refineGoalStage').classList.toggle('hidden', state.onboarding.stage !== 'refine');
  $('onboardingBackBtn').classList.toggle('hidden', !state.projectId);
  $('onboardingProjectName').textContent = state.onboarding.projectName;
  if (state.onboarding.stage === 'goal') $('initialGoal').value = state.onboarding.initialGoal;
  if (state.onboarding.stage === 'refine' && document.activeElement !== $('goalDraftText')) $('goalDraftText').value = state.onboarding.initialGoal;
  syncOnboardingAskRainbowState();
  renderOnboardingConversation();
  renderGoalDraft();
  renderNotifications();
  renderAccountIdentity();
}

function openNewProjectNameDialog() {
  $('newProjectName').value = state.onboarding.projectName;
  $('newProjectNameError').textContent = '';
  showDialog('newProjectNameDialog');
}

function submitNewProjectName(event) {
  event.preventDefault();
  const name = $('newProjectName').value.trim();
  if (!name) {
    $('newProjectNameError').textContent = 'Enter a project name.';
    $('newProjectName').setAttribute('aria-invalid', 'true');
    $('newProjectName').focus();
    return;
  }
  state.onboarding.projectName = name;
  $('newProjectName').removeAttribute('aria-invalid');
  state.onboarding.stage = state.onboarding.stage === 'refine' ? 'refine' : 'goal';
  $('newProjectNameDialog').close();
  renderOnboarding();
  setTimeout(() => (state.onboarding.stage === 'refine' ? $('goalDraftText') : $('initialGoal')).focus(), 0);
}

function continueToRefinement(event) {
  event.preventDefault();
  const goal = $('initialGoal').value.trim();
  if (!goal) {
    $('initialGoalError').textContent = 'Describe your project goal to continue.';
    $('initialGoal').setAttribute('aria-invalid', 'true');
    $('initialGoal').focus();
    return;
  }
  state.onboarding.initialGoal = goal;
  state.onboarding.stage = 'refine';
  $('initialGoal').removeAttribute('aria-invalid');
  $('initialGoalError').textContent = '';
  renderOnboarding();
  setTimeout(() => $('goalDraftText').focus(), 0);
}

function returnToProjectName() {
  openNewProjectNameDialog();
}

function hasCurrentProject() {
  return Boolean(state.projectId && state.project);
}

function cancelNewProjectNameDialog() {
  const emptyWorkspace = state.projects.length === 0;
  if (state.onboarding.stage === 'name' && !hasCurrentProject() && !emptyWorkspace) {
    $('newProjectNameError').textContent = 'Enter a project name to continue.';
    $('newProjectName').focus();
    return;
  }
  const returnToProject = state.onboarding.stage === 'name' && !emptyWorkspace;
  $('newProjectNameDialog').close();
  if (returnToProject) backToCurrentProject();
  else if (emptyWorkspace && state.onboarding.stage === 'name') renderWorkspaceHome();
  else if (emptyWorkspace) renderOnboarding();
}

function handleNewProjectNameDialogClose() {
  if (state.onboarding.stage === 'name' && !hasCurrentProject() && state.projects.length > 0) setTimeout(() => openNewProjectNameDialog(), 0);
}

function handleNewProjectNameDialogCancel(event) {
  event.preventDefault();
  cancelNewProjectNameDialog();
}

function onboardingProgressText() {
  const startedAt = state.onboarding.workingStartedAt || Date.now();
  const elapsed = Math.max(0, Math.floor((Date.now() - startedAt) / 1000));
  let stage = 'Reading your current Goal and Ask';
  if (elapsed >= 3) stage = 'Merging requirements without replacing your Goal';
  if (elapsed >= 7) stage = 'Building a self-contained Goal draft';
  if (elapsed >= 13) stage = 'Gemini is busy; trying a bounded fallback';
  return {elapsed, text: `${stage} · ${elapsed}s`};
}

function updateOnboardingProgressUI() {
  if (!state.onboarding.working) return;
  const progress = onboardingProgressText();
  const bubble = $('onboardingWorkingText');
  if (bubble) bubble.textContent = progress.text;
  setWorking(true, `Updating Goal · ${progress.elapsed}s`);
}

function startOnboardingProgress() {
  if (state.onboarding.workingTimer) clearInterval(state.onboarding.workingTimer);
  state.onboarding.working = true;
  state.onboarding.workingStartedAt = Date.now();
  state.onboarding.lastError = null;
  const sendButton = document.querySelector('#onboardingForm button[type="submit"]');
  if (sendButton) {
    sendButton.disabled = true;
    sendButton.textContent = 'Working…';
  }
  renderOnboardingConversation();
  updateOnboardingProgressUI();
  state.onboarding.workingTimer = setInterval(updateOnboardingProgressUI, 1000);
}

function stopOnboardingProgress() {
  if (state.onboarding.workingTimer) clearInterval(state.onboarding.workingTimer);
  state.onboarding.workingTimer = null;
  state.onboarding.working = false;
  state.onboarding.workingStartedAt = null;
  const sendButton = document.querySelector('#onboardingForm button[type="submit"]');
  if (sendButton) {
    sendButton.disabled = false;
    sendButton.textContent = 'Send';
  }
  setWorking(false);
}

function renderOnboardingConversation() {
  const el = $('onboardingConversation');
  if (!state.onboarding.messages.length && !state.onboarding.working && !state.onboarding.lastError) {
    el.classList.add('hidden');
    el.innerHTML = '';
    return;
  }

  el.classList.remove('hidden');

  const messages = state.onboarding.messages.map((message) => {
    const label = message.role === 'user' ? 'You' : 'Agent';
    return `<div class="chat-message ${message.role}"><small>${label}</small><p>${escapeHtml(message.content)}</p></div>`;
  }).join('');

  const working = state.onboarding.working
    ? `<div class="chat-message assistant working-bubble"><small>Agent · working</small><p><span class="working-spinner" aria-hidden="true"></span><span id="onboardingWorkingText">${escapeHtml(onboardingProgressText().text)}</span></p><span class="working-hint">Your existing Goal is kept as the baseline while this runs.</span></div>`
    : '';

  const error = state.onboarding.lastError
    ? `<div class="chat-message assistant error-bubble"><small>Agent · stopped</small><p>I could not finish this Goal update. Nothing was cleared or persisted.</p><span class="working-hint">${escapeHtml(state.onboarding.lastError)}</span><button id="onboardingRetryBtn" class="retry-ask" type="button">Retry this Ask</button></div>`
    : '';

  el.innerHTML = messages + working + error;
  const retry = $('onboardingRetryBtn');
  if (retry) retry.onclick = retryOnboardingAsk;
  el.scrollTop = el.scrollHeight;
}

function renderGoalDraft() {
  const draft = state.onboarding.draft;
  const goalInput = $('goalDraftText');

  if (!draft) {
    const hasManualGoal = Boolean(goalInput.value.trim());
    $('goalDraftStatus').textContent = hasManualGoal ? 'Using your written Goal' : 'Write a Goal or start with Ask';
    $('goalReadyBadge').textContent = hasManualGoal ? 'MANUAL' : 'DRAFT';
    $('goalReadyBadge').className = `status-pill ${hasManualGoal ? 'DONE' : ''}`;
    $('missingInfoWrap').classList.add('hidden');
    updateGoalConfirmState();
    return;
  }

  $('goalDraftStatus').textContent = draft.ready ? 'Ready to become the project Goal' : 'Still shaping the project Goal';
  $('goalReadyBadge').textContent = draft.ready ? 'READY' : 'DRAFT';
  $('goalReadyBadge').className = `status-pill ${draft.ready ? 'DONE' : ''}`;
  if (document.activeElement !== goalInput) {
    goalInput.value = draft.goal || '';
    state.onboarding.initialGoal = goalInput.value;
  }
  const missing = draft.missing_information || [];
  $('missingInfoWrap').classList.toggle('hidden', !missing.length);
  $('missingInfo').innerHTML = missing.map((item) => `<li>${escapeHtml(item)}</li>`).join('');
  updateGoalConfirmState();
}

function syncOnboardingAskRainbowState({activate = false} = {}) {
  const onboardingAsk = $('onboardingAsk');
  const composer = onboardingAsk?.closest('.onboarding-ask');
  if (!onboardingAsk || !composer) return;
  const hasContent = Boolean(onboardingAsk.value.trim());
  const focused = document.activeElement === onboardingAsk || onboardingAsk.matches(':focus');
  const shouldGlow = activate && hasContent && focused;
  composer.classList.toggle('rainbow-active', shouldGlow);
}

function syncInstructionRainbowState({activate = false} = {}) {
  const instruction = $('instruction');
  const composer = instruction?.closest('.global-agent-composer');
  if (!instruction || !composer) return;
  const hasContent = Boolean(instruction.value.trim());
  const focused = document.activeElement === instruction || instruction.matches(':focus');
  const shouldGlow = activate && hasContent && focused;
  composer.classList.toggle('rainbow-active', shouldGlow);
}

function updateGoalConfirmState() {
  const hasGoal = Boolean($('goalDraftText').value.trim());
  $('useGoalBtn').disabled = !hasGoal || state.onboarding.working;
}

async function requestOnboardingGoalDraft() {
  if (state.onboarding.working) return;
  startOnboardingProgress();
  updateGoalConfirmState();
  try {
    const draft = await api('/onboarding/goal', {
      method: 'POST',
      body: JSON.stringify({
        messages: state.onboarding.messages,
        current_goal: $('goalDraftText').value.trim(),
      }),
      timeoutMs: 30000,
    });
    state.onboarding.draft = draft;
    state.onboarding.lastError = null;
    state.onboarding.messages.push({role: 'assistant', content: draft.assistant_message});
  } catch (err) {
    state.onboarding.lastError = err.message || String(err);
    toast('Goal update stopped. Your Goal and Ask are preserved; retry when ready.', true);
  } finally {
    stopOnboardingProgress();
    renderOnboardingConversation();
    renderGoalDraft();
  }
}

async function submitOnboardingAsk() {
  if (WEBMCP_AGENT_MODE) {
    toast('Built-in Agent onboarding is disabled in WebMCP Agent Mode.', true);
    return;
  }
  if (state.onboarding.working) return;
  const input = $('onboardingAsk');
  const content = input.value.trim();
  if (!content) return;
  input.value = '';
  syncOnboardingAskRainbowState();
  state.onboarding.messages.push({role: 'user', content});
  state.onboarding.lastError = null;
  renderOnboardingConversation();
  await requestOnboardingGoalDraft();
}

async function retryOnboardingAsk() {
  if (state.onboarding.working) return;
  if (!state.onboarding.messages.length && !$('goalDraftText').value.trim()) return;
  state.onboarding.lastError = null;
  await requestOnboardingGoalDraft();
}

async function confirmGoalAndGenerate() {
  if (WEBMCP_AGENT_MODE) {
    toast('Built-in architecture generation is disabled in WebMCP Agent Mode.', true);
    return;
  }
  const name = state.onboarding.projectName.trim();
  const goal = $('goalDraftText').value.trim();
  if (!name || !goal || state.onboarding.working) return;
  try {
    setWorking(true, 'Creating project…');
    const project = await api('/projects', {
      method: 'POST',
      body: JSON.stringify({name, goal, description: 'Goal drafted through Goal + Ask onboarding.'}),
    });
    $('goalDraftText').value = '';
    $('onboardingAsk').value = '';
    state.projectId = project.id;
    state.expandedProjectIds.add(project.id);
    localStorage.setItem('archbro-project-id', project.id);
    state.project = project;
    state.lastRun = null;
    state.onboarding.active = false;
    await loadProjects();
    await refresh();
    toast('Goal confirmed. Generating Architecture v1…');
    await generateInitialArchitecture();
  } catch (err) {
    toast(err.message, true);
  } finally {
    setWorking(false);
  }
}

function backToCurrentProject() {
  if (!state.projectId) return;
  state.onboarding.active = false;
  refresh();
}

function openEditProject(trigger = document.activeElement) {
  if (!state.project) return;
  $('editProjectName').value = state.project.name;
  $('editProjectGoal').value = state.project.goal;
  $('editProjectDescription').value = state.project.description || '';
  const lockedGoal = (state.architecture?.version || 0) > 0;
  $('editProjectGoal').disabled = lockedGoal;
  $('editGoalHint').classList.toggle('hidden', !lockedGoal);
  showDialog('editProjectDialog', trigger);
}

async function saveProjectEdits() {
  if (!state.projectId) return;
  const body = {
    name: $('editProjectName').value.trim(),
    description: $('editProjectDescription').value.trim(),
  };
  if (!$('editProjectGoal').disabled) body.goal = $('editProjectGoal').value.trim();
  if (!body.name || (body.goal !== undefined && !body.goal)) return;
  try {
    const updated = await api(`/projects/${state.projectId}`, {method: 'PATCH', body: JSON.stringify(body)});
    state.project = updated;
    $('editProjectDialog').close();
    await loadProjects();
    await refresh();
    toast('Project updated.');
  } catch (err) {
    toast(err.message, true);
  }
}

function openDeleteProject(trigger = document.activeElement) {
  if (!state.project) return;
  $('deleteProjectName').textContent = state.project.name;
  showDialog('deleteProjectDialog', trigger);
}

async function deleteCurrentProject() {
  if (!state.projectId) return;
  const deletedId = state.projectId;
  const deletedName = state.project?.name || 'Project';
  try {
    await api(`/projects/${state.projectId}`, {method: 'DELETE'});
    $('deleteProjectDialog').close();
    state.projectId = null;
    state.project = null;
    state.tasks = [];
    state.architecture = null;
    state.diagram = null;
    state.diagramError = null;
    state.codeArchitecture = null;
    state.codeDiagram = null;
    state.architectureGraphKind = 'living';
    state.selectedCodeNodeId = null;
    state.graphFocusMode = 'all';
    state.proposals = [];
    state.lastRun = null;
    state.selectedComponentId = null;
    state.scopeComponentId = null;
    state.readingMode = 'MAP';
    state.projectSnapshots.delete(deletedId);
    state.expandedProjectIds.delete(deletedId);
    persistExpandedProjectIds();
    localStorage.removeItem('archbro-project-id');
    await loadProjects();
    if (state.projects.length) {
      await selectProject(state.projects[0].id);
    } else {
      await loadProjectSnapshots();
      renderWorkspaceHome();
    }
    toast(`${deletedName} deleted.`);
  } catch (err) {
    toast(err.message, true);
  }
}

function closeDialogOnBackdrop(event) {
  const dialog = event.currentTarget;
  if (event.target === dialog) dialog.close();
}

function closeAuthDialogOnBackdrop(event) {
  if (event.target === event.currentTarget) closeAuthentication();
}

function closeNewProjectNameDialogOnBackdrop(event) {
  if (event.target === event.currentTarget) cancelNewProjectNameDialog();
}

function showDialog(dialogId, trigger = document.activeElement) {
  const dialog = $(dialogId);
  state.experience.dialogReturnFocus.set(dialogId, trigger);
  dialog.showModal();
  setTimeout(() => dialog.querySelector('[autofocus], input:not([disabled]), textarea:not([disabled]), button:not([disabled])')?.focus(), 0);
}

function closeOverlay({returnFocus = false} = {}) {
  const openMenu = [['notificationBtn', 'notificationMenu'], ['accountBtn', 'accountMenu']]
    .find(([, menuId]) => !$(menuId).classList.contains('hidden'));
  const projectMenuWasOpen = state.openProjectMenuId;
  const mobileWasOpen = document.body.classList.contains('sidebar-open');
  closeProjectMenu({returnFocus});
  closeTopMenus();
  closeMobileSidebar();
  if (returnFocus && projectMenuWasOpen) return;
  if (returnFocus && openMenu) $(openMenu[0]).focus();
  else if (returnFocus && mobileWasOpen) $('mobileSidebarBtn').focus();
}

function renderNotifications() {
  const profile = prototype.currentProfile(localStorage);
  const items = state.onboarding.active ? [] : prototype.deriveNeedsYou(state.proposals, state.tasks, profile?.notifications);
  $('notificationBadge').textContent = items.length;
  $('notificationBadge').classList.toggle('hidden', items.length === 0);
  $('notificationCount').textContent = `${items.length} request${items.length === 1 ? '' : 's'}`;
  $('notificationList').innerHTML = items.length
    ? items.map((item) => `<button class="notification-item" type="button" data-attention-kind="${item.kind}" data-attention-id="${escapeHtml(item.id)}"><strong>${escapeHtml(item.title)}</strong><span>${escapeHtml(item.description)}</span></button>`).join('')
    : `<p class="notification-empty" role="status" tabindex="-1">${state.onboarding.active ? 'Nothing needs you in a new project draft. Return to a project to review its items.' : 'Nothing needs your approval right now.'}</p>`;
  document.querySelectorAll('[data-attention-kind]').forEach((button) => button.addEventListener('click', async () => openAttentionItem(button.dataset.attentionKind, button.dataset.attentionId)));
}

function attentionItemExists(kind, id) {
  return kind === 'proposal'
    ? state.proposals.some((item) => item.id === id)
    : kind === 'task' && state.tasks.some((item) => item.id === id);
}

function showUnavailableAttentionItem() {
  closeTopMenus();
  $('notificationList').innerHTML = '<p class="notification-empty" role="status" tabindex="-1">Item no longer available. The project has been refreshed.</p>';
  $('notificationCount').textContent = 'Updated';
  $('notificationMenu').classList.remove('hidden');
  $('notificationBtn').setAttribute('aria-expanded', 'true');
  setTimeout(() => $('notificationList').querySelector('[role="status"]')?.focus(), 0);
}

async function openAttentionItem(kind, id) {
  closeTopMenus();
  if (!attentionItemExists(kind, id)) {
    if (!state.onboarding.active && state.projectId) await refresh();
    if (!attentionItemExists(kind, id)) {
      showUnavailableAttentionItem();
      return false;
    }
  }
  if (kind === 'proposal') {
    state.selectedProposalId = id;
    renderProposals();
    updateInstructionContext();
    showDialog('proposalReviewDialog', $('notificationBtn'));
    setTimeout(() => document.querySelector(`[data-proposal-select="${CSS.escape(id)}"]`)?.focus(), 0);
    return true;
  }
  state.selectedTaskId = id;
  switchView('tasks');
  renderTasks();
  setTimeout(() => document.querySelector(`[data-task-select="${CSS.escape(id)}"]`)?.focus(), 0);
  return true;
}

function closeTopMenus() {
  for (const [buttonId, menuId] of [['notificationBtn', 'notificationMenu'], ['accountBtn', 'accountMenu']]) {
    $(menuId).classList.add('hidden');
    $(buttonId).setAttribute('aria-expanded', 'false');
  }
}

function toggleTopMenu(buttonId, menuId) {
  const opening = $(menuId).classList.contains('hidden');
  closeProjectMenu({returnFocus: false});
  clearProjectMenuFocusQueue();
  closeTopMenus();
  if (!opening) return;
  $(menuId).classList.remove('hidden');
  $(buttonId).setAttribute('aria-expanded', 'true');
  const target = menuId === 'notificationMenu'
    ? $(menuId).querySelector('[data-attention-kind], #notificationCloseBtn') || $(menuId)
    : $(menuId).querySelector('[role="menuitem"]') || $(menuId);
  target.focus();
}

function renderAccountIdentity() {
  const profile = prototype.currentProfile(localStorage);
  if (!profile) return;
  const initials = profile.name.split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0].toUpperCase()).join('');
  const safeInitials = initials || 'HU';
  $('accountInitials').textContent = safeInitials;
  $('accountBtn').setAttribute('aria-label', `Account menu for ${profile.name}`);
  const lens = profile.defaultLens || '';
  const lensLabel = lens ? `${lens[0].toUpperCase()}${lens.slice(1)}` : '';
  $('workspaceLens').textContent = lensLabel ? `Default lens · ${lensLabel}` : '';
  $('workspaceLens').classList.toggle('hidden', !lensLabel);
}

function resetEphemeralSessionState() {
  if (state.onboarding.workingTimer) clearInterval(state.onboarding.workingTimer);
  state.openProjectMenuId = null;
  state.projectMenuFocusId = null;
  closeTopMenus();
  closeMobileSidebar({returnFocus: false});
  document.querySelectorAll('dialog[open]').forEach((dialog) => dialog.close());
  setArchitectureProgress(false);
  setWorking(false);
  clearTimeout(toast.timer);
  $('toast').textContent = '';
  $('toast').classList.add('hidden');
  $('toast').classList.remove('error');
  $('authForm').reset();
  writeAuthErrors({});
  document.querySelectorAll('[data-password-target]').forEach((button) => {
    const input = $(button.dataset.passwordTarget);
    if (input) input.type = 'password';
    button.textContent = 'Show';
    button.setAttribute('aria-label', 'Show password');
  });
  setAuthMode('signin');
  state.experience.selectedLens = null;
  document.querySelectorAll('[data-project-lens]').forEach((button, index) => {
    button.setAttribute('aria-checked', 'false');
    button.tabIndex = index === 0 ? 0 : -1;
    button.classList.remove('selected');
  });
  $('preferenceContinueBtn').disabled = true;
  state.currentView = 'overview';
  state.renamingProjectId = null;
  state.selectedComponentId = null;
  state.selectedCodeNodeId = null;
  state.architectureGraphKind = 'living';
  state.scopeComponentId = null;
  state.readingMode = 'MAP';
  state.selectedTaskId = null;
  state.selectedProposalId = null;
  state.lastRun = null;
  state.projects = [];
  state.project = null;
  state.tasks = [];
  state.architecture = null;
  state.diagram = null;
  state.diagramError = null;
  state.codeArchitecture = null;
  state.codeDiagram = null;
  state.graphFocusMode = 'all';
  state.proposals = [];
  state.projectSnapshots = new Map();
  state.taskUpdating.clear();
  state.projectId = localStorage.getItem('archbro-project-id');
  state.expandedProjectIds = loadExpandedProjectIds();
  if (state.projectId) state.expandedProjectIds.add(state.projectId);
  state.onboarding = {
    active: !state.projectId,
    stage: 'name',
    projectName: '',
    initialGoal: '',
    messages: [],
    draft: null,
    working: false,
    workingStartedAt: null,
    workingTimer: null,
    lastError: null,
  };
  state.experience.settingsSection = 'profile';
  $('onboardingAsk').value = '';
  syncOnboardingAskRainbowState();
  $('goalDraftText').value = '';
  $('initialGoal').value = '';
  $('newProjectName').value = '';
  $('instruction').value = '';
  syncInstructionRainbowState();
  $('instruction').removeAttribute('aria-invalid');
  $('instructionError').textContent = '';
}

async function logout() {
  try {
    await signOutFromFirebase();
    prototype.endSession(localStorage);
    state.experience.workspaceInitialized = false;
    resetEphemeralSessionState();
    showExperience('landing');
    $('landingAuthTeaser').focus();
  } catch (error) {
    toast(authenticationErrorMessage(error), true);
  }
}

function openAccountSection(section) {
  const profile = prototype.currentProfile(localStorage);
  if (!profile) return;
  state.experience.settingsSection = section;
  $('accountSettingsTitle').textContent = section[0].toUpperCase() + section.slice(1);
  document.querySelectorAll('[data-settings-panel]').forEach((button) => button.classList.toggle('active', button.dataset.settingsPanel === section));
  if (section === 'profile') {
    $('settingsPanel').innerHTML = `<label>Display name<input id="settingsName" value="${escapeHtml(profile.name)}" /></label><label>Email<input value="${escapeHtml(profile.email)}" disabled /></label><p id="settingsNameError" class="field-error"></p>`;
  } else if (section === 'preferences') {
    $('settingsPanel').innerHTML = `<div class="settings-lens-group" role="radiogroup" aria-label="Default project lens">${['software', 'design', 'engineering'].map((lens) => `<button type="button" role="radio" aria-checked="${profile.defaultLens === lens}" tabindex="${profile.defaultLens === lens ? '0' : '-1'}" data-settings-lens="${lens}">${lens[0].toUpperCase() + lens.slice(1)}</button>`).join('')}</div>`;
    wireLensRadioGroup($('settingsPanel'), (lens) => selectSettingsLens(lens));
  } else {
    const notifications = profile.notifications || {};
    $('settingsPanel').innerHTML = `<fieldset><legend>In-app notifications</legend><label><input id="settingsArchitectureNotifications" type="checkbox"${notifications.architectureApprovals !== false ? ' checked' : ''} />Architecture approvals</label><label><input id="settingsBlockedNotifications" type="checkbox"${notifications.blockedTasks !== false ? ' checked' : ''} />Blocked tasks</label></fieldset><p class="muted">This prototype stores notification preferences only in this browser.</p>`;
  }
  closeTopMenus();
  showDialog('accountSettingsDialog', $('accountBtn'));
}

function selectSettingsLens(lens) {
  document.querySelectorAll('[data-settings-lens]').forEach((button) => {
    const selected = button.dataset.settingsLens === lens;
    button.setAttribute('aria-checked', String(selected));
    button.tabIndex = selected ? 0 : -1;
  });
}

function saveAccountSettings(event) {
  event.preventDefault();
  const section = state.experience.settingsSection;
  if (section === 'profile') {
    const name = $('settingsName').value.trim();
    if (!name) {
      $('settingsNameError').textContent = 'Enter your name.';
      $('settingsNameError').setAttribute('role', 'alert');
      return;
    }
    prototype.updateCurrentProfile(localStorage, {name});
  } else if (section === 'preferences') {
    const lens = document.querySelector('[data-settings-lens][aria-checked="true"]')?.dataset.settingsLens;
    if (lens) prototype.updateCurrentProfile(localStorage, {defaultLens: lens});
  } else {
    prototype.updateCurrentProfile(localStorage, {notifications: {
      architectureApprovals: $('settingsArchitectureNotifications').checked,
      blockedTasks: $('settingsBlockedNotifications').checked,
    }});
  }
  $('accountSettingsDialog').close();
  renderAccountIdentity();
  renderNotifications();
}

function render() {
  $('emptyState').classList.add('hidden');
  $('workspace').classList.remove('hidden');
  $('workspaceHome').classList.add('hidden');
  $('workspaceSwitcherBtn').removeAttribute('aria-current');
  renderProjectTree();
  $('welcomeTitle').textContent = state.project.name;
  $('goalText').textContent = state.project.goal;
  $('projectStatus').textContent = state.project.status;
  const activeView = views[state.currentView] ? state.currentView : 'overview';
  state.currentView = activeView;
  $('pageTitle').textContent = views[activeView].title;
  $('pageSubtitle').textContent = views[activeView].subtitle;

  const awaiting = state.architecture.version === 0;
  $('bootstrapPanel').classList.toggle('hidden', !awaiting);
  $('globalAgentDock').classList.toggle('hidden', awaiting);
  $('bootstrapGoal').textContent = state.project.goal;

  const ready = state.tasks.filter((t) => t.status === 'TODO' && (t.owner === 'HUMAN' || t.owner === 'UNASSIGNED'));
  const running = state.tasks.filter((t) => t.status === 'IN_PROGRESS');
  const pending = state.proposals.filter((p) => p.status === 'PENDING');
  const needsYou = prototype.deriveNeedsYou(state.proposals, state.tasks, prototype.currentProfile(localStorage)?.notifications);

  $('readyCount').textContent = `${ready.length} ready task${ready.length === 1 ? '' : 's'}`;
  $('readySub').textContent = ready[0]?.title || (awaiting ? 'Architecture generation pending' : 'No actionable human task yet');
  $('runningCount').textContent = `${running.length} in progress`;
  $('archVersion').textContent = `Version ${state.architecture.version}`;
  $('archState').textContent = state.architecture.components.length ? 'Machine-readable source of truth' : 'Goal saved; Architecture v1 pending';
  $('needsCount').textContent = `${needsYou.length} item${needsYou.length === 1 ? '' : 's'}`;
  $('graphVersion').textContent = `v${state.diagram?.architectureVersion ?? state.architecture.version}`;
  $('graphReviewState').textContent = pending.length ? `${pending.length} item${pending.length === 1 ? '' : 's'} need review` : 'Aligned';

  const aligned = state.architecture.components.length && !pending.length;
  $('alignmentFill').style.width = state.architecture.components.length ? (pending.length ? '72%' : '100%') : '0%';
  $('alignmentText').textContent = state.architecture.components.length ? (aligned ? 'Aligned' : 'Review required') : 'Awaiting initial architecture';
  $('architectureSummary').textContent = state.architecture.summary || 'No architecture generated yet.';
  $('overviewMessage').textContent = awaiting ? 'The Goal is saved. Architecture generation needs to complete before normal project updates begin.' : pending.length ? 'One architecture decision needs your review.' : 'The current architecture has no pending approval boundary.';

  document.querySelectorAll('.view').forEach((v) => v.classList.remove('active'));
  $(`view-${activeView}`).classList.add('active');

  renderTasks();
  renderProposals();
  renderNotifications();
  renderAccountIdentity();
  renderGraph();
  renderRecentActivity();
  renderLastRun();
  renderGlobalAgentReply();
  updateInstructionContext();
}

function renderTasks() {
  const order = {IN_PROGRESS: 0, TODO: 1, BLOCKED: 2, DONE: 3};
  const sorted = [...state.tasks].sort((a, b) => order[a.status] - order[b.status]);
  $('taskTotal').textContent = `${sorted.length} task${sorted.length === 1 ? '' : 's'}`;
  $('taskList').innerHTML = sorted.length ? sorted.map((task) => taskRow(task, true)).join('') : '<p class="muted">No tasks yet.</p>';
  $('overviewTasks').innerHTML = sorted.filter((t) => t.status !== 'DONE').slice(0, 3).map((task) => taskRow(task, false)).join('') || '<p class="muted">No active tasks.</p>';
  document.querySelectorAll('[data-task-action]').forEach((btn) => btn.addEventListener('click', () => updateTask(btn.dataset.taskId, btn.dataset.taskAction)));
  document.querySelectorAll('#taskList [data-task-select]').forEach((button) => button.addEventListener('click', () => selectTaskContext(button.dataset.taskSelect)));
  document.querySelectorAll('#taskList [data-task-navigate]').forEach((row) => {
    row.addEventListener('dblclick', (event) => {
      if (event.target.closest('[data-task-action]')) return;
      navigateTaskToArchitecture(row.dataset.taskNavigate);
    });
    row.addEventListener('keydown', (event) => {
      if (event.target !== row || event.key !== 'Enter') return;
      event.preventDefault();
      navigateTaskToArchitecture(row.dataset.taskNavigate);
    });
  });
}

function selectTaskContext(taskId) {
  state.selectedTaskId = taskId;
  state.selectedProposalId = null;
  state.selectedComponentId = null;
  renderTasks();
  updateInstructionContext();
  setTimeout(() => document.querySelector(`[data-task-select="${CSS.escape(taskId)}"]`)?.focus(), 0);
}

async function navigateTaskToArchitecture(taskId) {
  const task = state.tasks.find((item) => item.id === taskId);
  if (!task?.related_component) return false;
  const node = findArchitectureNode(task.related_component);
  if (!node) {
    toast(`Architecture component not found for ${task.title}.`, true);
    return false;
  }
  switchView('architecture');
  const parentScopeComponentId = findArchitectureParentId(node.id);
  const opened = await navigateGraphScope(parentScopeComponentId ?? null, {focusComponentId:node.id});
  if (!opened || !diagramNodeByComponentId(node.id)) return false;
  state.selectedComponentId = node.id;
  state.graphFocusMode = 'connected';
  renderGraph();
  setTimeout(() => document.querySelector(`[data-component="${CSS.escape(node.id)}"]`)?.focus(), 0);
  return true;
}

function taskRow(t, selectable = false) {
  const selected = selectable && state.selectedTaskId === t.id;
  const updating = state.taskUpdating.has(t.id);
  const action = WEBMCP_AGENT_MODE ? '' : t.status === 'TODO'
    ? `<button data-task-action="start" data-task-id="${escapeHtml(t.id)}" ${updating ? 'disabled' : ''}>${updating ? 'Starting…' : 'Start task'}</button>`
    : t.status === 'IN_PROGRESS'
      ? `<button data-task-action="done" data-task-id="${escapeHtml(t.id)}" ${updating ? 'disabled' : ''}>${updating ? 'Saving…' : 'Mark done'}</button>`
      : '';
  const content = selectable
    ? `<button class="task-context-button" type="button" data-task-select="${escapeHtml(t.id)}" aria-pressed="${selected}" aria-label="Use ${escapeHtml(t.title)} as Agent context"><strong>${escapeHtml(t.title)}</strong><p>${escapeHtml(t.description || `${t.owner} · ${t.source}`)}</p></button>${action}`
    : `<div><strong>${escapeHtml(t.title)}</strong><p>${escapeHtml(t.description || `${t.owner} · ${t.source}`)}</p>${action}</div>`;
  const navigable = selectable && Boolean(t.related_component);
  const navigationAttrs = navigable ? ` data-task-navigate="${escapeHtml(t.id)}" tabindex="0" aria-label="${escapeHtml(`Task ${t.title}. Double-click or press Enter to open its architecture component.`)}" title="Double-click to open related architecture component"` : '';
  return `<div class="task-row${selected ? ' context-selected' : ''}${navigable ? ' is-architecture-linked' : ''}"${navigationAttrs}><i class="status-dot ${statusClass(t.status)}"></i><div>${content}</div><span class="status-pill ${t.status}">${t.status.replace('_', ' ')}</span></div>`;
}

async function updateTask(taskId, action) {
  const task = state.tasks.find((t) => t.id === taskId);
  if (!task || state.taskUpdating.has(taskId)) return;
  const status = action === 'done' ? 'DONE' : 'IN_PROGRESS';
  state.taskUpdating.add(taskId);
  renderTasks();
  try {
    await sendEvent(
      'TASK_UPDATED',
      {task_id: task.id, title: task.title, status, message: `Task "${task.title}" changed to ${status}. Treat this as observed human project state.`},
      status === 'DONE' ? 'Saving completed task…' : 'Starting task…',
    );
  } finally {
    state.taskUpdating.delete(taskId);
    renderTasks();
  }
}

function renderProposals() {
  const pending = state.proposals.filter((p) => p.status === 'PENDING');
  $('overviewAttention').innerHTML = pending.length
    ? `<div class="attention-card"><strong>${escapeHtml(pending[0].reason)}</strong><p>${escapeHtml(pending[0].observed_change)}</p><div class="actions"><button class="btn secondary" data-proposal="reject" data-id="${escapeHtml(pending[0].id)}">Keep current</button><button class="btn primary" data-open-proposal="${escapeHtml(pending[0].id)}">Review change</button></div></div>`
    : '<p>No pending architecture decision. The agent can maintain task/status state without asking you to approve normal aligned updates.</p>';
  const proposalList = $('proposalList');
  if (proposalList) proposalList.innerHTML = state.proposals.length
    ? state.proposals.map((p) => `<article class="proposal-card${state.selectedProposalId === p.id ? ' context-selected' : ''}" data-proposal-card="${escapeHtml(p.id)}"><div class="proposal-head"><div><small>${p.status}</small><h3>${escapeHtml(p.reason)}</h3></div><span class="status-pill ${p.status}">${p.status}</span></div><p>${escapeHtml(p.observed_change)}</p><button class="proposal-context-button" type="button" data-proposal-select="${escapeHtml(p.id)}" aria-pressed="${state.selectedProposalId === p.id}">Use this proposal as Agent context</button><div class="meta"><div><small>EVIDENCE</small><p>${p.evidence.map(escapeHtml).join('<br>')}</p></div><div><small>IMPACT</small><p>${escapeHtml(p.impact)}</p></div></div>${p.status === 'PENDING' ? `<div class="actions"><button class="btn secondary" data-proposal="reject" data-id="${escapeHtml(p.id)}">Keep current</button><button class="btn primary" data-proposal="accept" data-id="${escapeHtml(p.id)}">Accept proposed change</button></div>` : ''}</article>`).join('')
    : '<article class="panel"><h3>No architecture review needed</h3><p class="muted">Normal aligned project updates stay ambient and do not interrupt the human.</p></article>';
  wireGoButtons();
  document.querySelectorAll('[data-proposal]').forEach((btn) => btn.addEventListener('click', () => decideProposal(btn.dataset.id, btn.dataset.proposal)));
  document.querySelectorAll('[data-open-proposal]').forEach((button) => button.addEventListener('click', () => openAttentionItem('proposal', button.dataset.openProposal)));
  document.querySelectorAll('[data-proposal-select]').forEach((button) => button.addEventListener('click', () => {
    state.selectedProposalId = button.dataset.proposalSelect;
    state.selectedTaskId = null;
    state.selectedComponentId = null;
    renderProposals();
    updateInstructionContext();
    setTimeout(() => document.querySelector(`[data-proposal-select="${CSS.escape(state.selectedProposalId)}"]`)?.focus(), 0);
  }));
}

async function decideProposal(id, decision) {
  try {
    setWorking(true);
    await api(`/projects/${state.projectId}/architecture/proposals/${id}/${decision}`, {method: 'POST'});
    toast(decision === 'accept' ? 'Architecture change accepted.' : 'Proposal rejected; current architecture preserved.');
    await refresh();
    if ($('proposalReviewDialog').open) $('proposalReviewDialog').close();
  } catch (err) {
    toast(err.message, true);
  } finally {
    setWorking(false);
  }
}

function flattenArchitectureNodes(nodes = state.architecture?.components || []) {
  const flat = [];
  const visit = (items) => items.forEach((node) => {
    flat.push(node);
    visit(node.children || []);
  });
  visit(nodes);
  return flat;
}

function findArchitectureNode(id) {
  if (!id) return null;
  return flattenArchitectureNodes().find((node) => node.id === id) || null;
}

function findArchitectureParentId(id, nodes = state.architecture?.components || [], parentId = null) {
  for (const node of nodes) {
    if (node.id === id) return parentId;
    const nestedParent = findArchitectureParentId(id, node.children || [], node.id);
    if (nestedParent !== undefined) return nestedParent;
  }
  return undefined;
}

function descendantArchitectureIds(node) {
  const ids = [];
  const visit = (item) => {
    ids.push(item.id);
    (item.children || []).forEach(visit);
  };
  if (node) visit(node);
  return ids;
}

function architectureHealth(node) {
  const ids = new Set(descendantArchitectureIds(node));
  const tasks = state.tasks.filter((task) => task.related_component && ids.has(task.related_component));
  const blockedTasks = tasks.filter((task) => task.status === 'BLOCKED');
  const activeTasks = tasks.filter((task) => task.status === 'IN_PROGRESS');
  const badNodes = flattenArchitectureNodes([node]).filter((item) => /BLOCKED|DRIFT|ERROR|DEGRADED|MISMATCH/i.test(item.status || ''));
  const pendingReviews = state.proposals.filter((proposal) => {
    if (proposal.status !== 'PENDING') return false;
    const affected = proposal.affected_components || [];
    const changed = (proposal.proposed_changes || []).map((change) => change.component_id).filter(Boolean);
    return [...affected, ...changed].some((id) => ids.has(id));
  });

  if (blockedTasks.length || badNodes.length) {
    const parts = [];
    if (blockedTasks.length) parts.push(`${blockedTasks.length} blocked task${blockedTasks.length === 1 ? '' : 's'}`);
    if (badNodes.length) parts.push(`${badNodes.length} unhealthy node${badNodes.length === 1 ? '' : 's'}`);
    if (pendingReviews.length) parts.push(`${pendingReviews.length} review${pendingReviews.length === 1 ? '' : 's'}`);
    return {key: 'blocked', label: 'Blocked', detail: parts.join(' · '), needsAttention: true, tasks, blockedTasks, activeTasks, pendingReviews};
  }
  if (pendingReviews.length) {
    return {key: 'review', label: 'Needs review', detail: `${pendingReviews.length} architecture decision${pendingReviews.length === 1 ? '' : 's'} waiting for you`, needsAttention: true, tasks, blockedTasks, activeTasks, pendingReviews};
  }
  if (activeTasks.length) {
    return {key: 'active', label: 'Active', detail: `${activeTasks.length} task${activeTasks.length === 1 ? '' : 's'} in progress · no action needed`, needsAttention: false, tasks, blockedTasks, activeTasks, pendingReviews};
  }
  return {key: 'healthy', label: 'Healthy', detail: 'Aligned · no action needed', needsAttention: false, tasks, blockedTasks, activeTasks, pendingReviews};
}

function healthVisual(health, base) {
  if (health.key === 'blocked') return {fill:'#FFF1F0', stroke:'#D92D20', accent:'#B42318', tag:'#FEE4E2'};
  if (health.key === 'review') return {fill:'#FFF7E8', stroke:'#D98B34', accent:'#A15C12', tag:'#FCE8C5'};
  return base;
}

function diagramNodeByComponentId(componentId) {
  return (state.diagram?.nodes || []).find((node) => node.component_id === componentId) || null;
}

function diagramNodeById(nodeId) {
  return (state.diagram?.nodes || []).find((node) => node.id === nodeId) || null;
}

function diagramNodeHealth(node) {
  const value = node?.status?.health || 'UNKNOWN';
  const visual = {
    BLOCKED: {key:'blocked', label:'Blocked', needsAttention:true},
    CHANGE_PENDING: {key:'review', label:'Review', needsAttention:true},
    IN_PROGRESS: {key:'active', label:'Active', needsAttention:false},
    DONE: {key:'healthy', label:'Done', needsAttention:false},
    TODO: {key:'planned', label:'Todo', needsAttention:false},
    PLANNED: {key:'planned', label:'Planned', needsAttention:false},
    UNKNOWN: {key:'planned', label:'Unknown', needsAttention:false},
  }[value] || {key:'planned', label:String(value), needsAttention:false};
  return {...visual, detail:(node?.supporting_text || []).join(' · ') || node?.status?.canonical_status || 'No additional status evidence.'};
}

function wrapGraphText(text, maxChars, maxLines = 2) {
  const source = String(text || '').trim();
  if (!source || maxChars < 2 || maxLines < 1) return [];
  const words = source.split(/\s+/).filter(Boolean);
  const lines = [];
  let line = '';
  let consumed = 0;
  const fitWord = (word) => word.length <= maxChars ? word : `${word.slice(0, Math.max(1, maxChars - 1))}…`;
  for (const rawWord of words) {
    if (lines.length >= maxLines) break;
    const word = fitWord(rawWord);
    const next = line ? `${line} ${word}` : word;
    if (line && next.length > maxChars) {
      lines.push(line);
      if (lines.length >= maxLines) break;
      line = word;
    } else {
      line = next;
      consumed += 1;
    }
  }
  if (line && lines.length < maxLines) lines.push(line);
  const rendered = lines.join(' ').replace(/…/g, '');
  if ((consumed < words.length || rendered.length < source.length - 2) && lines.length) {
    const last = lines.length - 1;
    const base = lines[last].replace(/[.…]+$/, '');
    lines[last] = `${base.slice(0, Math.max(1, maxChars - 1)).trimEnd()}…`;
  }
  return lines;
}

function graphNodeKindMarkup(node) {
  const label = `${String(node.semantic_kind || 'COMPONENT')} · ${node.semantic_type || 'component'}`;
  const maxChars = Math.max(12, Math.floor((node.width - 56) / 7.2));
  const line = wrapGraphText(label, maxChars, 1)[0] || 'COMPONENT';
  return `<text class="node-kind" x="${node.x+18}" y="${node.y+25}">${escapeHtml(line)}</text>`;
}

function graphFocusState(selectedNode, projectedEdges = state.diagram?.edges || []) {
  if (!selectedNode || state.graphFocusMode === 'all') return null;
  const nodes = new Set([selectedNode.id]);
  const edges = new Set();
  if (state.graphFocusMode === 'connected') {
    projectedEdges.forEach((edge) => {
      if (edge.source === selectedNode.id || edge.target === selectedNode.id) { edges.add(edge.id); nodes.add(edge.source); nodes.add(edge.target); }
    });
    return {nodes, edges};
  }
  const upstream = state.graphFocusMode === 'upstream';
  const visited = new Set([selectedNode.id]);
  const queue = [selectedNode.id];
  while (queue.length) {
    const current = queue.shift();
    projectedEdges.forEach((edge) => {
      const matches = upstream ? edge.target === current : edge.source === current;
      if (!matches) return;
      const peer = upstream ? edge.source : edge.target;
      edges.add(edge.id); nodes.add(peer);
      if (!visited.has(peer)) { visited.add(peer); queue.push(peer); }
    });
  }
  return {nodes, edges};
}

function graphScopeTrail(diagram = state.diagram) {
  const scope = diagram?.scope;
  if (!scope) return [];
  const trail = [...(scope.ancestorPath || [])];
  if (scope.componentId && !trail.some((item) => item.componentId === scope.componentId)) trail.push({componentId:scope.componentId,nodeId:scope.nodeId,label:scope.label});
  return trail;
}

function parentGraphScopeComponentId(diagram = state.diagram) {
  const scope = diagram?.scope;
  if (!scope?.componentId) return null;
  const trail = graphScopeTrail(diagram);
  const index = trail.findIndex((item) => item.componentId === scope.componentId);
  return index > 0 ? trail[index - 1].componentId : null;
}

function nextReadingModeForScope(currentMode, nextScopeComponentId) {
  void currentMode;
  void nextScopeComponentId;
  return 'MAP';
}

function graphNodeAction(node) {
  return node?.projectionRole === 'PRIMARY' && node.childCount > 0 ? 'drill' : 'inspect';
}

async function navigateGraphScope(scopeComponentId, {focusComponentId = state.scopeComponentId, loader = loadArchitectureDiagram, render = renderGraph, notify = toast} = {}) {
  if (!state.projectId || !state.architecture) return false;
  const targetScope = scopeComponentId || null;
  try {
    const nextMode = nextReadingModeForScope(state.readingMode, targetScope);
    const nextDiagram = await loader(state.projectId, state.architecture, targetScope, nextMode);
    if (!nextDiagram) throw new Error('Scoped diagram is unavailable.');
    state.scopeComponentId = targetScope;
    state.readingMode = nextMode;
    state.selectedComponentId = null;
    state.graphFocusMode = 'all';
    state.diagram = nextDiagram;
    state.diagramError = null;
    render();
    if (focusComponentId && typeof document !== 'undefined') setTimeout(() => document.querySelector(`[data-component="${CSS.escape(focusComponentId)}"]`)?.focus(), 0);
    return true;
  } catch (err) {
    state.diagramError = err?.message || String(err);
    notify(`Could not open that architecture scope. ${state.diagramError}`, true);
    return false;
  }
}

async function setGraphReadingMode(mode, {loader = loadArchitectureDiagram, render = renderGraph, notify = toast} = {}) {
  if (!['MAP','READ','FULL'].includes(mode)) return false;
  if (mode === state.readingMode) return true;
  try {
    const nextDiagram = await loader(state.projectId, state.architecture, state.scopeComponentId, mode);
    if (!nextDiagram) throw new Error('Diagram is unavailable for this reading mode.');
    state.diagram = nextDiagram;
    state.readingMode = mode;
    state.selectedComponentId = null;
    state.graphFocusMode = 'all';
    render();
    return true;
  } catch (err) {
    notify(err?.message || String(err), true);
    return false;
  }
}

async function activateGraphNode(node, {navigate = navigateGraphScope, render = renderGraph} = {}) {
  if (!node) return false;
  state.selectedComponentId = node.component_id;
  state.graphFocusMode = 'connected';
  render();
  return true;
}

async function drillGraphNode(node, {navigate = navigateGraphScope} = {}) {
  if (!node || graphNodeAction(node) !== 'drill') return false;
  return navigate(node.component_id, {focusComponentId:node.component_id});
}

function graphBreadcrumbMarkup(diagram) {
  const scope = diagram?.scope || {componentId:null};
  const trail = graphScopeTrail(diagram);
  const overview = scope.componentId ? '<button type="button" data-scope-target="">Overview</button>' : '<span aria-current="page">Overview</span>';
  const rest = trail.map((item) => item.componentId === scope.componentId ? `<span aria-current="page">${escapeHtml(item.label)}</span>` : `<button type="button" data-scope-target="${escapeHtml(item.componentId)}">${escapeHtml(item.label)}</button>`).join('<span class="graph-crumb-sep">/</span>');
  return `<nav class="graph-breadcrumb" aria-label="Architecture scope">${overview}${rest ? `<span class="graph-crumb-sep">/</span>${rest}` : ''}</nav>`;
}

function graphScopeToolbar(diagram) {
  const scope = diagram.scope || {componentId:null,label:'Overview',directRelationships:[]};
  const back = scope.componentId ? '<button class="graph-back" type="button" data-graph-back>← Back</button>' : '';
  const modes = ['MAP','READ','FULL'].map((mode) => `<button type="button" data-reading-mode="${mode}" class="${state.readingMode === mode ? 'active' : ''}" aria-pressed="${state.readingMode === mode}">${mode}</button>`).join('');
  const directCount = scope.directRelationships?.length || 0;
  return `<div class="graph-scope-bar"><div class="graph-scope-copy">${graphBreadcrumbMarkup(diagram)}<div><strong>${escapeHtml(scope.label || 'Overview')}</strong><span>${scope.componentId ? 'Canonical subsystem scope' : 'Canonical root system map'}${directCount ? ` · ${directCount} direct boundary relationship${directCount === 1 ? '' : 's'}` : ''}</span></div></div><div class="graph-scope-actions">${back}<div class="graph-reading-modes" role="group" aria-label="Graph information level">${modes}</div></div></div>`;
}

function setArchitectureGraphKind(kind, {render = renderGraph} = {}) {
  if (!['living','code'].includes(kind)) return false;
  state.architectureGraphKind = kind;
  state.selectedComponentId = null;
  state.selectedCodeNodeId = null;
  state.graphFocusMode = 'all';
  render();
  updateInstructionContext();
  return true;
}

function renderArchitectureChrome() {
  const codeMode = state.architectureGraphKind === 'code';
  const graphSide = document.querySelector('.graph-side');
  if (graphSide) {
    graphSide.dataset.graphKind = state.architectureGraphKind;
    graphSide.dataset.readingMode = state.readingMode;
  }
  document.querySelectorAll('[data-architecture-graph-kind]').forEach((button) => {
    const active = button.dataset.architectureGraphKind === state.architectureGraphKind;
    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', String(active));
  });
  if ($('architectureViewTitle')) $('architectureViewTitle').textContent = codeMode ? 'Code Graph' : 'Living Graph';
  if ($('architectureViewSubtitle')) $('architectureViewSubtitle').textContent = codeMode
    ? 'Implementation evidence generated from source inspected at one exact GitHub commit.'
    : 'Start with the system health map, then open canonical subsystems one level at a time.';
  if ($('graphPanelTitle')) $('graphPanelTitle').textContent = codeMode ? 'Implementation map' : 'System health map';
  if ($('graphPanelSubtitle')) $('graphPanelSubtitle').textContent = codeMode
    ? 'Derived evidence only. This graph never overwrites accepted Living Architecture.'
    : 'Red or amber areas need attention. Healthy areas require no inspection.';
  if ($('graphHealthLegend')) $('graphHealthLegend').classList.toggle('hidden', codeMode);
  if ($('graphEvidenceTitle')) $('graphEvidenceTitle').textContent = codeMode ? 'Source evidence' : 'Why this status?';
  if ($('graphDecisionTitle')) $('graphDecisionTitle').textContent = codeMode ? 'Repository snapshot' : 'Architecture decisions';
  if ($('graphRiskTitle')) $('graphRiskTitle').textContent = codeMode ? 'Evidence boundary' : 'Risks & assumptions';
  if (state.currentView === 'architecture') {
    $('pageTitle').textContent = codeMode ? 'Code Architecture' : 'Living Architecture';
    $('pageSubtitle').textContent = codeMode
      ? 'Revision-pinned implementation evidence from connected repository analysis.'
      : 'Human-approved design intent with backend-authored hierarchical drilldown.';
  }
}

function codeNodeById(nodeId) {
  return (state.codeDiagram?.nodes || []).find((node) => node.id === nodeId) || null;
}

function codeEvidenceLink(source) {
  const label = `${source.path || 'source'}:${source.line_start || '?'}${source.line_end && source.line_end !== source.line_start ? `-${source.line_end}` : ''}`;
  return `<a class="code-evidence-link" href="${escapeHtml(source.href)}" target="_blank" rel="noreferrer">${escapeHtml(label)} ↗</a>`;
}

function renderCodeSelectedNode() {
  const diagram = state.codeDiagram;
  const node = codeNodeById(state.selectedCodeNodeId);
  if (!diagram || !node) {
    $('selectedNode').innerHTML = diagram
      ? `<small>IMPLEMENTATION EVIDENCE</small><h3>${escapeHtml(diagram.repository.slug)}</h3><p>Select a code node to inspect the exact source excerpts that support it.</p>`
      : '<small>IMPLEMENTATION EVIDENCE</small><h3>No code snapshot yet</h3><p>Connect GitHub, let the agent inspect an exact commit, then publish a revision-pinned Code Architecture snapshot.</p>';
    $('nodeEvidence').innerHTML = diagram
      ? `<p><strong>Revision pinned</strong></p><p class="muted"><code>${escapeHtml(diagram.repository.revision)}</code></p><p><strong>Evidence mode</strong></p><p class="muted">${escapeHtml(diagram.evidenceVerification.mode || 'REVISION_PINNED_AGENT_SUPPLIED')}</p>`
      : '<p><strong>How this is created</strong></p><p class="muted">The agent must first inspect the connected GitHub repository, resolve a full commit SHA, and cite repository-relative source lines. File names alone are not accepted as architecture evidence.</p>';
    return;
  }
  const incoming = diagram.edges.filter((edge) => edge.target === node.id);
  const outgoing = diagram.edges.filter((edge) => edge.source === node.id);
  const connection = (edge, direction) => {
    const peer = codeNodeById(direction === 'in' ? edge.source : edge.target);
    return `<li><strong>${direction === 'in' ? 'From' : 'To'} ${escapeHtml(peer?.label || (direction === 'in' ? edge.source : edge.target))}</strong><span>${escapeHtml(edge.semantic_type || edge.label || 'relationship')}${edge.supporting_text ? ` · ${escapeHtml(edge.supporting_text)}` : ''}</span></li>`;
  };
  $('selectedNode').innerHTML = `<small>CODE COMPONENT · ${escapeHtml(String(node.semantic_kind || 'COMPONENT'))}</small><div class="selected-node-title"><h3>${escapeHtml(node.label)}</h3><span class="code-evidence-pill">PINNED</span></div><p>${escapeHtml(node.responsibility)}</p><div class="component-children-summary"><strong>Implementation facts</strong><span>Snapshot ID · ${escapeHtml(node.id)}</span><span>Children · ${node.childCount}</span><span>Commit · ${escapeHtml(diagram.repository.revision.slice(0, 12))}</span></div><div class="component-connections">${incoming.length || outgoing.length ? `<ul>${incoming.map((edge) => connection(edge, 'in')).join('')}${outgoing.map((edge) => connection(edge, 'out')).join('')}</ul>` : '<p class="muted">No evidence-backed runtime relationship is attached to this node.</p>'}</div>`;
  $('nodeEvidence').innerHTML = node.sources.length
    ? node.sources.map((source) => `<div class="code-evidence-item">${codeEvidenceLink(source)}${source.symbol ? `<span class="code-evidence-symbol">${escapeHtml(source.symbol)}</span>` : ''}<pre>${escapeHtml(source.excerpt || '')}</pre></div>`).join('')
    : '<p class="muted">No source excerpt is attached to this node.</p>';
}

function renderCodeGraph() {
  const canvas = $('graphCanvas');
  const diagram = state.codeDiagram;
  if (!diagram) {
    $('graphVersion').textContent = 'No snapshot';
    $('graphReviewState').textContent = 'Awaiting GitHub evidence';
    canvas.innerHTML = '<div class="graph-empty code-graph-empty"><div><strong>No Code Architecture snapshot yet</strong><p class="muted">Connect GitHub and ask the agent to inspect the repository at an exact commit, then publish evidence-backed implementation architecture.</p></div></div>';
    renderCodeSelectedNode();
    $('decisionList').innerHTML = '<p class="muted">No repository snapshot has been published for this project.</p>';
    $('riskList').innerHTML = '<p class="muted">Code Architecture is derived evidence. It never becomes accepted Living Architecture without a separate human-reviewed architecture proposal.</p>';
    return;
  }
  const selected = codeNodeById(state.selectedCodeNodeId);
  if (state.selectedCodeNodeId && !selected) state.selectedCodeNodeId = null;
  const byId = new Map(diagram.nodes.map((node) => [node.id, node]));
  const hierarchy = diagram.nodes.map((node) => {
    if (!node.parent_id) return '';
    const parent = byId.get(node.parent_id);
    if (!parent) return '';
    return `<line class="graph-hierarchy code-hierarchy" x1="${parent.x+parent.width/2}" y1="${parent.y+parent.height/2}" x2="${node.x+node.width/2}" y2="${node.y+node.height/2}"/>`;
  }).join('');
  const edges = diagram.edges.map((edge) => {
    const anchor = edge.points[Math.floor((edge.points.length - 1) / 2)];
    return `<g class="graph-edge code-edge" data-code-edge="${escapeHtml(edge.id)}"><path d="${graphPathData(edge.points)}" marker-end="url(#code-arrow)"/><text x="${anchor.x}" y="${anchor.y-7}" text-anchor="middle">${escapeHtml(edge.semantic_type || edge.label || '')}</text></g>`;
  }).join('');
  const nodes = diagram.nodes.map((node) => {
    const active = state.selectedCodeNodeId === node.id;
    const names = wrapGraphText(node.label, Math.max(13, Math.floor((node.width-40)/8.2)), 2).map((line,index) => `<text class="node-name" x="${node.x+18}" y="${node.y+55+index*17}">${escapeHtml(line)}</text>`).join('');
    const responsibility = wrapGraphText(node.responsibility, Math.max(18, Math.floor((node.width-40)/6.8)), 2).map((line,index) => `<text class="node-responsibility" x="${node.x+18}" y="${node.y+96+index*14}">${escapeHtml(line)}</text>`).join('');
    return `<g class="node-card code-node-card${active ? ' selected' : ''}" data-code-node="${escapeHtml(node.id)}" role="button" tabindex="0" aria-label="Inspect code evidence for ${escapeHtml(node.label)}"><rect class="node-surface" x="${node.x}" y="${node.y}" width="${node.width}" height="${node.height}" rx="16"/>${graphNodeKindMarkup(node)}${names}${responsibility}<text class="code-node-evidence-count" x="${node.x+18}" y="${node.y+node.height-13}">${node.sources.length} source${node.sources.length === 1 ? '' : 's'} · depth ${node.depth}</text></g>`;
  }).join('');
  $('graphVersion').textContent = `@${diagram.repository.revision.slice(0, 8)}`;
  $('graphReviewState').textContent = `${diagram.nodes.length} implementation node${diagram.nodes.length === 1 ? '' : 's'}`;
  const meta = `<div class="graph-meta code-graph-meta"><span>${escapeHtml(diagram.repository.slug)}</span><span>${diagram.nodes.length} nodes</span><span>${diagram.edges.length} evidence-backed relationship${diagram.edges.length === 1 ? '' : 's'}</span><span>Exact commit · ${escapeHtml(diagram.repository.revision.slice(0, 12))}</span><span class="graph-meta-ok">Living architecture unchanged</span></div>`;
  canvas.innerHTML = `<div class="code-snapshot-bar"><div><strong>${escapeHtml(diagram.repository.slug)}</strong><span>Implementation evidence at exact Git revision</span></div><a href="${escapeHtml(`${diagram.repository.url}/tree/${diagram.repository.revision}`)}" target="_blank" rel="noreferrer"><code>${escapeHtml(diagram.repository.revision)}</code> ↗</a></div><div class="graph-stage">${meta}<svg class="living-graph-svg code-graph-svg" viewBox="0 0 ${diagram.width} ${diagram.height}" role="img" aria-label="Revision-pinned code architecture graph"><defs><marker id="code-arrow" markerWidth="9" markerHeight="9" refX="8" refY="4.5" orient="auto"><path d="M1 1 L8 4.5 L1 8 Z"/></marker></defs>${hierarchy}${edges}${nodes}</svg></div>`;
  const activate = (element) => {
    state.selectedCodeNodeId = element.dataset.codeNode;
    renderCodeGraph();
    setTimeout(() => document.querySelector(`[data-code-node="${CSS.escape(state.selectedCodeNodeId)}"]`)?.focus(), 0);
  };
  canvas.querySelectorAll('[data-code-node]').forEach((element) => {
    element.addEventListener('click', () => activate(element));
    element.addEventListener('keydown', (event) => {
      if (event.key !== 'Enter' && event.key !== ' ') return;
      event.preventDefault();
      activate(element);
    });
  });
  renderCodeSelectedNode();
  $('decisionList').innerHTML = `<ul><li><strong>${escapeHtml(diagram.repository.slug)}</strong></li><li><code>${escapeHtml(diagram.repository.revision)}</code></li><li>${escapeHtml(diagram.summary || 'No implementation summary.')}</li></ul>`;
  $('riskList').innerHTML = `<ul><li>Classification: ${escapeHtml(diagram.classification || 'IMPLEMENTATION_EVIDENCE')}</li><li>Canonical state mutated: ${diagram.canonicalStateMutated ? 'YES' : 'NO'}</li><li>${escapeHtml(diagram.evidenceVerification.note || 'Evidence is pinned to the supplied Git revision.')}</li></ul>`;
}

function graphDisplayModel(diagram) {
  const scoped = Boolean(diagram?.scope?.componentId);
  const nodes = scoped
    ? (diagram?.nodes || []).filter((node) => node.projectionRole !== 'SCOPE')
    : (diagram?.nodes || []);
  const nodeIds = new Set(nodes.map((node) => node.id));
  const edges = (diagram?.edges || []).filter((edge) => nodeIds.has(edge.source) && nodeIds.has(edge.target));
  return {scoped, nodes, edges};
}

function graphVisualConnections(edges = [], nodes = []) {
  const nodeById = new Map(nodes.map((node) => [node.id, node]));
  const grouped = new Map();
  edges.forEach((edge) => {
    const pair = [String(edge.source), String(edge.target)].sort();
    // JSON tuple encoding is collision-free for arbitrary component ids; a
    // delimiter such as "::" can merge unrelated pairs when ids contain it.
    const key = JSON.stringify(pair);
    if (!grouped.has(key)) grouped.set(key, []);
    grouped.get(key).push(edge);
  });
  return [...grouped.entries()].map(([key, members]) => {
    const ordered = [...members].sort((left, right) => {
      const leftBackbone = String(left.layout_role || 'BACKBONE') === 'BACKBONE' ? 0 : 1;
      const rightBackbone = String(right.layout_role || 'BACKBONE') === 'BACKBONE' ? 0 : 1;
      if (leftBackbone !== rightBackbone) return leftBackbone - rightBackbone;
      const leftSource = nodeById.get(left.source), leftTarget = nodeById.get(left.target);
      const rightSource = nodeById.get(right.source), rightTarget = nodeById.get(right.target);
      const leftForward = (leftSource?.x ?? 0) <= (leftTarget?.x ?? 0) ? 0 : 1;
      const rightForward = (rightSource?.x ?? 0) <= (rightTarget?.x ?? 0) ? 0 : 1;
      if (leftForward !== rightForward) return leftForward - rightForward;
      const leftProvenance = Array.isArray(left.provenance) ? left.provenance.length : 0;
      const rightProvenance = Array.isArray(right.provenance) ? right.provenance.length : 0;
      if (leftProvenance !== rightProvenance) return rightProvenance - leftProvenance;
      return String(left.id).localeCompare(String(right.id));
    });
    const primary = ordered[0];
    const directions = new Set(members.map((edge) => `${edge.source}->${edge.target}`));
    return {
      ...primary,
      id: `visual:${key}`,
      label: members.length > 1 ? `${members.length} relationships` : (primary.label || primary.semantic_type || ''),
      layout_role: 'BACKBONE',
      memberIds: members.map((edge) => edge.id),
      bidirectional: directions.size > 1,
    };
  }).sort((left, right) => String(left.id).localeCompare(String(right.id)));
}

function graphDisplayViewBox(diagram, nodes, edges) {
  if (!diagram?.scope?.componentId || !nodes.length) return `0 0 ${diagram.width} ${diagram.height}`;
  const xs = [];
  const ys = [];
  nodes.forEach((node) => {
    xs.push(node.x, node.x + node.width);
    ys.push(node.y, node.y + node.height);
  });
  edges.forEach((edge) => (edge.points || []).forEach((point) => {
    xs.push(point.x);
    ys.push(point.y);
  }));
  const padding = 34;
  let minX = Math.min(...xs) - padding;
  let maxX = Math.max(...xs) + padding;
  let minY = Math.min(...ys) - padding;
  let maxY = Math.max(...ys) + padding;
  const minWidth = Math.min(Number(diagram.width) || 0, 520);
  const minHeight = Math.min(Number(diagram.height) || 0, 320);
  if (maxX - minX < minWidth) {
    const delta = (minWidth - (maxX - minX)) / 2;
    minX -= delta;
    maxX += delta;
  }
  if (maxY - minY < minHeight) {
    const delta = (minHeight - (maxY - minY)) / 2;
    minY -= delta;
    maxY += delta;
  }
  return `${minX} ${minY} ${maxX - minX} ${maxY - minY}`;
}

function graphRectOverlaps(left, right, padding = 0) {
  return !(
    left.x + left.width + padding <= right.x - padding
    || right.x + right.width + padding <= left.x - padding
    || left.y + left.height + padding <= right.y - padding
    || right.y + right.height + padding <= left.y - padding
  );
}

function graphSegmentHitsRect(start, end, rect, padding = 0) {
  const left = rect.x - padding;
  const right = rect.x + rect.width + padding;
  const top = rect.y - padding;
  const bottom = rect.y + rect.height + padding;
  if (start.x === end.x) {
    const low = Math.min(start.y, end.y), high = Math.max(start.y, end.y);
    return start.x > left && start.x < right && Math.max(low, top) < Math.min(high, bottom);
  }
  if (start.y === end.y) {
    const low = Math.min(start.x, end.x), high = Math.max(start.x, end.x);
    return start.y > top && start.y < bottom && Math.max(low, left) < Math.min(high, right);
  }
  return false;
}

let graphLabelMeasureContext = null;

function graphMeasuredLabelWidth(label) {
  if (!graphLabelMeasureContext) graphLabelMeasureContext = document.createElement('canvas').getContext('2d');
  const family = getComputedStyle(document.body).fontFamily || 'Arial, sans-serif';
  graphLabelMeasureContext.font = `750 9.5px ${family}`;
  return Math.max(42, Math.ceil(graphLabelMeasureContext.measureText(label).width) + 12);
}

function graphEdgeLabelPlacements(edges, nodes) {
  const occupied = [];
  const result = new Map();
  const nodeRects = nodes.map((node) => ({x:node.x, y:node.y, width:node.width, height:node.height}));
  const minNodeY = nodeRects.length ? Math.min(...nodeRects.map((rect) => rect.y)) : 48;
  const maxNodeBottom = nodeRects.length ? Math.max(...nodeRects.map((rect) => rect.y + rect.height)) : 320;
  const segmentsByEdge = new Map(edges.map((edge) => [edge.id, (edge.points || []).slice(0,-1).map((start,index) => {
    const end = edge.points[index+1];
    return {start,end,index,length:Math.abs(end.x-start.x)+Math.abs(end.y-start.y),horizontal:start.y===end.y};
  })]));
  const fractions = [0.5, 0.75, 0.25, 0.88, 0.12, 0.65, 0.35];

  edges.forEach((edge) => {
    const label = String(edge.label || edge.semantic_type || '').trim();
    if (!label) return;
    const width = graphMeasuredLabelWidth(label);
    const height = 17;
    const target = edge.points[edge.points.length - 1];
    const arrowBox = {x:target.x-11, y:target.y-11, width:22, height:22};
    const candidates = [];
    const segments = [...(segmentsByEdge.get(edge.id) || [])].sort((a,b) => b.length-a.length || a.index-b.index);
    segments.forEach((segment) => {
      fractions.forEach((fraction) => {
        const baseX = segment.start.x + (segment.end.x-segment.start.x)*fraction;
        const baseY = segment.start.y + (segment.end.y-segment.start.y)*fraction;
        if (segment.horizontal && segment.length >= 24) {
          [-14, 22, -30, 38, -46, 54, -62, 70].forEach((offset) => {
            const baselineY = baseY + offset;
            candidates.push({segmentIndex:segment.index, x:baseX, y:baselineY, box:{x:baseX-width/2, y:baselineY-height+4, width, height}});
          });
        } else if (!segment.horizontal && segment.length >= 24) {
          [8, 16, 28, 42, 58].forEach((extra) => {
            const distance = width/2 + extra;
            [1,-1].forEach((direction) => {
              const x = baseX + direction*distance;
              const baselineY = baseY + 4;
              candidates.push({segmentIndex:segment.index, x, y:baselineY, box:{x:x-width/2, y:baselineY-height+4, width, height}});
            });
          });
        }
      });
    });

    // Dense short routes can have no local label slot at all. Keep a
    // deterministic overflow lane in the graph padding instead of hiding the
    // relationship or placing text on a node/edge.
    const routeXs = (edge.points || []).map((point) => point.x);
    const routeCenterX = routeXs.length ? (Math.min(...routeXs) + Math.max(...routeXs)) / 2 : 48;
    const laneStep = width + 14;
    const laneXs = [routeCenterX, routeCenterX-laneStep, routeCenterX+laneStep, routeCenterX-2*laneStep, routeCenterX+2*laneStep];
    const topBaseline = Math.max(18, minNodeY - 16);
    const bottomBaseline = maxNodeBottom + 28;
    [topBaseline, bottomBaseline].forEach((baselineY) => laneXs.forEach((x) => {
      candidates.push({segmentIndex:-1, x, y:baselineY, box:{x:x-width/2, y:baselineY-height+4, width, height}});
    }));

    const safe = (candidate, avoidOtherEdges = true) => {
      if (graphRectOverlaps(candidate.box, arrowBox, 3)) return false;
      if (nodeRects.some((rect) => graphRectOverlaps(candidate.box, rect, 4))) return false;
      if (occupied.some((rect) => graphRectOverlaps(candidate.box, rect, 4))) return false;
      const ownSegments = segmentsByEdge.get(edge.id) || [];
      if (ownSegments.some((segment) => graphSegmentHitsRect(segment.start, segment.end, candidate.box, 3))) return false;
      if (!avoidOtherEdges) return true;
      return !edges.some((other) => {
        if (other.id === edge.id) return false;
        return (segmentsByEdge.get(other.id) || []).some((segment) => graphSegmentHitsRect(segment.start, segment.end, candidate.box, 3));
      });
    };

    let selected = candidates.find((candidate) => safe(candidate, true));
    // Preserve complete READ/FULL semantics if a very dense scope has no wholly
    // edge-free label lane. Even this fallback still forbids nodes, other labels,
    // this edge, its elbows, and its arrowhead; acceptance will surface any
    // unrelated-edge collision instead of silently hiding the relationship.
    if (!selected) selected = candidates.find((candidate) => safe(candidate, false));
    if (selected) {
      occupied.push(selected.box);
      result.set(edge.id, selected);
    }
  });
  return result;
}

function renderGraph() {
  renderArchitectureChrome();
  if (state.architectureGraphKind === 'code') {
    renderCodeGraph();
    return;
  }
  const canvas = $('graphCanvas');
  if (!state.architecture?.components?.length) {
    canvas.innerHTML = '<div class="graph-empty"><div><strong>No architecture yet</strong><p class="muted">Architecture v1 has not completed.</p></div></div>';
    renderSelectedNode(); renderLists(); return;
  }
  if (!state.diagram) {
    canvas.innerHTML = `<div class="graph-empty"><div><strong>Positioned diagram unavailable</strong><p class="muted">${escapeHtml(state.diagramError || 'The backend has not published the scoped positioned Diagram View for this architecture version.')}</p></div></div>`;
    $('graphReviewState').textContent = 'Diagram unavailable'; renderSelectedNode(); renderLists(); return;
  }
  const diagram = state.diagram;
  const display = graphDisplayModel(diagram);
  const selected = diagramNodeByComponentId(state.selectedComponentId);
  if (state.selectedComponentId && !selected) state.selectedComponentId = null;
  const focus = graphFocusState(selected, display.edges);
  const nodeById = new Map(diagram.nodes.map((node) => [node.id,node]));
  const attentionNodes = display.nodes.filter((node) => diagramNodeHealth(node).needsAttention);
  const activeTaskCount = state.tasks.filter((task) => task.status === 'IN_PROGRESS').length;
  const hierarchy = display.scoped ? '' : diagram.nodes.map((node) => {
    if (!node.parent_id) return '';
    const parent=nodeById.get(node.parent_id); if (!parent) return '';
    return `<line class="graph-hierarchy" x1="${parent.x+parent.width/2}" y1="${parent.y+parent.height/2}" x2="${node.x+node.width/2}" y2="${node.y+node.height/2}"/>`;
  }).join('');
  const visibleEdges = graphVisualConnections(display.edges, display.nodes);
  const labelPlacements = graphEdgeLabelPlacements(visibleEdges, display.nodes);
  const edges = visibleEdges.map((edge) => {
    const highlighted=!focus || edge.memberIds.some((edgeId)=>focus.edges.has(edgeId)); const label=edge.label || edge.semantic_type || ''; const labelPlacement=labelPlacements.get(edge.id);
    const projectionKind=edge.memberIds.length > 1 ? 'merged' : String(edge.projection_kind || 'AUTHORED').toLowerCase();
    const sourcePoint=edge.points[0];
    const sourcePort=edge.bidirectional ? '' : `<circle class="graph-edge-source-port" cx="${sourcePoint.x}" cy="${sourcePoint.y}" r="2.25"/>`;
    const startMarker=edge.bidirectional ? ' marker-start="url(#arrow-backbone)"' : '';
    return `<g class="graph-edge projection-${escapeHtml(projectionKind)} layout-backbone${highlighted ? ' is-focused' : ' is-dimmed'}" data-edge="${escapeHtml(edge.id)}">${sourcePort}<path d="${graphPathData(edge.points)}"${startMarker} marker-end="url(#arrow-backbone)"/>${label && labelPlacement ? `<text class="graph-edge-label graph-detail-read" data-edge-label="${escapeHtml(edge.id)}" x="${labelPlacement.x}" y="${labelPlacement.y}" text-anchor="middle">${escapeHtml(label)}</text>` : ''}</g>`;
  }).join('');
  const nodes = display.nodes.map((node) => {
    const health=diagramNodeHealth(node), selectedNode=state.selectedComponentId===node.component_id, highlighted=!focus || focus.nodes.has(node.id);
    const names=wrapGraphText(node.label,Math.max(12,Math.floor((node.width-48)/8.8)),2).map((line,index)=>`<text class="node-name" x="${node.x+18}" y="${node.y+55+index*17}">${escapeHtml(line)}</text>`).join('');
    const responsibility=wrapGraphText(node.responsibility,Math.max(16,Math.floor((node.width-48)/7.4)),2).map((line,index)=>`<text class="node-responsibility graph-detail-read" x="${node.x+18}" y="${node.y+96+index*14}">${escapeHtml(line)}</text>`).join('');
    const drillable=graphNodeAction(node)==='drill', role=node.projectionRole || 'PRIMARY', action=drillable ? 'drill' : 'inspect';
    const cue=drillable ? `<g class="node-drill-action" aria-hidden="true"><rect x="${node.x+node.width-126}" y="${node.y+node.height-31}" width="110" height="22" rx="11"/><text class="node-drill-cue" x="${node.x+node.width-25}" y="${node.y+node.height-16}" text-anchor="end">OPEN · ${node.childCount} CHILD${node.childCount===1?'':'REN'} ›</text></g>` : role==='SCOPE' ? `<text class="node-scope-cue" x="${node.x+node.width-16}" y="${node.y+node.height-13}" text-anchor="end">CURRENT SCOPE</text>` : role==='CONTEXT' ? `<text class="node-context-cue" x="${node.x+node.width-16}" y="${node.y+node.height-13}" text-anchor="end">CONTEXT</text>` : '';
    return `<g class="node-card projection-${role.toLowerCase()} health-${health.key} is-${action}${selectedNode ? ' selected' : ''}${highlighted ? ' is-focused' : ' is-dimmed'}" data-node="${escapeHtml(node.id)}" data-component="${escapeHtml(node.component_id)}" data-child-count="${node.childCount}" data-projection-role="${role}" data-node-action="${action}" role="button" aria-label="${escapeHtml(drillable ? `Inspect ${node.label}; double click to open subsystem with ${node.childCount} child${node.childCount===1?'':'ren'}` : `Inspect ${node.label}`)}" tabindex="0"><rect class="node-surface" x="${node.x}" y="${node.y}" width="${node.width}" height="${node.height}" rx="16"/>${graphNodeKindMarkup(node)}<circle class="node-health-dot" cx="${node.x+node.width-20}" cy="${node.y+21}" r="4.5"/>${names}${responsibility}<text class="node-status graph-detail-full" x="${node.x+18}" y="${node.y+node.height-13}">${escapeHtml(health.label)} · depth ${node.depth}</text>${cue}</g>`;
  }).join('');
  $('graphReviewState').textContent=attentionNodes.length ? `${attentionNodes.length} node${attentionNodes.length===1?'':'s'} need attention` : 'All projected nodes aligned';
  const boundaryRelationshipCount = diagram.scope?.directRelationships?.length || 0;
  const collapsedConnectionMeta = visibleEdges.length !== display.edges.length
    ? ` · ${visibleEdges.length} visual connection${visibleEdges.length===1?'':'s'}`
    : '';
  const relationshipMeta = display.scoped
    ? `${display.edges.length} direct child relationship${display.edges.length===1?'':'s'}${collapsedConnectionMeta}`
    : `${display.edges.length} projected relationship${display.edges.length===1?'':'s'}${collapsedConnectionMeta}`;
  const scopeMeta = display.scoped && boundaryRelationshipCount
    ? `<span class="graph-meta-scope">${boundaryRelationshipCount} boundary relationship${boundaryRelationshipCount===1?'':'s'}${display.edges.length===0 ? ' · kept at scope boundary' : ''}</span>`
    : display.scoped && display.edges.length === 0
      ? '<span class="graph-meta-scope">No authored child-to-child links</span>'
      : '';
  const meta=`<div class="graph-meta"><span>${display.nodes.length} visible nodes</span><span>${relationshipMeta}</span>${scopeMeta}<span>${activeTaskCount} task${activeTaskCount===1?'':'s'} active</span><span>Accepted v${diagram.architectureVersion}</span>${attentionNodes.length ? `<span class="graph-meta-attention">${attentionNodes.length} need attention</span>` : '<span class="graph-meta-ok">No action needed</span>'}</div>`;
  const viewBox = graphDisplayViewBox(diagram, display.nodes, visibleEdges);
  canvas.innerHTML=`${graphScopeToolbar(diagram)}<div class="graph-stage" data-reading-mode="${state.readingMode}">${meta}<svg class="living-graph-svg" viewBox="${viewBox}" role="img" aria-label="Accepted scoped project architecture graph"><defs><marker id="arrow-backbone" markerUnits="userSpaceOnUse" markerWidth="7" markerHeight="7" viewBox="0 0 7 7" refX="6.35" refY="3.5" orient="auto-start-reverse" overflow="visible"><path d="M1.2 1.1 L5.8 3.5 L1.2 5.9" fill="none" stroke="var(--brand-deep)" stroke-width="1.35" stroke-linecap="round" stroke-linejoin="round"/></marker></defs>${hierarchy}${edges}${nodes}</svg></div>`;
  canvas.querySelectorAll('[data-reading-mode]').forEach((button)=>button.addEventListener('click',()=>setGraphReadingMode(button.dataset.readingMode)));
  canvas.querySelector('[data-graph-back]')?.addEventListener('click',()=>navigateGraphScope(parentGraphScopeComponentId(diagram),{focusComponentId:state.scopeComponentId}));
  canvas.querySelectorAll('[data-scope-target]').forEach((button)=>button.addEventListener('click',()=>navigateGraphScope(button.dataset.scopeTarget || null,{focusComponentId:state.scopeComponentId})));
  const focusNode=async(el)=>{ const node=diagramNodeById(el.dataset.node); await activateGraphNode(node); setTimeout(()=>document.querySelector(`[data-component="${CSS.escape(node.component_id)}"]`)?.focus(),0); };
  const drillNode=async(el)=>{ const node=diagramNodeById(el.dataset.node); await drillGraphNode(node); };
  canvas.querySelectorAll('[data-node]').forEach((el)=>{
    el.addEventListener('click',()=>{focusNode(el);});
    el.addEventListener('dblclick',(event)=>{ event.preventDefault(); drillNode(el); });
    el.addEventListener('keydown',(event)=>{
      if(event.key==='Enter' && event.shiftKey){event.preventDefault();drillNode(el);return;}
      if(event.key==='ArrowRight' && graphNodeAction(diagramNodeById(el.dataset.node))==='drill'){event.preventDefault();drillNode(el);return;}
      if(event.key==='Enter'||event.key===' '){event.preventDefault();focusNode(el);}
    });
  });
  renderSelectedNode(); renderLists(); updateInstructionContext();
}

function renderSelectedNode() {
  const c=diagramNodeByComponentId(state.selectedComponentId), diagram=state.diagram;
  if (!c || !diagram) {
    const attention=(diagram?.nodes || []).filter((node)=>diagramNodeHealth(node).needsAttention), direct=diagram?.scope?.directRelationships || [];
    $('selectedNode').innerHTML=attention.length ? `<small>CURRENT SCOPE · ${escapeHtml(state.readingMode)}</small><h3>${attention.length} node${attention.length===1?'':'s'} need attention</h3><p>Select a node to inspect exact projected facts. Non-leaf primary nodes open their canonical backend scope.</p>` : `<small>CURRENT SCOPE · ${escapeHtml(state.readingMode)}</small><h3>${escapeHtml(diagram?.scope?.label || 'Overview')}</h3><p>Select a node to inspect it. Upstream/downstream focus follows only the currently returned projected edges.</p>`;
    $('nodeEvidence').innerHTML=state.diagramError ? `<p><strong>Diagram unavailable</strong></p><p class="muted">${escapeHtml(state.diagramError)}</p>` : direct.length ? `<p><strong>${direct.length} direct scope relationship${direct.length===1?'':'s'}</strong></p><p class="muted">These authored relationships touch this scope directly and are not converted into fake child edges.</p>` : '<p><strong>Canonical projection</strong></p><p class="muted">Backend owns scope, topology, geometry, and routes. MAP/READ/FULL only changes disclosure.</p>';
    return;
  }
  const health=diagramNodeHealth(c), incoming=diagram.edges.filter((edge)=>edge.target===c.id), outgoing=diagram.edges.filter((edge)=>edge.source===c.id), linkedTasks=state.tasks.filter((task)=>task.related_component===c.component_id);
  const connectionLine=(edge,direction)=>{ const peerId=direction==='in'?edge.source:edge.target, peer=diagramNodeById(peerId), count=Array.isArray(edge.provenance)?edge.provenance.length:0; return `<li><strong>${direction==='in'?'From':'To'} ${escapeHtml(peer?.label || peerId)}</strong><span>${escapeHtml(edge.semantic_type || edge.label || 'relationship')}${edge.supporting_text ? ` · ${escapeHtml(edge.supporting_text)}` : ''}${count ? ` · ${count} canonical source${count===1?'':'s'}` : ''}</span></li>`; };
  const controls=`<div class="graph-focus-controls" role="group" aria-label="Graph focus">${['connected','upstream','downstream','all'].map((mode)=>`<button type="button" data-graph-focus="${mode}" class="${state.graphFocusMode===mode?'active':''}">${mode[0].toUpperCase()+mode.slice(1)}</button>`).join('')}<button type="button" data-graph-focus="clear">Clear</button></div>`;
  const provenance=[...incoming,...outgoing].flatMap((edge)=>(edge.provenance || []).map((item)=>({edge,item})));
  $('selectedNode').innerHTML=`<small>SELECTED COMPONENT · ${escapeHtml(String(c.semantic_kind))} · ${escapeHtml(state.readingMode)}</small><div class="selected-node-title"><h3>${escapeHtml(c.label)}</h3><span class="health-pill health-${health.key}">${escapeHtml(health.label)}</span></div><p>${escapeHtml(c.responsibility)}</p>${controls}<div class="component-children-summary"><strong>Scope facts</strong><span>Projection role · ${escapeHtml(c.projectionRole)}</span><span>Canonical children · ${c.childCount}</span><span>Current scope · ${escapeHtml(diagram.scope?.label || 'Overview')}</span></div><div class="component-task-summary"><strong>${linkedTasks.length} linked task${linkedTasks.length===1?'':'s'}</strong>${linkedTasks.length ? linkedTasks.map((task)=>`<span><i class="status-dot ${statusClass(task.status)}"></i>${escapeHtml(task.title)} · ${escapeHtml(task.status.replace('_',' '))}</span>`).join('') : '<span class="muted">No execution task is linked to this component.</span>'}</div><div class="component-connections">${incoming.length||outgoing.length ? `<ul>${incoming.map((edge)=>connectionLine(edge,'in')).join('')}${outgoing.map((edge)=>connectionLine(edge,'out')).join('')}</ul>` : '<p class="muted">No projected relationships for this node in the current scope.</p>'}</div>`;
  const provenanceMarkup=provenance.length ? `<div class="inspector-technical-block"><h4>Relationship provenance</h4><div class="inspector-provenance">${provenance.map(({edge,item})=>`<span>${escapeHtml(edge.id)} ← ${escapeHtml(item.relationship_id || 'canonical relationship')} · ${escapeHtml(item.semantic_type || edge.semantic_type || '')}</span>`).join('')}</div></div>` : '';
  const projectedTaskEvidence=(c.supporting_text || []).map((text)=>{
    const match=/^Task\s+([A-Z_]+):\s*(.+)$/i.exec(String(text || '').trim());
    return match ? {status:match[1].toUpperCase(), title:match[2]} : null;
  }).filter(Boolean);
  const inspectorTasks=linkedTasks.length
    ? linkedTasks.map((task)=>({status:task.status,title:task.title}))
    : projectedTaskEvidence;
  const nonTaskEvidence=(c.supporting_text || []).filter((text)=>!/^Task\s+[A-Z_]+:\s*/i.test(String(text || '').trim()));
  const inspectorTaskMarkup=inspectorTasks.length
    ? `<div class="inspector-task-list">${inspectorTasks.map((task)=>`<div class="inspector-task-row"><i class="status-dot ${statusClass(task.status)}" aria-hidden="true"></i><span>${escapeHtml(task.title)}</span></div>`).join('')}</div>`
    : `<p class="inspector-status-detail">${escapeHtml(nonTaskEvidence.join(' · ') || c.status?.canonical_status || 'No additional status evidence.')}</p>`;
  $('nodeEvidence').innerHTML=`<div class="inspector-summary"><span class="inspector-status-label">${escapeHtml(health.label)}</span>${inspectorTaskMarkup}</div><section class="inspector-responsibility"><h4>Accepted responsibility</h4><p>${escapeHtml(c.responsibility)}</p></section><dl class="inspector-facts inspector-map-facts"><div><dt>Role</dt><dd>${escapeHtml(c.projectionRole)}</dd></div><div><dt>Children</dt><dd>${c.childCount}</dd></div><div><dt>Architecture</dt><dd>v${diagram.architectureVersion}</dd></div></dl><div class="inspector-read"><dl class="inspector-facts"><div><dt>Canonical status</dt><dd>${escapeHtml(c.status?.canonical_status || 'UNKNOWN')}</dd></div><div><dt>Current scope</dt><dd>${escapeHtml(diagram.scope?.label || 'Overview')}</dd></div></dl></div><div class="inspector-full"><div class="inspector-divider"></div><dl class="inspector-facts inspector-technical-facts"><div><dt>Stable Diagram ID</dt><dd><code>${escapeHtml(c.id)}</code></dd></div><div><dt>Component ID</dt><dd><code>${escapeHtml(c.component_id)}</code></dd></div></dl>${provenanceMarkup}</div>`;
  $('selectedNode').querySelectorAll('[data-graph-focus]').forEach((button)=>button.addEventListener('click',()=>{ const mode=button.dataset.graphFocus; if(mode==='clear'){state.selectedComponentId=null;state.graphFocusMode='all';} else state.graphFocusMode=mode; renderGraph(); }));
}

function renderLists() {
  const list = (items, empty) => items?.length ? `<ul>${items.map((x) => `<li>${escapeHtml(x)}</li>`).join('')}</ul>` : `<p class="muted graph-side-empty">${empty}</p>`;
  const architecture = state.architecture || {};
  $('decisionList').innerHTML = list(architecture.decisions, 'No recorded decisions.');
  $('riskList').innerHTML = list([...(architecture.risks || []), ...(architecture.assumptions || []).map((x) => `Assumption: ${x}`)], 'None recorded.');
}

function renderRecentActivity() {
  const el = $('recentActivity');
  if (!el) return;
  const events = [...(state.activity || [])].reverse().slice(0, 6);
  if (!events.length) {
    el.innerHTML = '<p class="muted">No observed project activity yet.</p>';
    return;
  }
  el.innerHTML = events.map((event) => {
    const source = event.payload?.external_source || event.source || 'SYSTEM';
    const summary = event.payload?.summary || event.payload?.message || event.payload?.note || event.type;
    return `<div class="activity-row"><span class="status-pill">${escapeHtml(source)}</span><div><strong>${escapeHtml(event.type)}</strong><p>${escapeHtml(summary)}</p></div></div>`;
  }).join('');
}

function renderLastRun() {
  if (!state.lastRun) {
    $('lastRun').innerHTML = '<p class="muted">No event processed in this browser session.</p>';
    return;
  }
  const ok = state.lastRun.result === 'SUCCESS';
  $('lastRun').innerHTML = `<p><strong>${escapeHtml(state.lastRun.summary)}</strong></p><p class="muted">${escapeHtml(state.lastRun.provider)} · ${escapeHtml(state.lastRun.model)} · ${state.lastRun.actions.length} action${state.lastRun.actions.length === 1 ? '' : 's'} · ${ok ? 'SUCCESS' : 'ERROR'}</p>${state.lastRun.error ? `<p class="muted">${escapeHtml(state.lastRun.error)}</p>` : ''}`;
}

function renderGlobalAgentReply() {
  const reply = $('globalAgentReply');
  if (!reply) return;
  if (!state.lastRun) {
    reply.classList.add('hidden');
    reply.innerHTML = '';
    return;
  }
  const ok = state.lastRun.result === 'SUCCESS';
  reply.classList.remove('hidden');
  reply.classList.toggle('error', !ok);
  reply.innerHTML = `<div class="global-agent-reply-head"><span>${ok ? 'AGENT RESPONSE' : 'AGENT ERROR'}</span><small>${escapeHtml(state.lastRun.provider)} · ${escapeHtml(state.lastRun.model)}</small></div><p>${escapeHtml(state.lastRun.summary || state.lastRun.error || 'No response summary.')}</p>`;
}

function currentInstructionContext() {
  const base = {
    view: state.currentView,
    project_id: state.projectId,
    project_name: state.project?.name || '',
  };

  const proposal = state.proposals.find((item) => item.id === state.selectedProposalId);
  if (proposal) {
    return {
      label: `Proposal · ${proposal.reason}`,
      instruction: 'Ask about this architecture proposal or add review evidence',
      placeholder: 'Describe your decision, constraint, or evidence for this proposal.',
      payload: {...base, proposal_id: proposal.id, proposal_reason: proposal.reason, proposal_status: proposal.status},
    };
  }

  if (state.currentView === 'tasks') {
    const task = state.tasks.find((item) => item.id === state.selectedTaskId);
    return {
      label: task ? `Task · ${task.title}` : 'Tasks · project execution',
      instruction: task ? 'Ask about this task or describe what changed' : 'Ask about project tasks or execution state',
      placeholder: task ? `Example: This task is blocked because...` : 'Select a task for focused context, or describe an execution update.',
      payload: {...base, ...(task ? {task_id: task.id, task_title: task.title, task_status: task.status, related_component: task.related_component} : {})},
    };
  }

  if (state.currentView === 'architecture') {
    if (state.architectureGraphKind === 'code') {
      const codeNode = codeNodeById(state.selectedCodeNodeId);
      const repository = state.codeDiagram?.repository;
      return {
        label: codeNode ? `Code · ${codeNode.label}` : repository ? `Code · ${repository.slug}@${repository.revision.slice(0, 8)}` : 'Code Architecture · no snapshot',
        instruction: codeNode ? 'Ask about this implementation component or its source evidence' : 'Ask the Agent to inspect GitHub and publish a revision-pinned Code Architecture snapshot',
        placeholder: codeNode ? `Example: What source evidence proves ${codeNode.label} belongs here?` : 'Inspect the connected GitHub repository at an exact commit and build the implementation architecture.',
        payload: {
          ...base,
          architecture_graph_kind: 'code',
          ...(repository ? {repository: repository.slug, revision: repository.revision} : {}),
          ...(codeNode ? {code_node_id: codeNode.id, code_component_id: codeNode.component_id, code_node_name: codeNode.label} : {}),
        },
      };
    }
    const node = findArchitectureNode(state.selectedComponentId || state.scopeComponentId);
    return {
      label: node ? `Architecture · ${node.name}` : `Architecture · v${state.architecture?.version || 0}`,
      instruction: node ? 'Ask about this architecture area or describe new evidence' : 'Ask about the accepted architecture or describe a mismatch',
      placeholder: node ? `Example: Can this be solved inside ${node.name} without changing the architecture?` : 'Describe an architecture concern, dependency change, or new requirement.',
      payload: {...base, ...(node ? {architecture_node_id: node.id, architecture_node_name: node.name, architecture_node_kind: node.kind || node.type} : {})},
    };
  }

  return {
    label: `Project · ${state.project?.name || 'Overview'}`,
    instruction: 'Ask the Agent, add a task, or describe a project change',
    placeholder: 'Describe what changed, what is blocked, or what you want the Agent to evaluate.',
    payload: base,
  };
}

function updateInstructionContext() {
  const context = currentInstructionContext();
  const chip = $('instructionContext');
  const label = $('instructionLabel');
  const input = $('instruction');
  if (!chip || !label || !input) return;
  chip.textContent = context.label;
  label.textContent = context.instruction;
  input.placeholder = context.placeholder;
}

async function sendEvent(type, payload, workingDetail = '') {
  if (!state.projectId) return null;
  try {
    setWorking(true, workingDetail);
    const result = await api(`/projects/${state.projectId}/events`, {method: 'POST', body: JSON.stringify({type, source: 'FRONTEND', payload})});
    state.lastRun = result;
    if (result.result === 'ERROR') toast(result.error || 'Agent run failed before state mutation.', true);
    else toast(result.architecture_review_required ? 'Agent created an architecture proposal for review.' : 'Project state updated.');
    await refresh();
    return result;
  } catch (err) {
    toast(err.message, true);
    return null;
  } finally {
    setWorking(false);
  }
}

function setArchitectureProgress(working, startedAt = 0) {
  const wrap = $('architectureProgress');
  const button = $('generateArchitectureBtn');
  if (!wrap || !button) return;
  wrap.classList.toggle('hidden', !working);
  button.disabled = working;
  button.textContent = working ? 'Generating architecture...' : 'Retry initial architecture';
  clearInterval(setArchitectureProgress.timer);
  if (!working) return;

  const update = () => {
    const elapsed = Math.max(0, Math.floor((Date.now() - startedAt) / 1000));
    $('architectureElapsed').textContent = `${elapsed}s`;
    if (elapsed < 8) {
      $('architectureProgressText').textContent = 'Reading Goal and shaping the V0 skeleton';
      $('architectureProgressHint').textContent = '3.7 Flash is reasoning over the confirmed Goal.';
    } else if (elapsed < 16) {
      $('architectureProgressText').textContent = 'Trying a bounded fallback if needed';
      $('architectureProgressHint').textContent = 'A slow model will not block the project indefinitely.';
    } else if (elapsed < 28) {
      $('architectureProgressText').textContent = 'Validating components, relationships, and tasks';
      $('architectureProgressHint').textContent = 'The result must satisfy the machine-readable Architecture contract.';
    } else {
      $('architectureProgressText').textContent = 'Finishing within the architecture deadline';
      $('architectureProgressHint').textContent = 'If no model completes, this run will stop safely with no project-state mutation.';
    }
  };
  update();
  setArchitectureProgress.timer = setInterval(update, 1000);
}

async function generateInitialArchitecture() {
  if (WEBMCP_AGENT_MODE) {
    toast('Built-in architecture generation is disabled in WebMCP Agent Mode.', true);
    return null;
  }
  if (!state.projectId || state.architecture?.version > 0) return;
  const startedAt = Date.now();
  setArchitectureProgress(true, startedAt);
  setWorking(true);
  const controller = new AbortController();
  const clientTimeout = setTimeout(() => controller.abort(), 42000);
  try {
    const result = await api(`/projects/${state.projectId}/events`, {
      method: 'POST',
      signal: controller.signal,
      body: JSON.stringify({type: 'USER_MESSAGE', source: 'FRONTEND', payload: {intent: 'INITIAL_ARCHITECTURE'}}),
    });
    state.lastRun = result;
    if (result.result === 'SUCCESS') {
      toast('Architecture v1 and initial tasks created from the confirmed Goal.');
    } else {
      toast(result.error || 'Architecture generation stopped safely. Retry when ready.', true);
    }
    await refresh();
    return result;
  } catch (err) {
    const message = err?.name === 'AbortError'
      ? 'Architecture generation reached the client deadline. The saved Goal is safe; retry once the backend is available.'
      : err.message;
    toast(message, true);
    await refresh();
    return null;
  } finally {
    clearTimeout(clientTimeout);
    setArchitectureProgress(false);
    setWorking(false);
  }
}

function switchView(name) {
  if (state.onboarding.active) return;
  if (!views[name]) return;
  if (name === 'tasks') {
    state.selectedProposalId = null;
    state.selectedComponentId = null;
  } else if (name === 'architecture') {
    state.selectedProposalId = null;
    state.selectedTaskId = null;
  }
  state.currentView = name;
  document.querySelectorAll('.view').forEach((v) => v.classList.remove('active'));
  $(`view-${name}`).classList.add('active');
  $('pageTitle').textContent = views[name].title;
  $('pageSubtitle').textContent = views[name].subtitle;
  renderProjectTree();
  if (name === 'architecture') renderGraph();
  updateInstructionContext();
  const workspaceMain = $('workspaceMain');
  if (workspaceMain) workspaceMain.scrollTop = 0;
  window.scrollTo(0, 0);
}

function wireGoButtons() {
  document.querySelectorAll('[data-go]').forEach((btn) => btn.onclick = () => switchView(btn.dataset.go));
  document.querySelectorAll('[data-go-card]').forEach((card) => {
    const open = () => switchView(card.dataset.goCard);
    card.addEventListener('click', (event) => {
      if (event.target.closest('button,a,input,textarea,select')) return;
      open();
    });
    card.addEventListener('keydown', (event) => {
      if (event.key !== 'Enter' && event.key !== ' ') return;
      event.preventDefault();
      open();
    });
  });
}

function syncMcpTransportFields() {
  const isHttp = $('mcpTransport').value === 'streamable_http';
  $('mcpHttpFields').classList.toggle('hidden', !isHttp);
  $('mcpStdioFields').classList.toggle('hidden', isHttp);
}

function mcpOAuthProviderId(preset = $('mcpPreset').value) {
  if (preset === 'github-remote') return 'github';
  if (preset === 'slack') return 'slack';
  if (preset === 'google-drive') return 'google-drive';
  if (preset === 'microsoft-teams') return 'microsoft-teams';
  return null;
}

function mcpPresetForProvider(providerId) {
  if (providerId === 'github') return 'github-remote';
  if (providerId === 'slack') return 'slack';
  if (providerId === 'google-drive') return 'google-drive';
  if (providerId === 'microsoft-teams') return 'microsoft-teams';
  return null;
}

function mcpProviderStatusEntry(providerId) {
  let entry = mcpProviderStatusCache.get(providerId);
  if (!entry) {
    entry = {value: null, fetchedAt: 0, inFlight: null, generation: 0};
    mcpProviderStatusCache.set(providerId, entry);
  }
  return entry;
}

function mcpProviderStatusEndpoint(providerId) {
  return `/mcp/oauth/${encodeURIComponent(providerId)}/status`;
}

function mcpLegacyProviderStatusEndpoint(providerId) {
  if (providerId === 'github') return '/mcp/auth/github/status';
  if (providerId === 'google-drive') return '/mcp/auth/google-drive/status';
  return null;
}

async function resolveMcpProviderStatus(providerId) {
  const generic = await api(mcpProviderStatusEndpoint(providerId));
  if (generic?.configured === true) return {...generic, oauth_strategy: 'generic'};

  const legacyEndpoint = mcpLegacyProviderStatusEndpoint(providerId);
  if (!legacyEndpoint) return {...generic, oauth_strategy: 'generic'};

  try {
    const legacy = await api(legacyEndpoint);
    if (legacy?.configured === true) return {...legacy, oauth_strategy: 'legacy-runtime'};
  } catch {
    // Keep the deployment OAuth result when the optional runtime fallback is unavailable.
  }
  return {...generic, oauth_strategy: 'generic'};
}

function invalidateMcpProviderStatus(providerId) {
  if (!providerId) return;
  const entry = mcpProviderStatusEntry(providerId);
  entry.value = null;
  entry.fetchedAt = 0;
  entry.generation += 1;
  entry.inFlight = null;
}

function requestMcpProviderStatus(providerId, {force = false} = {}) {
  const entry = mcpProviderStatusEntry(providerId);
  const fresh = entry.value && (Date.now() - entry.fetchedAt) < MCP_PROVIDER_STATUS_TTL_MS;
  if (!force && fresh) return Promise.resolve(entry.value);
  if (entry.inFlight) return entry.inFlight;

  const generation = entry.generation;
  const request = resolveMcpProviderStatus(providerId)
    .then((value) => {
      if (entry.generation === generation) {
        entry.value = value;
        entry.fetchedAt = Date.now();
      }
      return value;
    })
    .finally(() => {
      if (entry.inFlight === request) entry.inFlight = null;
    });
  entry.inFlight = request;
  return request;
}

function renderMcpOAuthStatusShell(preset) {
  const providerId = mcpOAuthProviderId(preset);
  const oauthMode = Boolean(providerId);
  $('mcpManualPanel').classList.toggle('hidden', oauthMode);
  $('mcpOAuthPanel').classList.toggle('hidden', !oauthMode);
  $('mcpManualConnectBtn').classList.toggle('hidden', oauthMode);
  $('mcpOAuthConnectBtn').classList.toggle('hidden', !oauthMode);
  if (!providerId) return null;

  const sourceIcon = $('mcpConfigIcon');
  $('mcpOAuthProviderIcon').innerHTML = sourceIcon?.innerHTML || '';
  $('mcpOAuthProviderIcon').className = `mcp-oauth-provider-icon ${sourceIcon?.className || ''}`;
  const titles = {github: 'Connect GitHub', slack: 'Connect Slack', 'google-drive': 'Connect Google Drive', 'microsoft-teams': 'Connect Microsoft Teams'};
  $('mcpOAuthTitle').textContent = titles[providerId] || 'Connect provider';
  const descriptions = {
    github: 'Sign in to your own GitHub account and approve ArchBro. Your GitHub token stays backend-only and is attached only to your ArchBro user while connecting to GitHub remote MCP.',
    slack: 'Sign in to your own Slack workspace account and approve ArchBro. Your Slack user token stays backend-only and is attached only to your ArchBro user while connecting to Slack remote MCP.',
    'google-drive': 'Sign in to your own Google account and approve ArchBro. Your Google token stays backend-only and is attached only to your ArchBro user while connecting to Google Drive remote MCP.',
    'microsoft-teams': 'A Microsoft authorization window will open. ArchBro uses delegated Microsoft Graph access for Teams and keeps the OAuth session backend-only and memory-only.',
  };
  $('mcpOAuthDescription').textContent = descriptions[providerId] || 'A provider sign-in window will open.';
  return providerId;
}

function renderMcpOAuthStatusLoading(preset) {
  if ($('mcpPreset').value !== preset) return;
  $('mcpOAuthReady').classList.add('hidden');
  $('mcpProviderSetup').classList.add('hidden');
  $('mcpOAuthRedirectReady').textContent = 'Checking provider sign-in status…';
  $('mcpOAuthConnectBtn').classList.remove('hidden');
  $('mcpOAuthConnectBtn').disabled = true;
  $('mcpOAuthConnectBtn').textContent = 'Checking sign-in…';
  delete $('mcpOAuthConnectBtn').dataset.statusRetry;
}

function renderMcpOAuthStatus(preset, status) {
  if ($('mcpPreset').value !== preset || !status) return;
  const providerId = mcpOAuthProviderId(preset);
  $('mcpOAuthReady').classList.add('hidden');
  $('mcpProviderSetup').classList.add('hidden');
  delete $('mcpOAuthConnectBtn').dataset.statusRetry;


  const configured = status.configured === true;
  $('mcpOAuthRedirectReady').textContent = configured
    ? `${status.name} sign-in is ready. Authorization opens in a separate window.`
    : `${status.name} is a built-in ArchBro connector. Sign-in requires the ArchBro deployment provider identity.`;
  $('mcpOAuthReady').classList.toggle('hidden', !configured);
  $('mcpProviderSetup').classList.toggle('hidden', configured);
  const missingConfiguration = Array.isArray(status.missing_configuration) && status.missing_configuration.length
    ? ` Missing: ${status.missing_configuration.join(', ')}.`
    : '';
  $('mcpProviderSetupText').textContent = configured
    ? ''
    : `${status.name} sign-in is not provisioned for this ArchBro deployment. The deployment owner must configure the provider identity.${missingConfiguration}`;
  $('mcpOAuthConnectBtn').classList.remove('hidden');
  $('mcpOAuthConnectBtn').disabled = !configured;
  $('mcpOAuthConnectBtn').textContent = `Continue with ${status.name}`;
}

function renderMcpOAuthStatusError(preset, err) {
  if ($('mcpPreset').value !== preset) return;
  $('mcpOAuthRedirectReady').textContent = `Unable to refresh sign-in status: ${err.message}`;
  $('mcpOAuthConnectBtn').classList.remove('hidden');
  $('mcpOAuthConnectBtn').disabled = false;
  $('mcpOAuthConnectBtn').textContent = 'Retry status';
  $('mcpOAuthConnectBtn').dataset.statusRetry = 'true';
}

async function refreshMcpProviderStatusAfterMutation(providerId) {
  if (!providerId) return null;
  invalidateMcpProviderStatus(providerId);
  const preset = mcpPresetForProvider(providerId);
  try {
    const status = await requestMcpProviderStatus(providerId, {force: true});
    if (preset) renderMcpOAuthStatus(preset, status);
    return status;
  } catch (err) {
    if (preset) renderMcpOAuthStatusError(preset, err);
    return null;
  }
}

function openMcpOAuthPopup(providerId) {
  const width = 620;
  const height = 760;
  const left = Math.max(0, window.screenX + Math.round((window.outerWidth - width) / 2));
  const top = Math.max(0, window.screenY + Math.round((window.outerHeight - height) / 2));
  const popup = window.open(
    'about:blank',
    `archbro_mcp_oauth_${providerId}`,
    `popup=yes,width=${width},height=${height},left=${left},top=${top}`,
  );
  if (!popup) {
    toast('Your browser blocked the OAuth window. Allow popups for this ArchBro site and retry.', true);
    return null;
  }
  activeMcpOAuthPopup = popup;
  popup.document.title = 'ArchBro authorization';
  popup.document.body.innerHTML = '<p style="font:16px system-ui;padding:24px">Opening authorization…</p>';
  popup.focus();
  return popup;
}

function watchMcpOAuthPopup(popup, preset) {
  const timer = setInterval(async () => {
    if (!popup.closed) return;
    clearInterval(timer);
    if (activeMcpOAuthPopup === popup) activeMcpOAuthPopup = null;
    if (handledMcpOAuthPopups.has(popup)) {
      handledMcpOAuthPopups.delete(popup);
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
    if ($('mcpConnectionsDialog').open && $('mcpPreset').value === preset) {
      await loadMcpOAuthStatus(preset, {force: true});
    }
  }, 500);
}

function applyMcpPreset(preset = $('mcpPreset').value) {
  $('mcpBearerToken').value = '';
  $('mcpCommand').value = '';
  $('mcpArgs').value = '';
  $('mcpEnv').value = '{}';
  $('mcpTransport').value = 'streamable_http';
  $('mcpBearerToken').placeholder = 'Paste access token if required';
  $('mcpAuthHint').textContent = 'Bearer token · kept in memory only';

  if (preset === 'github-remote') {
    $('mcpName').value = 'GitHub';
    $('mcpUrl').value = 'https://api.githubcopilot.com/mcp/';
    $('mcpBearerToken').placeholder = 'GitHub access token';
    $('mcpAuthHint').textContent = 'GitHub token · kept in memory only';
  } else if (preset === 'slack') {
    $('mcpName').value = 'Slack';
    $('mcpUrl').value = 'https://mcp.slack.com/mcp';
  } else if (preset === 'google-drive') {
    $('mcpName').value = 'Google Drive';
    $('mcpUrl').value = 'https://drivemcp.googleapis.com/mcp/v1';
    $('mcpAuthHint').textContent = 'Google Drive OAuth · remote MCP';
  } else if (preset === 'microsoft-teams') {
    $('mcpName').value = 'Microsoft Teams';
    $('mcpUrl').value = 'https://graph.microsoft.com/v1.0';
  } else {
    $('mcpName').value = 'Custom MCP';
    $('mcpUrl').value = '';
    $('mcpAuthHint').textContent = 'Optional bearer token · kept in memory only';
  }
  syncMcpTransportFields();
}

async function loadMcpOAuthStatus(preset = $('mcpPreset').value, {force = false, background = false} = {}) {
  const requestId = ++mcpOAuthStatusRequestId;
  const providerId = renderMcpOAuthStatusShell(preset);
  if (!providerId) return null;

  const entry = mcpProviderStatusEntry(providerId);
  const fresh = entry.value && (Date.now() - entry.fetchedAt) < MCP_PROVIDER_STATUS_TTL_MS;
  if (entry.value) renderMcpOAuthStatus(preset, entry.value);
  if (!force && fresh) return entry.value;
  if (!entry.value) renderMcpOAuthStatusLoading(preset);

  const request = requestMcpProviderStatus(providerId, {force});
  const applyResult = request
    .then((status) => {
      if (requestId === mcpOAuthStatusRequestId && $('mcpPreset').value === preset) {
        renderMcpOAuthStatus(preset, status);
      }
      return status;
    })
    .catch((err) => {
      if (requestId === mcpOAuthStatusRequestId && $('mcpPreset').value === preset) {
        renderMcpOAuthStatusError(preset, err);
      }
      return null;
    });

  if (background) {
    void applyResult;
    return entry.value;
  }
  return applyResult;
}

function selectMcpPreset(preset) {
  const selected = document.querySelector(`[data-mcp-preset="${preset}"]`);
  if (!selected) return;
  $('mcpPreset').value = preset;
  document.querySelectorAll('[data-mcp-preset]').forEach((card) => card.classList.toggle('selected', card === selected));
  const sourceIcon = selected.querySelector('.mcp-provider-icon');
  const configIcon = $('mcpConfigIcon');
  if (sourceIcon && configIcon) {
    configIcon.className = sourceIcon.className;
    configIcon.innerHTML = sourceIcon.innerHTML;
  }
  const title = selected.querySelector('.mcp-provider-copy strong')?.textContent || 'Custom MCP';
  const subtitle = selected.querySelector('.mcp-provider-copy small')?.textContent || 'Remote or local';
  $('mcpConfigTitle').textContent = title;
  $('mcpConfigSubtitle').textContent = subtitle;
  applyMcpPreset(preset);
  void loadMcpOAuthStatus(preset, {background: true});
}

async function startLegacyMcpOAuth(providerId, popup, preset, status) {
  let connectionId = null;
  try {
    const started = await api(`/mcp/auth/${encodeURIComponent(providerId)}/start`, {method: 'POST'});
    connectionId = started.connection?.id || null;
    if (started.connected) {
      popup.close();
      toast(`${status.name} connected: ${started.tool_count || started.connection?.tool_count || 0} tools discovered.`);
      await refreshMcpProviderStatusAfterMutation(providerId);
      await loadMcpConnections();
      setMcpPickerTab('connected');
      return;
    }
    if (!started.authorization_url || !connectionId) {
      throw new Error(`${status.name} did not return an authorization URL.`);
    }
    popup.location.replace(started.authorization_url);
    popup.focus();

    const deadline = Date.now() + 180000;
    while (Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 1500));
      const result = await api(`/mcp/auth/${encodeURIComponent(providerId)}/${encodeURIComponent(connectionId)}/poll`, {method: 'POST'});
      if (!result.connected) {
        if (providerId === 'google-drive' && popup.closed) {
          throw new Error('Google Drive authorization was cancelled.');
        }
        continue;
      }
      if (!popup.closed) popup.close();
      toast(`${status.name} connected: ${result.tool_count || 0} tools discovered.`);
      await refreshMcpProviderStatusAfterMutation(providerId);
      await loadMcpConnections();
      setMcpPickerTab('connected');
      return;
    }
    throw new Error(`${status.name} authorization timed out. Retry Connect when ready.`);
  } catch (err) {
    if (!popup.closed) popup.close();
    if (connectionId) {
      try { await api(`/mcp/connections/${encodeURIComponent(connectionId)}`, {method: 'DELETE'}); } catch {}
    }
    toast(err.message, true);
    await refreshMcpProviderStatusAfterMutation(providerId);
  }
}

async function startMcpOAuth() {
  const preset = $('mcpPreset').value;
  const providerId = mcpOAuthProviderId(preset);
  if (!providerId) return;
  if ($('mcpOAuthConnectBtn').dataset.statusRetry === 'true') {
    await loadMcpOAuthStatus(preset, {force: true});
    return;
  }

  const popup = openMcpOAuthPopup(providerId);
  if (!popup) return;

  const status = await loadMcpOAuthStatus(preset);
  if ($('mcpPreset').value !== preset || !$('mcpConnectionsDialog').open) {
    popup.close();
    return;
  }

  $('mcpOAuthConnectBtn').disabled = true;
  $('mcpOAuthConnectBtn').textContent = 'Waiting for authorization…';



  if (!status?.configured) {
    popup.close();
    toast(`${providerId.replace('-', ' ')} sign-in is not available in this ArchBro deployment.`, true);
    return;
  }

  if (status.oauth_strategy === 'legacy-runtime') {
    await startLegacyMcpOAuth(providerId, popup, preset, status);
    return;
  }

  try {
    const started = await api(`/mcp/oauth/${encodeURIComponent(providerId)}/start`, {method: 'POST'});
    if (!started?.authorization_url) throw new Error(`${status.name} did not return an authorization URL.`);
    popup.location.replace(started.authorization_url);
    popup.focus();
    watchMcpOAuthPopup(popup, preset);
  } catch (err) {
    if (!popup.closed) popup.close();
    toast(err.message, true);
    await refreshMcpProviderStatusAfterMutation(providerId);
  }
}

function setMcpPickerTab(tab) {
  const connected = tab === 'connected';
  $('mcpBrowsePane').classList.toggle('hidden', connected);
  $('mcpConnectedPane').classList.toggle('hidden', !connected);
  document.querySelectorAll('[data-mcp-tab]').forEach((button) => button.classList.toggle('active', button.dataset.mcpTab === tab));
}

function filterMcpProviders(query = '') {
  const normalized = String(query).trim().toLowerCase();
  let visible = 0;
  document.querySelectorAll('[data-mcp-preset]').forEach((card) => {
    const haystack = `${card.dataset.mcpSearch || ''} ${card.textContent || ''}`.toLowerCase();
    const match = !normalized || haystack.includes(normalized);
    card.classList.toggle('hidden', !match);
    if (match) visible += 1;
  });
  $('mcpNoSearchResults').classList.toggle('hidden', visible !== 0);
}

function parseMcpJson(id, fallback) {
  const text = $(id).value.trim();
  if (!text) return fallback;
  try {
    return JSON.parse(text);
  } catch {
    throw new Error(`${id === 'mcpArgs' ? 'Args' : 'Env'} must be valid JSON.`);
  }
}

async function loadMcpConnections() {
  const connections = await api('/mcp/connections');
  const list = $('mcpConnectionList');
  $('mcpConnectedCount').textContent = connections.length;
  if (!connections.length) {
    list.innerHTML = '<div class="mcp-empty-state"><strong>No MCPs connected yet</strong><span>Choose Browse and connect one when you are ready.</span></div>';
    return connections;
  }
  list.innerHTML = connections.map((connection) => {
    const probe = connection.last_probe_ok === true ? 'READY' : connection.last_probe_ok === false ? 'FAILED' : 'NOT TESTED';
    const probeClass = connection.last_probe_ok === true ? 'ready' : connection.last_probe_ok === false ? 'failed' : '';
    const toolCount = connection.tool_count == null ? '—' : connection.tool_count;
    const authBadge = connection.auth_type === 'oauth'
      ? '<span class="ready">OAuth</span>'
      : connection.auth_type === 'github_oauth'
        ? '<span class="ready">GitHub OAuth</span>'
      : ['google_gcloud', 'google_drive_oauth'].includes(connection.auth_type)
          ? '<span class="ready">Google OAuth</span>'
        : connection.auth_type === 'microsoft_teams_oauth'
          ? '<span class="ready">Teams OAuth</span>'
        : connection.has_credentials ? '<span>credential set</span>' : '';
    return `<div class="mcp-connection-row">
      <div class="mcp-connected-main"><span class="mcp-connected-dot ${probeClass}"></span><div><strong>${escapeHtml(connection.name)}</strong><p>${escapeHtml(connection.endpoint || '')}</p><div class="mcp-connection-meta"><span>${escapeHtml(connection.transport)}</span><span class="${probeClass}">${probe}</span><span>${toolCount} tools</span>${authBadge}</div>${connection.last_error ? `<p class="mcp-connection-error">${escapeHtml(connection.last_error)}</p>` : ''}</div></div>
      <div class="mcp-connection-actions"><button type="button" data-mcp-probe="${escapeHtml(connection.id)}">Test</button><button type="button" class="danger" data-mcp-remove="${escapeHtml(connection.id)}" data-mcp-provider="${escapeHtml(connection.provider || '')}">Remove</button></div>
    </div>`;
  }).join('');
  list.querySelectorAll('[data-mcp-probe]').forEach((button) => button.addEventListener('click', async () => {
    button.disabled = true;
    button.textContent = 'Testing…';
    try {
      const result = await api(`/mcp/connections/${encodeURIComponent(button.dataset.mcpProbe)}/probe`, {method: 'POST'});
      toast(`MCP ready: ${result.tool_count} tool${result.tool_count === 1 ? '' : 's'} discovered.`);
    } catch (err) {
      toast(err.message, true);
    } finally {
      await loadMcpConnections();
    }
  }));
  list.querySelectorAll('[data-mcp-remove]').forEach((button) => button.addEventListener('click', async () => {
    button.disabled = true;
    const providerId = button.dataset.mcpProvider || null;
    try {
      await api(`/mcp/connections/${encodeURIComponent(button.dataset.mcpRemove)}`, {method: 'DELETE'});
      toast('MCP connection removed.');
      if (providerId) await refreshMcpProviderStatusAfterMutation(providerId);
    } catch (err) {
      toast(err.message, true);
    } finally {
      await loadMcpConnections();
    }
  }));
  return connections;
}

function openMcpConnections() {
  $('mcpConnectionsDialog').showModal();
  $('mcpSearch').value = '';
  filterMcpProviders('');
  setMcpPickerTab('browse');
  selectMcpPreset($('mcpPreset').value || 'github-remote');
  setTimeout(() => $('mcpSearch').focus(), 20);
  void loadMcpConnections().catch((err) => toast(err.message, true));
}

async function addMcpConnection() {
  if (mcpOAuthProviderId()) throw new Error('Use the provider sign-in button for GitHub, Slack, Google Drive, or Microsoft Teams.');
  const transport = $('mcpTransport').value;
  const secret = $('mcpBearerToken').value.trim();
  const body = {
    name: $('mcpName').value.trim(),
    transport,
  };
  if (!body.name) throw new Error('Connection name is required.');
  if (transport === 'streamable_http') {
    body.url = $('mcpUrl').value.trim();
    body.headers = secret ? {Authorization: `Bearer ${secret}`} : {};
  } else {
    const args = parseMcpJson('mcpArgs', []);
    const env = parseMcpJson('mcpEnv', {});
    if (!Array.isArray(args)) throw new Error('Args must be a JSON array.');
    if (!env || Array.isArray(env) || typeof env !== 'object') throw new Error('Env must be a JSON object.');
    body.command = $('mcpCommand').value.trim();
    body.args = args.map((value) => String(value));
    body.env = Object.fromEntries(Object.entries(env).map(([key, value]) => [key, String(value)]));
  }
  await api('/mcp/connections', {method: 'POST', body: JSON.stringify(body)});
  $('mcpBearerToken').value = '';
  $('mcpEnv').value = transport === 'stdio' ? '{}' : '';
  toast('MCP connected in memory.');
  await loadMcpConnections();
  setMcpPickerTab('connected');
}

function webMcpRequireProject() {
  if (!state.projectId || !state.project || !state.architecture) {
    throw new Error('No active ArchBro project is loaded.');
  }
}

function webMcpContext() {
  if (!state.projectId || !state.project || !state.architecture) {
    return {
      project: null,
      view: 'onboarding',
      project_count: state.projects.length,
      can_create_project: true,
    };
  }
  const selectedTask = state.tasks.find((item) => item.id === state.selectedTaskId) || null;
  const selectedNode = findArchitectureNode(state.selectedComponentId || state.scopeComponentId);
  const pending = state.proposals.filter((proposal) => proposal.status === 'PENDING');
  const selectedProposal = state.proposals.find((proposal) => proposal.id === state.selectedProposalId) || pending[0] || null;
  return {
    project: state.project,
    view: state.currentView,
    architecture_version: state.architecture.version,
    selected_task: selectedTask,
    selected_architecture_node: selectedNode,
    selected_proposal: selectedProposal,
    pending_proposal_count: pending.length,
  };
}

const WEBMCP_ARCHITECTURE_KINDS = new Set([
  'SYSTEM', 'UI', 'SERVICE', 'AGENT', 'TOOL', 'DATA_STORE', 'STATE',
  'EXTERNAL_SERVICE', 'INFRASTRUCTURE',
]);

function normalizeWebMcpArchitectureComponents(rawComponents, {requireIds = false, maxDepth = 3} = {}) {
  if (!Array.isArray(rawComponents) || !rawComponents.length) {
    throw new Error('At least one architecture component is required.');
  }
  const usedIds = new Set();
  const aliases = new Map();
  const ambiguousNames = new Set();

  const registerName = (name, id) => {
    const key = name.toLowerCase();
    if (aliases.has(key) && aliases.get(key) !== id) {
      ambiguousNames.add(key);
      aliases.delete(key);
      return;
    }
    if (!ambiguousNames.has(key)) aliases.set(key, id);
  };

  const normalize = (component, depth, indexPath) => {
    if (!component || typeof component !== 'object' || Array.isArray(component)) {
      throw new Error('Every architecture component must be an object.');
    }
    if (depth > maxDepth) throw new Error(`Architecture depth is capped at ${maxDepth} levels.`);
    const componentName = String(component.name || '').trim();
    const componentType = String(component.type || '').trim();
    const responsibility = String(component.responsibility || '').trim();
    const explicitId = String(component.id || '').trim();
    if (!componentName || !componentType || !responsibility) {
      throw new Error('Every component requires name, type, and responsibility.');
    }
    if (requireIds && !explicitId) throw new Error('Every architecture component requires a stable id.');
    const baseId = explicitId || componentName.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '') || `component-${indexPath.join('-')}`;
    let id = baseId;
    if (explicitId && usedIds.has(id)) throw new Error(`Duplicate architecture component id: ${id}`);
    let suffix = 2;
    while (!explicitId && usedIds.has(id)) id = `${baseId}-${suffix++}`;
    usedIds.add(id);
    aliases.set(id.toLowerCase(), id);
    registerName(componentName, id);

    const kind = String(component.kind || 'SYSTEM').trim().toUpperCase();
    if (!WEBMCP_ARCHITECTURE_KINDS.has(kind)) throw new Error(`Unsupported architecture component kind: ${kind}`);
    const rawChildren = component.children ?? [];
    if (!Array.isArray(rawChildren)) throw new Error(`Component ${id} children must be an array.`);
    if (depth >= maxDepth && rawChildren.length) {
      throw new Error(`Architecture depth is capped at ${maxDepth} levels.`);
    }
    const children = rawChildren.map((child, index) => normalize(child, depth + 1, [...indexPath, index + 1]));
    return {
      id,
      name: componentName,
      type: componentType,
      responsibility,
      status: String(component.status || 'PLANNED').trim() || 'PLANNED',
      kind,
      children,
    };
  };

  const components = rawComponents.map((component, index) => normalize(component, 1, [index + 1]));
  const resolveComponent = (value) => {
    const raw = String(value || '').trim();
    const key = raw.toLowerCase();
    if (ambiguousNames.has(key)) throw new Error(`Architecture component name is ambiguous; use a stable id instead: ${raw}`);
    return aliases.get(key) || raw;
  };
  return {components, resolveComponent};
}

function normalizeInitialPlanningTrace(rawTrace, normalizedComponents) {
  if (!rawTrace || typeof rawTrace !== 'object' || Array.isArray(rawTrace)) {
    throw new Error('planning_trace is required for WebMCP initial architecture.');
  }
  if (rawTrace.reconciled !== true) {
    throw new Error('planning_trace.reconciled must be true after relationships and tasks are reconciled.');
  }
  const rootIds = normalizedComponents.map((component) => component.id);
  const leafRoots = normalizedComponents.filter((component) => !(component.children || []).length).map((component) => component.id);
  if (leafRoots.length) {
    throw new Error(`WebMCP SYSTEM_MAP roots must be expanded architecture boundaries; atomic components belong below a root: ${leafRoots.join(', ')}.`);
  }
  const systemMapRootIds = Array.isArray(rawTrace.system_map_root_ids)
    ? rawTrace.system_map_root_ids.map((value) => String(value || '').trim())
    : [];
  if (systemMapRootIds.length !== rootIds.length || systemMapRootIds.some((id, index) => id !== rootIds[index])) {
    throw new Error('planning_trace.system_map_root_ids must exactly match final architecture roots in order.');
  }
  const flattenPreorder = (components) => components.flatMap((component) => [component, ...flattenPreorder(component.children || [])]);
  const plannedComponents = flattenPreorder(normalizedComponents);
  const rawEvaluations = Array.isArray(rawTrace.scope_evaluations) ? rawTrace.scope_evaluations : [];
  if (rawEvaluations.length !== plannedComponents.length) {
    throw new Error('planning_trace.scope_evaluations must cover every canonical component exactly once in preorder.');
  }
  const scopeEvaluations = rawEvaluations.map((evaluation, index) => {
    const component = plannedComponents[index];
    const scopeComponentId = String(evaluation?.scope_component_id || '').trim();
    if (scopeComponentId !== component.id) {
      throw new Error('planning_trace.scope_evaluations must follow canonical component preorder.');
    }
    const decomposition = String(evaluation?.decomposition || '').trim();
    const childIds = Array.isArray(evaluation?.child_ids)
      ? evaluation.child_ids.map((value) => String(value || '').trim())
      : [];
    if (childIds.some((id) => !id) || new Set(childIds).size !== childIds.length) {
      throw new Error(`planning_trace child_ids must be non-empty and unique for scope ${scopeComponentId}.`);
    }
    const expectedChildIds = (component.children || []).map((child) => child.id);
    const leafReason = String(evaluation?.leaf_reason || '').trim();
    if (expectedChildIds.length) {
      if (decomposition !== 'EXPANDED') throw new Error(`Scope ${scopeComponentId} has children and must be EXPANDED.`);
      if (leafReason) throw new Error(`EXPANDED scope ${scopeComponentId} must not provide leaf_reason.`);
      if (childIds.length !== expectedChildIds.length || childIds.some((id, childIndex) => id !== expectedChildIds[childIndex])) {
        throw new Error(`planning_trace child_ids do not match immediate final children for scope ${scopeComponentId}.`);
      }
    } else {
      if (decomposition !== 'JUSTIFIED_LEAF') throw new Error(`Scope ${scopeComponentId} has no children and must be JUSTIFIED_LEAF.`);
      if (childIds.length) throw new Error(`JUSTIFIED_LEAF scope ${scopeComponentId} must not provide child_ids.`);
      if (leafReason.length < 24) throw new Error(`JUSTIFIED_LEAF scope ${scopeComponentId} requires a specific leaf_reason of at least 24 characters.`);
    }
    return {scope_component_id: scopeComponentId, decomposition, child_ids: childIds, ...(leafReason ? {leaf_reason: leafReason} : {})};
  });
  return {
    system_map_root_ids: systemMapRootIds,
    scope_evaluations: scopeEvaluations,
    reconciled: true,
  };
}

window.ArchBroWebBridge = {
  async bootstrapProject({name, goal, architectureSummary, components = [], relationships = [], tasks = [], planningTrace, reasoning} = {}) {
    await ensureAppInitialized();
    const projectName = String(name || '').trim();
    const projectGoal = String(goal || '').trim();
    const summary = String(architectureSummary || '').trim();
    const bootstrapReasoning = String(reasoning || '').trim();
    if (!projectName) throw new Error('Project name is required.');
    if (!projectGoal) throw new Error('Project goal is required.');
    if (!summary) throw new Error('Architecture summary is required.');
    if (!Array.isArray(components) || !components.length) throw new Error('At least one architecture component is required.');
    if (!Array.isArray(tasks) || !tasks.length) throw new Error('At least one initial task is required.');
    if (!bootstrapReasoning) throw new Error('Architecture reasoning is required.');

    const {components: normalizedComponents, resolveComponent} = normalizeWebMcpArchitectureComponents(components, {requireIds: true});
    const normalizedPlanningTrace = normalizeInitialPlanningTrace(planningTrace, normalizedComponents);
    const architecture = {
      version: 1,
      summary,
      components: normalizedComponents,
      relationships: (relationships || []).map((relationship) => ({
        source: resolveComponent(relationship?.source),
        target: resolveComponent(relationship?.target),
        relationship_type: String(relationship?.type || 'DEPENDS_ON').trim(),
        description: String(relationship?.description || '').trim(),
      })),
      decisions: [],
      assumptions: [],
      risks: [],
    };
    const normalizedTasks = tasks.map((task) => ({
      title: String(task?.title || '').trim(),
      description: String(task?.description || '').trim(),
      related_component: task?.component ? resolveComponent(task.component) : null,
      source: 'AGENT',
      acceptance_criteria: [],
      dependencies: [],
    }));
    if (normalizedTasks.some((task) => !task.title)) throw new Error('Every initial task requires a title.');

    const previousProjectId = state.projectId;
    const project = await api('/projects', {
      method: 'POST',
      body: JSON.stringify({name: projectName, goal: projectGoal, description: ''}),
    });

    try {
      state.projectId = project.id;
      localStorage.setItem('archbro-project-id', project.id);
      state.project = project;
      state.lastRun = null;
      state.onboarding.active = false;
      const result = await api(`/projects/${project.id}/interactive-initial-architecture`, {
        method: 'POST',
        body: JSON.stringify({
          architecture,
          tasks: normalizedTasks,
          reasoning: bootstrapReasoning,
          planning_trace: normalizedPlanningTrace,
        }),
      });
      await loadProjects();
      await refresh();
      return {
        project: state.project,
        ...result,
        built_in_model_called: false,
        context: webMcpContext(),
      };
    } catch (error) {
      try {
        await api(`/projects/${project.id}`, {method: 'DELETE'});
      } catch (_cleanupError) {
        // Preserve the original bootstrap failure; cleanup is best-effort.
      }
      state.projectId = previousProjectId || null;
      if (previousProjectId) localStorage.setItem('archbro-project-id', previousProjectId);
      else localStorage.removeItem('archbro-project-id');
      await loadProjects();
      await refresh();
      throw error;
    }
  },

  async expandArchitectureScope({scopeComponentId, children = [], reasoning, evidence = [], impact = '', expectedArchitectureVersion} = {}) {
    await ensureAppInitialized();
    webMcpRequireProject();
    await refresh();
    const scopeId = String(scopeComponentId || '').trim();
    if (!scopeId) throw new Error('scope_component_id is required.');
    if (!findArchitectureNode(scopeId)) throw new Error(`Architecture component not found: ${scopeId}`);
    const expected = Number(expectedArchitectureVersion);
    if (!Number.isInteger(expected) || expected < 0) throw new Error('expected_architecture_version must be a non-negative integer.');
    if (Number(state.architecture.version) !== expected) {
      throw new Error(`Stale architecture version: expected ${expected}, current ${state.architecture.version}.`);
    }
    const normalizedEvidence = (Array.isArray(evidence) ? evidence : []).map((item) => String(item || '').trim()).filter(Boolean);
    if (!normalizedEvidence.length) throw new Error('At least one evidence item is required.');
    const expansionReasoning = String(reasoning || '').trim();
    if (!expansionReasoning) throw new Error('Expansion reasoning is required.');
    const {components: normalizedChildren} = normalizeWebMcpArchitectureComponents(children, {requireIds: true, maxDepth: 1});
    const existingIds = new Set();
    const collectExistingIds = (nodes) => {
      for (const node of nodes || []) {
        existingIds.add(node.id);
        collectExistingIds(node.children || []);
      }
    };
    collectExistingIds(state.architecture.components);
    const collisions = normalizedChildren.map((child) => child.id).filter((id) => existingIds.has(id));
    if (collisions.length) throw new Error(`Expanded child ids already exist in architecture: ${collisions.join(', ')}`);
    return window.ArchBroWebBridge.submitAgentRecommendation({
      recommendation: 'ACCEPT_PROPOSED_CHANGE',
      reasoning: expansionReasoning,
      evidence: normalizedEvidence,
      observedChange: `The accepted ${scopeId} boundary needs one more explicit decomposition level.`,
      affectedComponents: [scopeId],
      proposedChanges: [{operation: 'expand_scope', component_id: scopeId, children: normalizedChildren}],
      impact: String(impact || '').trim() || `Adds explicit child boundaries under ${scopeId} without replacing existing component identities.`,
      expectedArchitectureVersion: expected,
    });
  },

  async createProject({name, goal, description = ''} = {}) {
    const projectName = String(name || '').trim();
    const projectGoal = String(goal || '').trim();
    const projectDescription = String(description || '').trim();
    if (!projectName) throw new Error('Project name is required.');
    if (!projectGoal) throw new Error('Project goal is required.');

    const project = await api('/projects', {
      method: 'POST',
      body: JSON.stringify({name: projectName, goal: projectGoal, description: projectDescription}),
    });
    state.projectId = project.id;
    localStorage.setItem('archbro-project-id', project.id);
    state.project = project;
    state.lastRun = null;
    state.onboarding.active = false;
    await loadProjects();
    await refresh();
    return {
      project: state.project,
      bootstrap_required: true,
      bootstrap_provider: 'webmcp-agent',
      built_in_model_called: false,
      bootstrap_context: {
        goal: projectGoal,
        description: projectDescription,
        architecture_version_required: 1,
        task_status_default: 'TODO',
        rules: [
          'This low-level bridge method is internal; public WebMCP project creation uses archbro_bootstrap_project atomically.',
          'Use stable component ids that tasks can reference.',
        ],
      },
      recommended_next_tool: null,
    };
  },

  async submitInitialArchitecture({architecture, tasks = [], planningTrace, reasoning} = {}) {
    webMcpRequireProject();
    if (!architecture || typeof architecture !== 'object') throw new Error('Architecture v1 is required.');
    if (!Array.isArray(tasks) || !tasks.length) throw new Error('At least one initial task is required.');
    const {components: normalizedComponents} = normalizeWebMcpArchitectureComponents(architecture.components || [], {requireIds: true});
    const normalizedPlanningTrace = normalizeInitialPlanningTrace(planningTrace, normalizedComponents);
    const result = await api(`/projects/${state.projectId}/interactive-initial-architecture`, {
      method: 'POST',
      body: JSON.stringify({architecture: {...architecture, components: normalizedComponents}, tasks, planning_trace: normalizedPlanningTrace, reasoning: String(reasoning || '').trim()}),
    });
    await refresh();
    return {
      ...result,
      built_in_model_called: false,
      context: webMcpContext(),
    };
  },

  async getContext() {
    return webMcpContext();
  },

  async getProjectBrief() {
    await ensureAppInitialized();
    webMcpRequireProject();
    await refresh();
    const summarizeTask = (task) => ({
      id: task.id,
      title: task.title,
      status: task.status,
      owner: task.owner,
      related_component: task.related_component,
    });
    const done = state.tasks.filter((task) => task.status === 'DONE');
    const inProgress = state.tasks.filter((task) => task.status === 'IN_PROGRESS');
    const blocked = state.tasks.filter((task) => task.status === 'BLOCKED');
    const ready = state.tasks.filter((task) => task.status === 'TODO');
    const pending = state.proposals.filter((proposal) => proposal.status === 'PENDING');
    const recentActivity = [...(state.activity || [])].reverse().slice(0, 6).map((event) => ({
      source: event.payload?.external_source || event.source || 'SYSTEM',
      type: event.type,
      summary: event.payload?.summary || event.payload?.message || event.payload?.note || event.type,
    }));
    const architectureStatus = pending.length
      ? 'REVIEW_REQUIRED'
      : blocked.length
        ? 'BLOCKED'
        : inProgress.length
          ? 'ACTIVE'
          : 'ALIGNED';
    const recommendedFocus = pending.length
      ? {kind: 'proposal', id: pending[0].id}
      : blocked.length
        ? {kind: 'task', id: blocked[0].id}
        : null;
    return {
      project: {
        id: state.project.id,
        name: state.project.name,
        status: state.project.status,
        goal: state.project.goal,
      },
      architecture: {
        version: state.architecture.version,
        summary: state.architecture.summary,
        status: architectureStatus,
      },
      execution: {
        counts: {done: done.length, in_progress: inProgress.length, blocked: blocked.length, ready: ready.length},
        done: done.map(summarizeTask),
        in_progress: inProgress.map(summarizeTask),
        blocked: blocked.map(summarizeTask),
        ready: ready.map(summarizeTask),
      },
      recent_activity: recentActivity,
      attention: {
        required: pending.length > 0 || blocked.length > 0,
        pending_reviews: pending.map((proposal) => ({
          id: proposal.id,
          reason: proposal.reason,
          observed_change: proposal.observed_change,
          affected_components: proposal.affected_components || [],
          impact: proposal.impact,
        })),
        blockers: blocked.map(summarizeTask),
        recommended_next_tool: null,
        recommended_human_action: pending.length
          ? 'REVIEW_ARCHITECTURE_PROPOSAL'
          : blocked.length
            ? 'UNBLOCK_TASK'
            : null,
        recommended_focus: recommendedFocus,
      },
      latest_agent_result: state.lastRun,
    };
  },

  async getDecisionContext() {
    await ensureAppInitialized();
    webMcpRequireProject();
    const brief = await window.ArchBroWebBridge.getProjectBrief();
    const componentIds = [];
    const collectIds = (nodes) => {
      for (const node of nodes || []) {
        componentIds.push(node.id);
        collectIds(node.children || []);
      }
    };
    collectIds(state.architecture.components);
    return {
      project_brief: brief,
      architecture: state.architecture,
      tasks: state.tasks,
      recent_activity: [...(state.activity || [])].reverse().slice(0, 10),
      pending_reviews: state.proposals.filter((proposal) => proposal.status === 'PENDING'),
      decision_contract: {
        provider: 'webmcp-agent',
        mode: 'interactive',
        allowed_recommendations: ['KEEP_CURRENT', 'ACCEPT_PROPOSED_CHANGE'],
        existing_component_ids: componentIds,
        rules: [
          'Base the recommendation on the provided project evidence and accepted architecture.',
          'Use KEEP_CURRENT when the issue can be resolved without changing an accepted architecture boundary.',
          'Use ACCEPT_PROPOSED_CHANGE only when evidence justifies a reviewable architecture change.',
          'Submitting a recommendation never approves the architecture change; the human review boundary remains authoritative.',
        ],
      },
    };
  },

  async submitAgentRecommendation({
    recommendation,
    reasoning,
    evidence = [],
    observedChange,
    affectedComponents = [],
    proposedChanges = [],
    impact = '',
    expectedArchitectureVersion,
  } = {}) {
    await ensureAppInitialized();
    webMcpRequireProject();
    const expected = Number(expectedArchitectureVersion);
    if (!Number.isInteger(expected) || expected < 0) throw new Error('expected_architecture_version must be a non-negative integer.');
    const result = await api(`/projects/${state.projectId}/agent-recommendations`, {
      method: 'POST',
      body: JSON.stringify({
        recommendation,
        reasoning,
        evidence,
        observed_change: observedChange,
        affected_components: affectedComponents,
        proposed_changes: proposedChanges,
        impact,
        expected_architecture_version: expected,
      }),
    });
    await refresh();
    if (result?.proposal?.id) {
      state.selectedProposalId = result.proposal.id;
    }
    return {
      ...result,
      context: webMcpContext(),
    };
  },

  async inspectProjectStatus() {
    webMcpRequireProject();
    const blockers = state.tasks.filter((task) => task.status === 'BLOCKED');
    const inProgress = state.tasks.filter((task) => task.status === 'IN_PROGRESS');
    const ready = state.tasks.filter((task) => task.status === 'TODO');
    const pending = state.proposals.filter((proposal) => proposal.status === 'PENDING');
    return {
      project: state.project,
      architecture: state.architecture,
      tasks: state.tasks,
      blockers,
      in_progress: inProgress,
      ready_tasks: ready,
      pending_reviews: pending,
      latest_agent_result: state.lastRun,
    };
  },

  async getRecentActivity({limit = 10} = {}) {
    webMcpRequireProject();
    const boundedLimit = Math.min(50, Math.max(1, Number(limit) || 10));
    const events = await api(`/projects/${state.projectId}/events?limit=${boundedLimit}`);
    state.activity = events;
    renderRecentActivity();
    return {project_id: state.projectId, events, latest_agent_result: state.lastRun};
  },

  async focusPendingReview() {
    await ensureAppInitialized();
    webMcpRequireProject();
    const proposal = state.proposals.find((item) => item.status === 'PENDING') || null;
    if (!proposal) {
      return {focused: false, reason: 'no-pending-review', context: webMcpContext()};
    }
    state.selectedProposalId = proposal.id;
    switchView('attention');
    renderProposals();
    updateInstructionContext();
    return {focused: true, proposal, context: webMcpContext()};
  },

  async inspectArchitecture({componentId = null} = {}) {
    webMcpRequireProject();
    const pending = state.proposals.filter((proposal) => proposal.status === 'PENDING');
    if (!componentId) {
      return {
        project_id: state.projectId,
        architecture: state.architecture,
        tasks: state.tasks,
        pending_proposals: pending,
      };
    }

    const node = findArchitectureNode(componentId);
    if (!node) throw new Error(`Architecture component not found: ${componentId}`);
    const ids = new Set(descendantArchitectureIds(node));
    return {
      project_id: state.projectId,
      architecture_version: state.architecture.version,
      node,
      health: architectureHealth(node),
      tasks: state.tasks.filter((task) => task.related_component && ids.has(task.related_component)),
      pending_proposals: pending.filter((proposal) => {
        const affected = proposal.affected_components || [];
        const changed = (proposal.proposed_changes || []).map((change) => change.component_id).filter(Boolean);
        return [...affected, ...changed].some((id) => ids.has(id));
      }),
    };
  },

  async focusItem({kind, id = null} = {}) {
    webMcpRequireProject();
    if (kind === 'project') {
      if (id && id !== state.projectId) await selectProject(id);
      switchView('overview');
    } else if (kind === 'task') {
      const task = state.tasks.find((item) => item.id === id);
      if (!task) throw new Error(`Task not found: ${id}`);
      state.selectedTaskId = task.id;
      switchView('tasks');
      renderTasks();
    } else if (kind === 'architecture') {
      const node = findArchitectureNode(id);
      if (!node) throw new Error(`Architecture component not found: ${id}`);
      switchView('architecture');
      const parentScopeComponentId = findArchitectureParentId(node.id);
      await navigateGraphScope(parentScopeComponentId ?? null, {focusComponentId: node.id});
      if (diagramNodeByComponentId(node.id)) {
        state.selectedComponentId = node.id;
        state.graphFocusMode = 'connected';
        renderGraph();
      }
    } else if (kind === 'proposal') {
      const proposal = state.proposals.find((item) => item.id === id);
      if (!proposal) throw new Error(`Architecture proposal not found: ${id}`);
      state.selectedProposalId = proposal.id;
      switchView('attention');
      renderProposals();
    } else {
      throw new Error(`Unsupported ArchBro focus kind: ${kind}`);
    }
    updateInstructionContext();
    return webMcpContext();
  },

  async reportChange({summary, evidence = [], relatedComponent = null} = {}) {
    webMcpRequireProject();
    const message = String(summary || '').trim();
    if (!message) throw new Error('Project change summary is required.');
    if (state.architecture.version === 0) throw new Error('Architecture v1 must exist before reporting project changes.');
    const normalizedEvidence = evidence.map((item) => String(item).trim()).filter(Boolean);
    const uiContext = {
      ...currentInstructionContext().payload,
      ...(relatedComponent ? {related_component: relatedComponent} : {}),
    };
    return sendEvent(
      'USER_MESSAGE',
      {message, evidence: normalizedEvidence, ui_context: uiContext},
      'Evaluating WebMCP project change…',
    );
  },

  async createTask({
    requestId,
    title,
    description = '',
    owner = 'UNASSIGNED',
    relatedComponent = null,
    dependencies = [],
    acceptanceCriteria = [],
  } = {}) {
    await ensureAppInitialized();
    webMcpRequireProject();
    const result = await api(`/projects/${state.projectId}/tasks`, {
      method: 'POST',
      body: JSON.stringify({
        request_id: requestId,
        title,
        description,
        owner,
        related_component: relatedComponent,
        dependencies,
        acceptance_criteria: acceptanceCriteria,
      }),
    });
    await refresh();
    return {...result, context: webMcpContext()};
  },

  async recordProjectObservation({
    summary,
    evidence = [],
    relatedComponents = [],
    relatedTaskId = null,
  } = {}) {
    await ensureAppInitialized();
    webMcpRequireProject();
    const result = await api(`/projects/${state.projectId}/observations`, {
      method: 'POST',
      body: JSON.stringify({
        summary,
        evidence,
        related_components: relatedComponents,
        related_task_id: relatedTaskId,
      }),
    });
    await refresh();
    return {...result, context: webMcpContext()};
  },

  async updateTaskStatus({taskId, status} = {}) {
    await ensureAppInitialized();
    webMcpRequireProject();
    const task = state.tasks.find((item) => item.id === taskId);
    if (!task) throw new Error(`Task not found: ${taskId}`);
    if (status === 'IN_PROGRESS' && task.status !== 'TODO') {
      throw new Error(`Task ${taskId} must be TODO before starting.`);
    }
    if (status === 'DONE' && task.status !== 'IN_PROGRESS') {
      throw new Error(`Task ${taskId} must be IN_PROGRESS before completion.`);
    }
    if (!['IN_PROGRESS', 'DONE'].includes(status)) throw new Error(`Unsupported task status: ${status}`);
    const result = await api(`/projects/${state.projectId}/tasks/${encodeURIComponent(taskId)}/status`, {
      method: 'PATCH',
      body: JSON.stringify({status}),
    });
    await refresh();
    return {...result, context: webMcpContext()};
  },

  async decideProposal({proposalId, decision} = {}) {
    webMcpRequireProject();
    const proposal = state.proposals.find((item) => item.id === proposalId);
    if (!proposal) throw new Error(`Architecture proposal not found: ${proposalId}`);
    if (proposal.status !== 'PENDING') throw new Error(`Architecture proposal ${proposalId} is not pending.`);
    if (!['accept', 'reject'].includes(decision)) throw new Error(`Unsupported proposal decision: ${decision}`);
    await decideProposal(proposalId, decision);
    return state.proposals.find((item) => item.id === proposalId) || null;
  },
};

$('nav')?.addEventListener('click', (e) => {
  const btn = e.target.closest('[data-view]');
  if (btn) switchView(btn.dataset.view);
});
$('instructionForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  if (WEBMCP_AGENT_MODE) {
    toast('Built-in Agent messaging is disabled in WebMCP Agent Mode.', true);
    return;
  }
  if (state.architecture?.version === 0) {
    toast('Architecture v1 must finish before normal project updates.', true);
    return;
  }
  const input = $('instruction');
  const message = input.value.trim();
  if (!message) return;
  input.removeAttribute('aria-invalid');
  $('instructionError').textContent = '';
  const context = currentInstructionContext();
  const result = await sendEvent('USER_MESSAGE', {message, ui_context: context.payload});
  if (result?.result === 'SUCCESS') {
    if (input.value.trim() === message) {
      input.value = '';
      syncInstructionRainbowState();
    }
  } else {
    input.setAttribute('aria-invalid', 'true');
    $('instructionError').textContent = 'Instruction not sent. Your text and context are still here—review and press Send to retry.';
    input.focus();
  }
});

$('onboardingForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  await submitOnboardingAsk();
});

$('onboardingAsk').addEventListener('input', () => syncOnboardingAskRainbowState({activate: true}));
$('onboardingAsk').addEventListener('focus', syncOnboardingAskRainbowState);
$('onboardingAsk').addEventListener('blur', syncOnboardingAskRainbowState);
$('instruction').addEventListener('input', () => syncInstructionRainbowState({activate: true}));
$('instruction').addEventListener('focus', syncInstructionRainbowState);
$('instruction').addEventListener('blur', syncInstructionRainbowState);

$('goalDraftText').addEventListener('input', () => {
  state.onboarding.initialGoal = $('goalDraftText').value;
  updateGoalConfirmState();
  if (!state.onboarding.draft) renderGoalDraft();
});
$('useGoalBtn').addEventListener('click', confirmGoalAndGenerate);
$('onboardingBackBtn').addEventListener('click', backToCurrentProject);
$('newProjectNameForm').addEventListener('submit', submitNewProjectName);
$('initialGoalForm').addEventListener('submit', continueToRefinement);
$('initialGoal').addEventListener('input', () => {
  state.onboarding.initialGoal = $('initialGoal').value;
  $('initialGoal').removeAttribute('aria-invalid');
  $('initialGoalError').textContent = '';
});
$('initialGoalBackBtn').addEventListener('click', returnToProjectName);
$('editOnboardingProjectName').addEventListener('click', returnToProjectName);
$('generateArchitectureBtn').addEventListener('click', generateInitialArchitecture);
$('newProjectBtn').addEventListener('click', () => {
  closeMobileSidebar();
  startOnboarding();
});
$('accountMcpConnectionsBtn').addEventListener('click', () => { closeTopMenus(); openMcpConnections(); });
document.querySelectorAll('[data-mcp-preset]').forEach((card) => card.addEventListener('click', async () => selectMcpPreset(card.dataset.mcpPreset)));
document.querySelectorAll('[data-mcp-tab]').forEach((button) => button.addEventListener('click', async () => {
  setMcpPickerTab(button.dataset.mcpTab);
  if (button.dataset.mcpTab === 'connected') {
    try { await loadMcpConnections(); } catch (err) { toast(err.message, true); }
  }
}));
$('mcpSearch').addEventListener('input', (event) => filterMcpProviders(event.target.value));
$('mcpTransport').addEventListener('change', syncMcpTransportFields);
$('mcpOAuthConnectBtn').addEventListener('click', startMcpOAuth);
$('mcpConnectionForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  try {
    await addMcpConnection();
  } catch (err) {
    toast(err.message, true);
  }
});
window.addEventListener('message', async (event) => {
  const trustedOAuthOrigins = new Set([window.location.origin, 'https://archbro-dev.magicdala.com', 'https://archbro.magicdala.com', 'https://archbro-webmcp.magicdala.com']);
  if (!trustedOAuthOrigins.has(event.origin)) return;
  if (activeMcpOAuthPopup && event.source && event.source !== activeMcpOAuthPopup) return;
  const payload = event.data;
  if (!payload || payload.type !== 'archbro-mcp-oauth') return;
  if (event.source) handledMcpOAuthPopups.add(event.source);
  const providerId = payload.provider || mcpOAuthProviderId();
  if (payload.ok) {
    toast(payload.message || 'MCP OAuth connection completed.');
    try {
      if (providerId) await refreshMcpProviderStatusAfterMutation(providerId);
      await loadMcpConnections();
      setMcpPickerTab('connected');
    } catch (err) {
      toast(err.message, true);
    }
  } else {
    toast(payload.message || 'MCP OAuth connection failed.', true);
    if (providerId) await refreshMcpProviderStatusAfterMutation(providerId);
  }
});
$('workspaceSwitcherBtn').addEventListener('click', openPersonalWorkspace);
$('workspaceHomeNewProjectBtn').addEventListener('click', () => {
  closeMobileSidebar();
  startOnboarding();
});
$('mobileSidebarBtn').addEventListener('click', openMobileSidebar);
$('sidebarBackdrop').addEventListener('click', closeMobileSidebar);
$('editProjectForm').addEventListener('submit', async (e) => { e.preventDefault(); await saveProjectEdits(); });
$('deleteProjectForm').addEventListener('submit', async (e) => { e.preventDefault(); await deleteCurrentProject(); });
document.querySelectorAll('[data-close-dialog]').forEach((button) => button.addEventListener('click', () => $(button.dataset.closeDialog).close()));
document.querySelectorAll('dialog').forEach((dialog) => dialog.addEventListener('close', () => {
  const trigger = state.experience.dialogReturnFocus.get(dialog.id);
  state.experience.dialogReturnFocus.delete(dialog.id);
  if (dialog.id === 'authView') {
    if (state.experience.authClosingReturnFocus) (trigger || $('landingAuthTeaser')).focus();
    state.experience.authClosingReturnFocus = true;
    return;
  }
  trigger?.focus?.();
}));
$('authView').addEventListener('click', closeAuthDialogOnBackdrop);
$('authView').addEventListener('cancel', (event) => {
  event.preventDefault();
  closeAuthentication();
});
$('editProjectDialog').addEventListener('click', closeDialogOnBackdrop);
$('newProjectNameDialog').addEventListener('click', closeNewProjectNameDialogOnBackdrop);
$('newProjectNameDialog').addEventListener('cancel', handleNewProjectNameDialogCancel);
$('newProjectNameDialog').addEventListener('close', handleNewProjectNameDialogClose);
document.querySelectorAll('[data-new-project-name-cancel]').forEach((button) => button.addEventListener('click', cancelNewProjectNameDialog));
$('deleteProjectDialog').addEventListener('click', closeDialogOnBackdrop);
$('proposalReviewDialog').addEventListener('click', closeDialogOnBackdrop);
$('accountSettingsDialog').addEventListener('click', closeDialogOnBackdrop);
$('mcpConnectionsDialog').addEventListener('click', closeDialogOnBackdrop);
$('notificationBtn').addEventListener('click', () => toggleTopMenu('notificationBtn', 'notificationMenu'));
$('notificationCloseBtn').addEventListener('click', () => {
  closeTopMenus();
  $('notificationBtn').focus();
});
$('accountBtn').addEventListener('click', () => toggleTopMenu('accountBtn', 'accountMenu'));
$('accountMenu').addEventListener('keydown', (event) => {
  const items = [...$('accountMenu').querySelectorAll('[role="menuitem"]')];
  const current = items.indexOf(document.activeElement);
  let target = null;
  if (event.key === 'ArrowDown') target = items[(current + 1 + items.length) % items.length];
  if (event.key === 'ArrowUp') target = items[(current - 1 + items.length) % items.length];
  if (event.key === 'Home') target = items[0];
  if (event.key === 'End') target = items.at(-1);
  if (event.key === 'Escape') {
    event.preventDefault();
    event.stopPropagation();
    closeTopMenus();
    $('accountBtn').focus();
    return;
  }
  if (event.key === 'Tab') closeTopMenus();
  if (!target) return;
  event.preventDefault();
  target.focus();
});
document.querySelectorAll('[data-account-section]').forEach((button) => button.addEventListener('click', () => openAccountSection(button.dataset.accountSection)));
$('logoutBtn').addEventListener('click', logout);
$('accountSettingsForm').addEventListener('submit', saveAccountSettings);
document.querySelectorAll('[data-settings-panel]').forEach((button) => button.addEventListener('click', () => openAccountSection(button.dataset.settingsPanel)));
document.addEventListener('pointerdown', (event) => {
  const focusableOutsideTarget = event.target.closest('button, [href], input, textarea, select, summary, [tabindex]:not([tabindex="-1"])');
  if (state.openProjectMenuId && !event.target.closest('[data-project-menu], [data-project-menu-panel]')) {
    const keepFocusOnTrigger = !focusableOutsideTarget;
    closeProjectMenuAfterPointerEvent({returnFocus: keepFocusOnTrigger});
  }
  if (!event.target.closest('.top-menu, #notificationBtn, #accountBtn')) closeTopMenus();
});
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && $('newProjectNameDialog').open) {
    event.preventDefault();
    event.stopPropagation();
    cancelNewProjectNameDialog();
    return;
  }
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'n' && !event.target.closest('input, textarea, select, [contenteditable="true"]')) {
    event.preventDefault();
    closeMobileSidebar();
    startOnboarding();
    return;
  }
  if (event.key === 'Escape' && state.openProjectMenuId) {
    event.preventDefault();
    event.stopPropagation();
    closeProjectMenu({returnFocus: true});
    return;
  }
  if (event.key === 'Escape') closeOverlay({returnFocus: true});
});

$('landingLoginBtn').addEventListener('click', (event) => openAuthentication(event.currentTarget));
$('landingAuthTeaser').addEventListener('click', (event) => openAuthentication(event.currentTarget));
$('landingAuthSend').addEventListener('click', (event) => openAuthentication(event.currentTarget));
$('authForm').addEventListener('submit', submitAuthentication);
$('authModeToggle').addEventListener('click', () => setAuthMode(state.experience.authMode === 'signup' ? 'signin' : 'signup'));
$('authCloseBtn').addEventListener('click', () => closeAuthentication());
document.querySelectorAll('[data-password-target]').forEach((button) => button.addEventListener('click', () => togglePasswordVisibility(button)));
document.querySelectorAll('[data-auth-provider]').forEach((button) => button.addEventListener('click', async () => {
  const provider = button.dataset.authProvider;
  const providerSignIn = AUTH_PROVIDER_SIGN_INS.get(provider);
  if (!providerSignIn) {
    writeAuthMessage('This sign-in method is not supported.');
    return;
  }
  if (usesFirebaseAuthentication()) {
    writeAuthMessage('');
    setAuthenticationBusy(true);
    try {
      const identity = await providerSignIn();
      prototype.startSession(localStorage, identity);
      await routeAfterAuthentication();
    } catch (error) {
      writeAuthMessage(authenticationErrorMessage(error, {provider}));
    } finally {
      setAuthenticationBusy(false);
    }
    return;
  }
  prototype.startSession(localStorage, {
    provider,
    email: `${provider}@demo.archbro.local`,
    name: `${provider} user`,
  });
  await routeAfterAuthentication();
}));
wireLensRadioGroup(document.querySelector('.preference-grid'), selectProjectLens);
$('preferenceContinueBtn').addEventListener('click', completePreference);
$('authBackBtn').addEventListener('click', () => closeAuthentication());

wireGoButtons();
document.querySelectorAll('[data-architecture-graph-kind]').forEach((button) => button.addEventListener('click', () => setArchitectureGraphKind(button.dataset.architectureGraphKind)));
window.matchMedia('(max-width: 760px)').addEventListener('change', syncMobileSidebarLayers);
async function initializeWorkspace() {
  try {
    await loadProjects();
    const staleProjectId = state.projectId;
    if (state.projectId && !state.projects.some((project) => project.id === state.projectId)) {
      state.projectId = null;
      localStorage.removeItem('archbro-project-id');
      state.expandedProjectIds.delete(staleProjectId);
      persistExpandedProjectIds();
      if (state.projects.length) {
        state.expandedProjectIds.add(state.projects[0].id);
        await selectProject(state.projects[0].id);
        return true;
      }
    }
    if (state.projectId) {
      state.onboarding.active = false;
      return (await refresh()) !== false;
    }
    if (!state.projectId) {
      if (WEBMCP_AGENT_MODE) {
        state.onboarding.active = true;
        renderOnboarding();
      } else {
        state.onboarding.active = false;
        await loadProjectSnapshots();
        renderWorkspaceHome();
      }
      return true;
    }
  } catch (err) {
    toast(err.message, true);
    return false;
  }
}

async function initializeApp() {
  if (WEBMCP_AGENT_MODE && !usesFirebaseAuthentication()) {
    showExperience('workspace');
    state.experience.workspaceInitialized = await initializeWorkspace();
    syncMobileSidebarLayers();
    return state.experience.workspaceInitialized;
  }
  const hadStoredSession = localStorage.getItem(prototype.KEYS.session) !== null;
  localStorage.removeItem('archbro-pending-goal');
  if (usesFirebaseAuthentication()) {
    let identity = null;
    try {
      identity = await restoreFirebaseIdentity();
    } catch (error) {
      prototype.endSession(localStorage);
      showExperience('landing');
      toast(authenticationErrorMessage(error), true);
      syncMobileSidebarLayers();
      return false;
    }
    if (!identity) {
      prototype.endSession(localStorage);
      showExperience('landing');
      if (WEBMCP_AGENT_MODE) openAuthentication($('landingLoginBtn'));
      syncMobileSidebarLayers();
      return true;
    }
    prototype.startSession(localStorage, identity);
    await routeAfterAuthentication();
    syncMobileSidebarLayers();
    return true;
  }
  const session = prototype.loadSession(localStorage);
  if (!session) {
    showExperience('landing');
    if (hadStoredSession) toast('That local demo session was invalid and has been cleared. Your projects are still available.', true);
    syncMobileSidebarLayers();
    return;
  }
  await routeAfterAuthentication();
  syncMobileSidebarLayers();
  return true;
}

let appInitializationPromise = null;
async function initialize() {
  document.body.dataset.webmcpAgentMode = WEBMCP_AGENT_MODE ? 'true' : 'false';
  document.body.classList.toggle('webmcp-agent-mode', WEBMCP_AGENT_MODE);
  return initializeApp();
}

appInitializationPromise = initialize();

async function ensureAppInitialized() {
  if (appInitializationPromise) await appInitializationPromise;
}
