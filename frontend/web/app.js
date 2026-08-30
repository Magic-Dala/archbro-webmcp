import {getFirebaseIdToken} from './firebase-auth.js';

const prototype = window.ArchbroPrototype;
const storedProjectId = localStorage.getItem('archbro-project-id');
const WEBMCP_AGENT_MODE = new URLSearchParams(window.location.search).get('mode') === 'webmcp';

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
  proposals: [],
  activity: [],
  lastRun: null,
  selectedNode: null,
  drillNodeId: null,
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

const $ = (id) => document.getElementById(id);
const views = {
  overview: {title: 'Project Overview', subtitle: 'Keep project reality aligned with the accepted architecture.'},
  tasks: {title: 'Tasks', subtitle: 'Concrete, actionable work shared by humans and the agent.'},
  architecture: {title: 'Living Graph', subtitle: 'Machine-readable architecture rendered as a living project graph.'},
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

function closeAuthentication({returnFocus = true} = {}) {
  const authDialog = $('authView');
  const shouldReturnToLanding = returnFocus && state.experience.phase === 'auth';
  state.experience.authClosingReturnFocus = returnFocus;
  if (authDialog.open) authDialog.close();
  else if (returnFocus) $('landingAuthTeaser').focus();
  if (shouldReturnToLanding) showExperience('landing');
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
  $('authSubtitle').textContent = signingUp ? 'Create a local demo profile to continue building with Archbro.' : 'Sign in to continue building with Archbro.';
  $('authSubmitBtn').textContent = signingUp ? 'Create account' : 'Continue with email';
  $('authModeToggle').textContent = signingUp ? 'Already have an account? Log in' : 'New to Archbro? Create an account';
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

async function routeAfterAuthentication() {
  const profile = prototype.currentProfile(localStorage);
  if (!profile) {
    state.experience.workspaceInitialized = false;
    closeAuthentication();
    showExperience('landing');
    toast('That local demo session could not be restored. Sign in again to continue.', true);
    return false;
  }
  closeAuthentication({returnFocus: false});
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
  if (Object.keys(errors).length) return;
  prototype.startSession(localStorage, {provider: 'password', email: values.email, name: values.name});
  await routeAfterAuthentication();
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
      snapshots.set(project.id, await api(`/projects/${project.id}/architecture`));
    } catch {
      snapshots.set(project.id, null);
    }
  }));
  state.projectSnapshots = snapshots;
  return snapshots;
}

function snapshotNodes(architecture) {
  const nodes = [];
  const visit = (items = [], depth = 0) => {
    items.forEach((component) => {
      if (nodes.length < 5) nodes.push({...component, depth});
      if (nodes.length < 5) visit(component.children || [], depth + 1);
    });
  };
  visit(architecture?.components || []);
  return nodes;
}

function renderArchitectureSnapshot(architecture) {
  const nodes = snapshotNodes(architecture);
  if (!architecture || Number(architecture.version || 0) < 1 || !nodes.length) {
    return '<div class="project-snapshot project-snapshot-pending"><span class="snapshot-icon" aria-hidden="true">⌁</span><strong>Architecture snapshot pending</strong><small>Generate Living Architecture to see the system map here.</small></div>';
  }
  const positions = nodes.map((node, index) => ({
    node,
    x: 36 + (index % 3) * 86,
    y: 48 + Math.floor(index / 3) * 39,
  }));
  const edges = positions.slice(1).map((position, index) => {
    const source = positions[index];
    return `<line x1="${source.x}" y1="${source.y}" x2="${position.x}" y2="${position.y}" />`;
  }).join('');
  const circles = positions.map(({node, x, y}, index) => {
    const colors = ['#4D416F', '#8B5CF6', '#3B82F6', '#22B96B', '#FF7A66'];
    const label = escapeHtml(String(node.name || 'Area').slice(0, 10));
    return `<g class="snapshot-node"><circle cx="${x}" cy="${y}" r="14" fill="${colors[index % colors.length]}"/><text x="${x}" y="${y + 3}" text-anchor="middle">${label.slice(0, 5)}</text></g>`;
  }).join('');
  return `<div class="project-snapshot project-snapshot-architecture" role="img" aria-label="Living Architecture snapshot version ${escapeHtml(architecture.version)}"><span class="snapshot-label">LIVING ARCHITECTURE · v${escapeHtml(architecture.version)}</span><svg viewBox="0 0 236 132" aria-hidden="true"><g class="snapshot-edges">${edges}</g>${circles}</svg></div>`;
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
    const architecture = state.projectSnapshots.get(project.id);
    const status = projectCardStatus(project, architecture);
    const snapshotMeta = architecture?.version > 0
      ? `Architecture v${architecture.version} · click to open Living Graph`
      : 'Goal saved · architecture pending';
    return `<article class="project-card" data-project-card="${escapeHtml(project.id)}"><button class="project-card-open" type="button" data-project-card-open="${escapeHtml(project.id)}"><div class="project-card-preview">${renderArchitectureSnapshot(architecture)}</div><div class="project-card-body"><div class="project-card-heading"><strong>${escapeHtml(project.name)}</strong><span class="project-card-status ${status.className}">${status.label}</span></div><p>${escapeHtml(snapshotMeta)}</p><span class="project-card-link">Open project <span aria-hidden="true">→</span></span></div></button></article>`;
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
  state.proposals = [];
  state.lastRun = null;
  state.selectedNode = null;
  state.drillNodeId = null;
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

async function loadProjectContext(projectId) {
  const [project, tasks, architecture, proposals, activity] = await Promise.all([
    api(`/projects/${projectId}`),
    api(`/projects/${projectId}/tasks`),
    api(`/projects/${projectId}/architecture`),
    api(`/projects/${projectId}/architecture/proposals`),
    api(`/projects/${projectId}/events?limit=12`),
  ]);
  return {project, tasks, architecture, proposals, activity};
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
      selectedNode: null,
      drillNodeId: null,
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
    Object.assign(state, await loadProjectContext(state.projectId));
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
  state.selectedNode = null;
  state.drillNodeId = null;
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
    state.proposals = [];
    state.lastRun = null;
    state.selectedNode = null;
    state.drillNodeId = null;
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
  state.selectedNode = null;
  state.drillNodeId = null;
  state.selectedTaskId = null;
  state.selectedProposalId = null;
  state.lastRun = null;
  state.projects = [];
  state.project = null;
  state.tasks = [];
  state.architecture = null;
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

function logout() {
  prototype.endSession(localStorage);
  state.experience.workspaceInitialized = false;
  resetEphemeralSessionState();
  showExperience('landing');
  $('landingAuthTeaser').focus();
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
  $('graphVersion').textContent = `v${state.architecture.version}`;
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
}

function selectTaskContext(taskId) {
  state.selectedTaskId = taskId;
  state.selectedProposalId = null;
  state.selectedNode = null;
  renderTasks();
  updateInstructionContext();
  setTimeout(() => document.querySelector(`[data-task-select="${CSS.escape(taskId)}"]`)?.focus(), 0);
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
  return `<div class="task-row${selected ? ' context-selected' : ''}"><i class="status-dot ${statusClass(t.status)}"></i><div>${content}</div><span class="status-pill ${t.status}">${t.status.replace('_', ' ')}</span></div>`;
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
    state.selectedNode = null;
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

function architectureLineage(id) {
  const result = [];
  const visit = (nodes, trail) => {
    for (const node of nodes || []) {
      const next = [...trail, node];
      if (node.id === id) {
        result.push(...next);
        return true;
      }
      if (visit(node.children || [], next)) return true;
    }
    return false;
  };
  visit(state.architecture?.components || [], []);
  return result;
}

function parentArchitectureNodeId(id) {
  const lineage = architectureLineage(id);
  return lineage.length > 1 ? lineage[lineage.length - 2].id : null;
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

function renderArchitectureDrilldown(parent) {
  const canvas = $('graphCanvas');
  const children = parent.children || [];
  const parentHealth = architectureHealth(parent);
  const lineage = architectureLineage(parent.id);
  const breadcrumb = ['Overview', ...lineage.map((node) => node.name)].map((label, index) => `<span class="drill-crumb ${index === lineage.length ? 'current' : ''}">${escapeHtml(label)}</span>`).join('<span class="drill-sep">/</span>');
  const cards = children.map((child) => {
    const health = architectureHealth(child);
    const nestedCount = (child.children || []).length;
    const clickable = health.needsAttention || nestedCount > 0;
    const selected = state.selectedNode === child.id;
    const cta = health.needsAttention ? 'Inspect issue' : nestedCount ? `Open ${nestedCount} detail${nestedCount === 1 ? '' : 's'}` : 'No action needed';
    return `<button type="button" class="drill-card health-${health.key}${selected ? ' selected' : ''}" data-detail-node="${escapeHtml(child.id)}" data-clickable="${clickable}">
      <span class="drill-card-top"><small>${escapeHtml(child.kind || child.type || 'COMPONENT')}</small><span class="health-pill health-${health.key}">${escapeHtml(health.label)}</span></span>
      <strong>${escapeHtml(child.name)}</strong>
      <p>${escapeHtml(child.responsibility)}</p>
      <span class="drill-card-footer"><span>${escapeHtml(health.detail)}</span><b>${escapeHtml(cta)}</b></span>
    </button>`;
  }).join('');
  const childCarriesReview = children.some((child) => architectureHealth(child).pendingReviews.length);
  const ownIssue = parentHealth.pendingReviews.length && !childCarriesReview
    ? `<div class="drill-boundary-alert"><strong>This boundary itself needs review.</strong><span>The pending proposal is attached to ${escapeHtml(parent.name)}, not a specific child.</span></div>`
    : '';
  canvas.innerHTML = `<div class="graph-drilldown">
    <div class="drill-toolbar"><div><div class="drill-breadcrumb">${breadcrumb}</div><h4>${escapeHtml(parent.name)}</h4><p>${escapeHtml(parent.responsibility)}</p></div><button type="button" class="btn secondary drill-back">← ${parentArchitectureNodeId(parent.id) ? 'Back one level' : 'Back to overview'}</button></div>
    ${ownIssue}
    <div class="drill-grid">${cards || '<div class="drill-empty">No deeper architecture is needed for this component.</div>'}</div>
  </div>`;
  canvas.querySelector('.drill-back')?.addEventListener('click', () => {
    const nextDrillNodeId = parentArchitectureNodeId(parent.id);
    state.selectedNode = null;
    state.drillNodeId = nextDrillNodeId;
    renderGraph();
    setTimeout(() => {
      const target = nextDrillNodeId
        ? document.querySelector('.drill-back')
        : document.querySelector(`[data-graph-drill="${CSS.escape(parent.id)}"]`);
      target?.focus();
    }, 0);
  });
  canvas.querySelectorAll('[data-detail-node]').forEach((el) => el.addEventListener('click', () => {
    const node = findArchitectureNode(el.dataset.detailNode);
    if (!node) return;
    state.selectedNode = node.id;
    state.selectedTaskId = null;
    state.selectedProposalId = null;
    const drilling = Boolean((node.children || []).length);
    if (drilling) state.drillNodeId = node.id;
    renderGraph();
    setTimeout(() => (drilling
      ? document.querySelector('.drill-back')
      : document.querySelector(`[data-detail-node="${CSS.escape(node.id)}"]`))?.focus(), 0);
  }));
  renderSelectedNode();
  renderLists();
  updateInstructionContext();
}

function renderGraph() {
  const a = state.architecture;
  const canvas = $('graphCanvas');
  if (!a.components.length) {
    canvas.innerHTML = '<div class="architecture-empty-state"><div><strong>No architecture yet</strong><p class="muted">Architecture v1 has not completed.</p></div></div>';
    renderLists();
    return;
  }
  if (state.drillNodeId && !findArchitectureNode(state.drillNodeId)) state.drillNodeId = null;
  const drillNode = findArchitectureNode(state.drillNodeId);
  if (drillNode && (drillNode.children || []).length) {
    renderArchitectureDrilldown(drillNode);
    return;
  }
  const nodeW = 224, nodeH = 148, gapX = 78, gapY = 42, padX = 42, padY = 72;
  const byId = new Map(a.components.map((c) => [c.id, c]));
  const indegree = new Map(a.components.map((c) => [c.id, 0]));
  const outgoing = new Map(a.components.map((c) => [c.id, []]));
  a.relationships.forEach((r) => {
    if (!byId.has(r.source) || !byId.has(r.target) || r.source === r.target) return;
    outgoing.get(r.source).push(r.target);
    indegree.set(r.target, (indegree.get(r.target) || 0) + 1);
  });

  const depth = new Map(a.components.map((c) => [c.id, 0]));
  const queue = a.components.filter((c) => indegree.get(c.id) === 0).map((c) => c.id);
  const visited = new Set();
  while (queue.length) {
    const id = queue.shift();
    if (visited.has(id)) continue;
    visited.add(id);
    (outgoing.get(id) || []).forEach((target) => {
      depth.set(target, Math.max(depth.get(target) || 0, (depth.get(id) || 0) + 1));
      indegree.set(target, indegree.get(target) - 1);
      if (indegree.get(target) === 0) queue.push(target);
    });
  }
  a.components.filter((c) => !visited.has(c.id)).forEach((c, i) => depth.set(c.id, i % Math.max(1, Math.ceil(Math.sqrt(a.components.length)))));

  const layerCount = Math.max(...[...depth.values()]) + 1;
  const layers = Array.from({length: layerCount}, () => []);
  a.components.forEach((c) => layers[depth.get(c.id) || 0].push(c));
  const maxRows = Math.max(1, ...layers.map((layer) => layer.length));
  const maxLayerHeight = maxRows * nodeH + Math.max(0, maxRows - 1) * gapY;
  const W = Math.max(780, padX * 2 + layerCount * nodeW + Math.max(0, layerCount - 1) * gapX);
  const H = Math.max(470, 96 + maxLayerHeight + 52);
  const positions = {};
  layers.forEach((layer, layerIndex) => {
    const layerHeight = layer.length * nodeH + Math.max(0, layer.length - 1) * gapY;
    const startY = 78 + (maxLayerHeight - layerHeight) / 2;
    layer.forEach((c, row) => {
      positions[c.id] = {x: padX + layerIndex * (nodeW + gapX), y: startY + row * (nodeH + gapY), c};
    });
  });

  const wrap = (text, maxChars, maxLines = 2) => {
    const words = String(text || '').split(/\s+/).filter(Boolean);
    const lines = [];
    let line = '';
    words.forEach((word) => {
      if (lines.length >= maxLines) return;
      const next = line ? `${line} ${word}` : word;
      if (next.length > maxChars && line) {
        lines.push(line);
        line = word;
      } else line = next;
    });
    if (line && lines.length < maxLines) lines.push(line);
    if (words.length && lines.length === maxLines && lines.join(' ').length < String(text).length - 2) {
      lines[maxLines - 1] = `${lines[maxLines - 1].replace(/[.…]+$/, '')}…`;
    }
    return lines;
  };
  const palette = (c) => {
    const text = `${c.type} ${c.name}`;
    if (/agent|model|\bai\b|adk|orchestrat/i.test(text)) return {fill:'#F4F1FB', stroke:'#B8AADD', accent:'#5A49B8', tag:'#CBC3E3'};
    if (/data|database|storage|state|sql|firestore/i.test(text)) return {fill:'#F8F7F4', stroke:'#D8D4CC', accent:'#5E5A53', tag:'#EEECE7'};
    if (/front|web|ui|client/i.test(text)) return {fill:'#F2F7FF', stroke:'#3B82F6', accent:'#245FAE', tag:'#E4EFFF'};
    if (/cloud|infra|deploy|service/i.test(text)) return {fill:'#FAF9F7', stroke:'#DDD8CF', accent:'#615C54', tag:'#F1EEE8'};
    return {fill:'#FFFFFF', stroke:'#D8D5DD', accent:'#625E68', tag:'#F2F0F3'};
  };

  const layerTitle = (layer, index) => {
    const text = layer.map((c) => `${c.type} ${c.name}`).join(' ');
    if (/front|web|ui|client/i.test(text)) return 'EXPERIENCE';
    if (/agent|model|\bai\b|adk|orchestrat/i.test(text)) return 'AGENT & ORCHESTRATION';
    if (/data|database|storage|state|sql|firestore/i.test(text)) return 'DATA & STATE';
    if (/cloud|infra|deploy/i.test(text)) return 'CLOUD & INFRA';
    if (/domain|search|recommend|listing|catalog|service|backend|api/i.test(text)) return 'DOMAIN & SERVICES';
    return `LAYER ${index + 1}`;
  };
  const layerHeaders = layers.map((layer, index) => {
    const x = padX + index * (nodeW + gapX);
    return `<g><text x="${x + nodeW / 2}" y="38" text-anchor="middle" font-size="9.5" font-weight="850" letter-spacing="1.1" fill="#6F6B78">${escapeHtml(layerTitle(layer, index))}</text><line x1="${x}" y1="51" x2="${x + nodeW}" y2="51" stroke="#E6E3EA" stroke-width="1"/></g>`;
  }).join('');

  const edges = a.relationships.map((r, index) => {
    const s = positions[r.source], t = positions[r.target];
    if (!s || !t) return '';
    const sx = s.x + nodeW, sy = s.y + nodeH / 2, tx = t.x, ty = t.y + nodeH / 2;
    const sameOrBack = tx <= sx;
    const bend = sameOrBack ? Math.max(sx, tx) + 42 + index * 4 : (sx + tx) / 2;
    const path = sameOrBack
      ? `M ${sx} ${sy} C ${bend} ${sy}, ${bend} ${ty}, ${tx} ${ty}`
      : `M ${sx} ${sy} C ${bend} ${sy}, ${bend} ${ty}, ${tx} ${ty}`;
    const label = escapeHtml(r.relationship_type || 'relates to');
    const lx = sameOrBack ? bend : (sx + tx) / 2;
    const ly = (sy + ty) / 2 - 9;
    const labelW = Math.max(54, Math.min(128, label.length * 6.2 + 18));
    return `<g class="graph-edge"><path d="${path}" fill="none" stroke="#9aa7b7" stroke-width="2" marker-end="url(#arrow)"/><rect x="${lx - labelW / 2}" y="${ly - 11}" width="${labelW}" height="22" rx="11" fill="#ffffff" stroke="#e2e8f0"/><text x="${lx}" y="${ly + 4}" text-anchor="middle" font-size="9.5" font-weight="700" fill="#667588">${label}</text></g>`;
  }).join('');

  const nodes = a.components.map((c) => {
    const p = positions[c.id];
    const selected = state.selectedNode === c.id;
    const baseColors = palette(c);
    const health = architectureHealth(c);
    const colors = healthVisual(health, baseColors);
    const nameLines = wrap(c.name, 24, 2);
    const responsibilityLines = wrap(c.responsibility, 32, 2);
    const nameText = nameLines.map((line, i) => `<text x="${p.x + 18}" y="${p.y + 49 + i * 17}" font-size="13.5" font-weight="800" fill="#1D1D1F">${escapeHtml(line)}</text>`).join('');
    const responsibilityText = responsibilityLines.map((line, i) => `<text x="${p.x + 18}" y="${p.y + 91 + i * 14}" font-size="10.2" fill="#64748b">${escapeHtml(line)}</text>`).join('');
    const type = escapeHtml(c.type || 'Component');
    const status = escapeHtml(health.label);
    const componentTasks = state.tasks.filter((t) => t.related_component === c.id);
    const doneTasks = componentTasks.filter((t) => t.status === 'DONE').length;
    const activeTasks = componentTasks.filter((t) => t.status === 'IN_PROGRESS').length;
    const childCount = (c.children || []).length;
    const taskText = health.needsAttention
      ? health.detail
      : childCount
      ? `${health.detail} · ${childCount} detail${childCount === 1 ? '' : 's'}`
      : componentTasks.length
      ? `${doneTasks}/${componentTasks.length} done${activeTasks ? ` · ${activeTasks} active` : ''}`
      : health.detail;
    const progress = componentTasks.length ? Math.round((doneTasks / componentTasks.length) * 100) : 0;
    const visibleProgress = progress || (activeTasks ? 18 : 0);
    const clickable = health.needsAttention || childCount > 0;
    return `<g class="node-card health-${health.key}${health.needsAttention ? ' attention' : ''}" data-node="${escapeHtml(c.id)}" data-clickable="${clickable}">
      <rect x="${p.x}" y="${p.y}" width="${nodeW}" height="${nodeH}" rx="16" fill="${colors.fill}" stroke="${selected ? '#4D416F' : colors.stroke}" stroke-width="${selected ? 2.8 : health.needsAttention ? 2.2 : 1.5}"/>
      <rect x="${p.x + 14}" y="${p.y + 13}" width="${Math.min(112, type.length * 6 + 18)}" height="21" rx="10.5" fill="${baseColors.tag}"/>
      <text x="${p.x + 24}" y="${p.y + 27}" font-size="9.2" font-weight="800" fill="${baseColors.accent}">${type}</text>
      <circle cx="${p.x + nodeW - 70}" cy="${p.y + 23.5}" r="4" fill="${health.key === 'healthy' ? '#237A57' : health.key === 'active' ? '#8B5CF6' : colors.accent}"/>
      <text x="${p.x + nodeW - 60}" y="${p.y + 27}" font-size="8.8" font-weight="800" fill="${health.needsAttention ? colors.accent : '#667588'}">${status}</text>
      ${nameText}${responsibilityText}
      <text x="${p.x + 18}" y="${p.y + 124}" font-size="8.8" font-weight="750" fill="#738095">${escapeHtml(taskText)}</text>
      <rect x="${p.x + 18}" y="${p.y + 133}" width="${nodeW - 36}" height="5" rx="2.5" fill="#e8edf3"/>
      <rect x="${p.x + 18}" y="${p.y + 133}" width="${Math.max(0, (nodeW - 36) * visibleProgress / 100)}" height="5" rx="2.5" fill="${colors.accent}"/>
    </g>`;
  }).join('');
  const activeTaskCount = state.tasks.filter((t) => t.status === 'IN_PROGRESS').length;
  const attentionRoots = a.components.filter((component) => architectureHealth(component).needsAttention);
  const interactiveNodes = a.components.filter((component) => architectureHealth(component).needsAttention || (component.children || []).length);
  const nodeControls = interactiveNodes.length
    ? `<div class="graph-node-controls" role="group" aria-label="Interactive architecture areas"><span>Architecture controls</span>${interactiveNodes.map((node) => `<span class="graph-node-control"><button type="button" data-graph-node="${escapeHtml(node.id)}" aria-pressed="${state.selectedNode === node.id}">Use ${escapeHtml(node.name)} as Agent context · ${escapeHtml(architectureHealth(node).label)}</button>${(node.children || []).length ? `<button type="button" data-graph-drill="${escapeHtml(node.id)}">Inspect ${escapeHtml(node.name)} details</button>` : ''}</span>`).join('')}</div>`
    : '';
  $('graphReviewState').textContent = attentionRoots.length ? `${attentionRoots.length} area${attentionRoots.length === 1 ? '' : 's'} need attention` : 'All top-level areas aligned';
  canvas.innerHTML = `<div class="graph-meta"><span>${a.components.length} top-level areas</span><span>${a.relationships.length} relationships</span><span>${activeTaskCount} task${activeTaskCount === 1 ? '' : 's'} active</span><span>Accepted v${a.version}</span>${attentionRoots.length ? `<span class="graph-meta-attention">${attentionRoots.length} need attention</span>` : '<span class="graph-meta-ok">No action needed</span>'}</div>${nodeControls}<svg class="architecture-graph-svg" viewBox="0 0 ${W} ${H}" width="${Math.min(W, 860)}" height="${H}" aria-hidden="true" focusable="false"><defs><marker id="arrow" markerWidth="9" markerHeight="9" refX="8" refY="4.5" orient="auto"><path d="M1 1 L8 4.5 L1 8 Z" fill="#8b98a9"/></marker></defs>${layerHeaders}${edges}${nodes}</svg>`;
  canvas.querySelectorAll('[data-node][data-clickable="true"]').forEach((el) => el.addEventListener('click', () => {
    const node = findArchitectureNode(el.dataset.node);
    if (!node) return;
    state.selectedNode = node.id;
    state.selectedTaskId = null;
    state.selectedProposalId = null;
    if ((node.children || []).length) state.drillNodeId = node.id;
    renderGraph();
  }));
  canvas.querySelectorAll('[data-graph-node]').forEach((button) => button.addEventListener('click', () => {
    const nodeId = button.dataset.graphNode;
    if (!findArchitectureNode(nodeId)) return;
    state.selectedNode = nodeId;
    state.selectedTaskId = null;
    state.selectedProposalId = null;
    renderGraph();
    setTimeout(() => document.querySelector(`[data-graph-node="${CSS.escape(nodeId)}"]`)?.focus(), 0);
  }));
  canvas.querySelectorAll('[data-graph-drill]').forEach((button) => button.addEventListener('click', () => {
    const nodeId = button.dataset.graphDrill;
    const node = findArchitectureNode(nodeId);
    if (!node || !(node.children || []).length) return;
    state.selectedNode = nodeId;
    state.selectedTaskId = null;
    state.selectedProposalId = null;
    state.drillNodeId = nodeId;
    renderGraph();
    setTimeout(() => document.querySelector('.drill-back')?.focus(), 0);
  }));
  renderSelectedNode();
  renderLists();
  updateInstructionContext();
}

function renderSelectedNode() {
  const c = findArchitectureNode(state.selectedNode);
  if (!c) {
    const attentionRoots = (state.architecture?.components || []).filter((component) => architectureHealth(component).needsAttention);
    $('selectedNode').innerHTML = attentionRoots.length
      ? `<small>SYSTEM HEALTH</small><h3>${attentionRoots.length} area${attentionRoots.length === 1 ? '' : 's'} need attention</h3><p>Only the colored problem areas require inspection. Click one to locate the blocked child, task, dependency, or architecture decision.</p>`
      : '<small>SYSTEM HEALTH</small><h3>Everything is aligned</h3><p>No top-level area needs your attention. You can ignore the graph until a boundary changes, a task blocks, or the Agent raises an architecture review.</p>';
    $('nodeEvidence').innerHTML = attentionRoots.length
      ? `<p><strong>What should I inspect?</strong></p><p class="muted">${attentionRoots.map((root) => `${escapeHtml(root.name)} — ${escapeHtml(architectureHealth(root).detail)}`).join('<br>')}</p>`
      : '<p><strong>No action needed</strong></p><p class="muted">Healthy areas stay quiet. Human attention is reserved for blocked work or architecture decisions.</p>';
    return;
  }
  const health = architectureHealth(c);
  const ids = new Set(descendantArchitectureIds(c));
  const incoming = state.architecture.relationships.filter((r) => r.target === c.id);
  const outgoing = state.architecture.relationships.filter((r) => r.source === c.id);
  const linkedTasks = state.tasks.filter((t) => t.related_component && ids.has(t.related_component));
  const connectionLine = (r, direction) => {
    const peerId = direction === 'in' ? r.source : r.target;
    const peer = findArchitectureNode(peerId);
    return `<li><strong>${direction === 'in' ? 'From' : 'To'} ${escapeHtml(peer?.name || peerId)}</strong><span>${escapeHtml(r.relationship_type)}${r.description ? ` · ${escapeHtml(r.description)}` : ''}</span></li>`;
  };
  const childSummary = (c.children || []).length ? `<div class="component-children-summary"><strong>${c.children.length} detailed area${c.children.length === 1 ? '' : 's'}</strong>${c.children.map((child) => { const childHealth = architectureHealth(child); return `<span class="health-${childHealth.key}">${escapeHtml(child.name)} · ${escapeHtml(childHealth.label)}</span>`; }).join('')}</div>` : '';
  $('selectedNode').innerHTML = `<small>SELECTED COMPONENT · ${escapeHtml(c.kind || c.type)}</small><div class="selected-node-title"><h3>${escapeHtml(c.name)}</h3><span class="health-pill health-${health.key}">${escapeHtml(health.label)}</span></div><p>${escapeHtml(c.responsibility)}</p>${childSummary}<div class="component-task-summary"><strong>${linkedTasks.length} linked task${linkedTasks.length === 1 ? '' : 's'}</strong>${linkedTasks.length ? linkedTasks.map((t) => `<span><i class="status-dot ${statusClass(t.status)}"></i>${escapeHtml(t.title)} · ${escapeHtml(t.status.replace('_', ' '))}</span>`).join('') : '<span class="muted">No execution task is linked to this component yet.</span>'}</div><div class="component-connections">${incoming.length || outgoing.length ? `<ul>${incoming.map((r) => connectionLine(r, 'in')).join('')}${outgoing.map((r) => connectionLine(r, 'out')).join('')}</ul>` : '<p class="muted">No explicit relationships recorded at this level.</p>'}</div>`;
  const healthReason = health.key === 'blocked'
    ? `This area is blocked because ${health.detail}.`
    : health.key === 'review'
    ? `Human review is required: ${health.detail}.`
    : health.key === 'active'
    ? `Work is progressing normally: ${health.detail}.`
    : 'No blocked task or pending architecture decision maps to this area.';
  $('nodeEvidence').innerHTML = `<p><strong>${escapeHtml(health.label)}</strong></p><p class="muted">${escapeHtml(healthReason)}</p><p><strong>Accepted responsibility</strong></p><p class="muted">${escapeHtml(c.responsibility)}</p><p><strong>Node ID</strong></p><p class="muted">${escapeHtml(c.id)}</p><p><strong>Architecture status</strong></p><p class="muted">${escapeHtml(c.status)} — sourced from Architecture v${state.architecture.version}.</p>`;
}

function renderLists() {
  const list = (items, empty) => items?.length ? `<ul>${items.map((x) => `<li>${escapeHtml(x)}</li>`).join('')}</ul>` : `<p class="muted">${empty}</p>`;
  $('decisionList').innerHTML = list(state.architecture.decisions, 'No recorded architecture decisions.');
  $('riskList').innerHTML = list([...(state.architecture.risks || []), ...(state.architecture.assumptions || []).map((x) => `Assumption: ${x}`)], 'No recorded risks or assumptions.');
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
    const node = findArchitectureNode(state.selectedNode || state.drillNodeId);
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
    state.selectedNode = null;
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
  const selectedNode = findArchitectureNode(state.selectedNode || state.drillNodeId);
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

window.ArchBroWebBridge = {
  async bootstrapProject({name, goal, architectureSummary, components = [], relationships = [], tasks = [], reasoning} = {}) {
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

    const componentIds = new Map();
    const usedIds = new Set();
    const normalizedComponents = components.map((component, index) => {
      const componentName = String(component?.name || '').trim();
      const componentType = String(component?.type || '').trim();
      const responsibility = String(component?.responsibility || '').trim();
      if (!componentName || !componentType || !responsibility) throw new Error('Every component requires name, type, and responsibility.');
      const baseId = componentName.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '') || `component-${index + 1}`;
      let id = baseId;
      let suffix = 2;
      while (usedIds.has(id)) id = `${baseId}-${suffix++}`;
      usedIds.add(id);
      componentIds.set(componentName.toLowerCase(), id);
      return {id, name: componentName, type: componentType, responsibility, children: []};
    });
    const resolveComponent = (value) => componentIds.get(String(value || '').trim().toLowerCase()) || String(value || '').trim();
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
        body: JSON.stringify({architecture, tasks: normalizedTasks, reasoning: bootstrapReasoning}),
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
          'Generate Architecture v1 from the stored Goal/Brief using the current WebMCP host model.',
          'Use stable component ids that tasks can reference.',
          'Create at least one actionable initial task.',
          'Submit the result with archbro_submit_initial_architecture.',
        ],
      },
      recommended_next_tool: 'archbro_submit_initial_architecture',
    };
  },

  async submitInitialArchitecture({architecture, tasks = [], reasoning} = {}) {
    webMcpRequireProject();
    if (!architecture || typeof architecture !== 'object') throw new Error('Architecture v1 is required.');
    if (!Array.isArray(tasks) || !tasks.length) throw new Error('At least one initial task is required.');
    const result = await api(`/projects/${state.projectId}/interactive-initial-architecture`, {
      method: 'POST',
      body: JSON.stringify({architecture, tasks, reasoning: String(reasoning || '').trim()}),
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
        recommended_next_tool: pending.length
          ? 'archbro_focus_pending_review'
          : blocked.length
            ? 'archbro_focus_workspace_item'
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
  } = {}) {
    await ensureAppInitialized();
    webMcpRequireProject();
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
      state.selectedNode = node.id;
      state.drillNodeId = null;
      switchView('architecture');
      renderGraph();
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
    await updateTask(taskId, status === 'DONE' ? 'done' : 'start');
    return {
      task: state.tasks.find((item) => item.id === taskId) || null,
      agent_run: state.lastRun,
    };
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
  if (WEBMCP_AGENT_MODE) {
    showExperience('workspace');
    state.experience.workspaceInitialized = await initializeWorkspace();
    syncMobileSidebarLayers();
    return state.experience.workspaceInitialized;
  }
  const hadStoredSession = localStorage.getItem(prototype.KEYS.session) !== null;
  const session = prototype.loadSession(localStorage);
  localStorage.removeItem('archbro-pending-goal');
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
