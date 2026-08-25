const storedProjectId = localStorage.getItem('archbro-project-id');
const state = {
  projectId: storedProjectId,
  projects: [],
  project: null,
  tasks: [],
  architecture: null,
  proposals: [],
  lastRun: null,
  selectedNode: null,
  drillNodeId: null,
  selectedTaskId: null,
  selectedProposalId: null,
  currentView: 'overview',
  taskUpdating: new Set(),
  onboarding: {
    active: !storedProjectId,
    messages: [],
    draft: null,
    working: false,
    workingStartedAt: null,
    workingTimer: null,
    lastError: null,
  },
};

const $ = (id) => document.getElementById(id);
const views = {
  overview: {title: 'Overview', subtitle: 'Keep project reality aligned with the accepted architecture.'},
  tasks: {title: 'Tasks', subtitle: 'Concrete, actionable work shared by humans and the agent.'},
  architecture: {title: 'Living Graph', subtitle: 'Machine-readable architecture rendered as a living project graph.'},
  attention: {title: 'Needs You', subtitle: 'Only meaningful architecture approvals cross this boundary.'},
};

async function api(path, options = {}) {
  const {timeoutMs = 0, headers = {}, ...fetchOptions} = options;
  const controller = timeoutMs ? new AbortController() : null;
  const timeout = controller ? setTimeout(() => controller.abort(), timeoutMs) : null;
  try {
    const res = await fetch(path, {
      headers: {'Content-Type': 'application/json', ...headers},
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
  renderProjectControls();
  return state.projects;
}

function renderProjectControls() {
  const select = $('projectSelect');
  if (!select) return;
  select.innerHTML = state.projects.length
    ? state.projects.map((project) => `<option value="${escapeHtml(project.id)}"${project.id === state.projectId ? ' selected' : ''}>${escapeHtml(project.name)}</option>`).join('')
    : '<option value="">No project</option>';
  select.disabled = !state.projects.length;
  const hasCurrent = Boolean(state.projectId && state.projects.some((project) => project.id === state.projectId));
  $('editProjectBtn').disabled = !hasCurrent;
  $('deleteProjectBtn').disabled = !hasCurrent;
}

async function selectProject(projectId) {
  if (!projectId || (projectId === state.projectId && !state.onboarding.active && state.project)) return;
  state.projectId = projectId;
  state.project = null;
  state.lastRun = null;
  state.selectedNode = null;
  state.drillNodeId = null;
  state.currentView = 'overview';
  state.onboarding.active = false;
  localStorage.setItem('archbro-project-id', projectId);
  renderProjectControls();
  await refresh();
}

async function refresh() {
  if (state.onboarding.active || !state.projectId) {
    renderOnboarding();
    return;
  }
  try {
    const [project, tasks, architecture, proposals] = await Promise.all([
      api(`/projects/${state.projectId}`),
      api(`/projects/${state.projectId}/tasks`),
      api(`/projects/${state.projectId}/architecture`),
      api(`/projects/${state.projectId}/architecture/proposals`),
    ]);
    Object.assign(state, {project, tasks, architecture, proposals});
    render();
  } catch (err) {
    if (String(err.message).startsWith('404:')) {
      localStorage.removeItem('archbro-project-id');
      state.projectId = null;
      state.project = null;
      await loadProjects();
      if (state.projects.length) {
        await selectProject(state.projects[0].id);
      } else {
        state.onboarding.active = true;
        renderOnboarding();
      }
    } else {
      toast(err.message, true);
    }
  }
}

function startOnboarding() {
  if (state.onboarding.workingTimer) clearInterval(state.onboarding.workingTimer);
  state.currentView = 'overview';
  state.onboarding = {
    active: true,
    messages: [],
    draft: null,
    working: false,
    workingStartedAt: null,
    workingTimer: null,
    lastError: null,
  };
  renderOnboarding();
  setTimeout(() => $('onboardingAsk').focus(), 30);
}

function renderOnboarding() {
  $('emptyState').classList.remove('hidden');
  $('workspace').classList.add('hidden');
  renderProjectControls();
  $('pageTitle').textContent = 'New Project';
  $('pageSubtitle').textContent = 'Write the Goal directly or use Ask to refine it. Both update the same project brief.';
  document.querySelectorAll('.nav-item').forEach((n) => n.classList.remove('active'));
  $('onboardingBackBtn').classList.toggle('hidden', !state.projectId);
  renderOnboardingConversation();
  renderGoalDraft();
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
    el.innerHTML = '<div class="onboarding-empty">Describe the product, problem, or outcome you want. You can also write the Goal directly on the right.</div>';
    return;
  }

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
  const nameInput = $('goalProjectName');
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
  if (document.activeElement !== nameInput) nameInput.value = draft.suggested_project_name || nameInput.value || 'Untitled Project';
  if (document.activeElement !== goalInput) goalInput.value = draft.goal || '';
  const missing = draft.missing_information || [];
  $('missingInfoWrap').classList.toggle('hidden', !missing.length);
  $('missingInfo').innerHTML = missing.map((item) => `<li>${escapeHtml(item)}</li>`).join('');
  updateGoalConfirmState();
}

function updateGoalConfirmState() {
  const hasGoal = Boolean($('goalDraftText').value.trim());
  const hasName = Boolean($('goalProjectName').value.trim());
  $('useGoalBtn').disabled = !(hasGoal && hasName) || state.onboarding.working;
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
  if (state.onboarding.working) return;
  const input = $('onboardingAsk');
  const content = input.value.trim();
  if (!content) return;
  input.value = '';
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
  const name = $('goalProjectName').value.trim();
  const goal = $('goalDraftText').value.trim();
  if (!name || !goal || state.onboarding.working) return;
  try {
    setWorking(true, 'Creating project…');
    const project = await api('/projects', {
      method: 'POST',
      body: JSON.stringify({name, goal, description: 'Goal drafted through Goal + Ask onboarding.'}),
    });
    state.projectId = project.id;
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

function openEditProject() {
  if (!state.project) return;
  $('editProjectName').value = state.project.name;
  $('editProjectGoal').value = state.project.goal;
  $('editProjectDescription').value = state.project.description || '';
  const lockedGoal = (state.architecture?.version || 0) > 0;
  $('editProjectGoal').disabled = lockedGoal;
  $('editGoalHint').classList.toggle('hidden', !lockedGoal);
  $('editProjectDialog').showModal();
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

function openDeleteProject() {
  if (!state.project) return;
  $('deleteProjectName').textContent = state.project.name;
  $('deleteProjectDialog').showModal();
}

async function deleteCurrentProject() {
  if (!state.projectId) return;
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
    localStorage.removeItem('archbro-project-id');
    await loadProjects();
    if (state.projects.length) {
      await selectProject(state.projects[0].id);
    } else {
      state.onboarding.active = true;
      renderOnboarding();
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

function render() {
  $('emptyState').classList.add('hidden');
  $('workspace').classList.remove('hidden');
  renderProjectControls();
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

  $('readyCount').textContent = `${ready.length} ready task${ready.length === 1 ? '' : 's'}`;
  $('readySub').textContent = ready[0]?.title || (awaiting ? 'Architecture generation pending' : 'No actionable human task yet');
  $('runningCount').textContent = `${running.length} in progress`;
  $('archVersion').textContent = `Version ${state.architecture.version}`;
  $('archState').textContent = state.architecture.components.length ? 'Machine-readable source of truth' : 'Goal saved; Architecture v1 pending';
  $('needsCount').textContent = `${pending.length} review${pending.length === 1 ? '' : 's'}`;
  $('attentionBadge').textContent = pending.length;
  $('attentionBadge').classList.toggle('hidden', !pending.length);
  $('graphVersion').textContent = `v${state.architecture.version}`;
  $('graphReviewState').textContent = pending.length ? `${pending.length} item${pending.length === 1 ? '' : 's'} need review` : 'Aligned';

  const aligned = state.architecture.components.length && !pending.length;
  $('alignmentFill').style.width = state.architecture.components.length ? (pending.length ? '72%' : '100%') : '0%';
  $('alignmentText').textContent = state.architecture.components.length ? (aligned ? 'Aligned' : 'Review required') : 'Awaiting initial architecture';
  $('architectureSummary').textContent = state.architecture.summary || 'No architecture generated yet.';
  $('overviewMessage').textContent = awaiting ? 'The Goal is saved. Architecture generation needs to complete before normal project updates begin.' : pending.length ? 'One architecture decision needs your review.' : 'The current architecture has no pending approval boundary.';

  document.querySelectorAll('.nav-item').forEach((n) => n.classList.toggle('active', n.dataset.view === activeView));
  document.querySelectorAll('.view').forEach((v) => v.classList.remove('active'));
  $(`view-${activeView}`).classList.add('active');

  renderTasks();
  renderProposals();
  renderGraph();
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
  document.querySelectorAll('#taskList [data-task-select]').forEach((row) => row.addEventListener('click', (event) => {
    if (event.target.closest('button')) return;
    state.selectedTaskId = row.dataset.taskSelect;
    renderTasks();
    updateInstructionContext();
  }));
}

function taskRow(t, selectable = false) {
  const selected = selectable && state.selectedTaskId === t.id;
  const updating = state.taskUpdating.has(t.id);
  const action = t.status === 'TODO'
    ? `<button data-task-action="start" data-task-id="${escapeHtml(t.id)}" ${updating ? 'disabled' : ''}>${updating ? 'Starting…' : 'Start task'}</button>`
    : t.status === 'IN_PROGRESS'
      ? `<button data-task-action="done" data-task-id="${escapeHtml(t.id)}" ${updating ? 'disabled' : ''}>${updating ? 'Saving…' : 'Mark done'}</button>`
      : '';
  const selector = selectable ? ` data-task-select="${escapeHtml(t.id)}"` : '';
  return `<div class="task-row${selected ? ' context-selected' : ''}"${selector}><i class="status-dot ${statusClass(t.status)}"></i><div><strong>${escapeHtml(t.title)}</strong><p>${escapeHtml(t.description || `${t.owner} · ${t.source}`)}</p>${action}</div><span class="status-pill ${t.status}">${t.status.replace('_', ' ')}</span></div>`;
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
    ? `<div class="attention-card"><strong>${escapeHtml(pending[0].reason)}</strong><p>${escapeHtml(pending[0].observed_change)}</p><button class="btn soft" data-go="attention">Review architecture change</button></div>`
    : '<p>No pending architecture decision. The agent can maintain task/status state without asking you to approve normal aligned updates.</p>';
  $('proposalList').innerHTML = state.proposals.length
    ? state.proposals.map((p) => `<article class="proposal-card${state.selectedProposalId === p.id ? ' context-selected' : ''}" data-proposal-card="${escapeHtml(p.id)}"><div class="proposal-head"><div><small>${p.status}</small><h3>${escapeHtml(p.reason)}</h3></div><span class="status-pill ${p.status}">${p.status}</span></div><p>${escapeHtml(p.observed_change)}</p><div class="meta"><div><small>EVIDENCE</small><p>${p.evidence.map(escapeHtml).join('<br>')}</p></div><div><small>IMPACT</small><p>${escapeHtml(p.impact)}</p></div></div>${p.status === 'PENDING' ? `<div class="actions"><button class="btn secondary" data-proposal="reject" data-id="${escapeHtml(p.id)}">Keep current</button><button class="btn primary" data-proposal="accept" data-id="${escapeHtml(p.id)}">Accept proposed change</button></div>` : ''}</article>`).join('')
    : '<article class="panel"><h3>No architecture review needed</h3><p class="muted">Normal aligned project updates stay ambient and do not interrupt the human.</p></article>';
  wireGoButtons();
  document.querySelectorAll('[data-proposal]').forEach((btn) => btn.addEventListener('click', () => decideProposal(btn.dataset.id, btn.dataset.proposal)));
  document.querySelectorAll('[data-proposal-card]').forEach((card) => card.addEventListener('click', (event) => {
    if (event.target.closest('button')) return;
    state.selectedProposalId = card.dataset.proposalCard;
    renderProposals();
    updateInstructionContext();
  }));
}

async function decideProposal(id, decision) {
  try {
    setWorking(true);
    await api(`/projects/${state.projectId}/architecture/proposals/${id}/${decision}`, {method: 'POST'});
    toast(decision === 'accept' ? 'Architecture change accepted.' : 'Proposal rejected; current architecture preserved.');
    await refresh();
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
  if (health.key === 'blocked') return {fill:'#fff7f7', stroke:'#ef4444', accent:'#dc2626', tag:'#fee2e2'};
  if (health.key === 'review') return {fill:'#fffaf0', stroke:'#f59e0b', accent:'#b45309', tag:'#fef3c7'};
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
    state.selectedNode = null;
    state.drillNodeId = parentArchitectureNodeId(parent.id);
    renderGraph();
  });
  canvas.querySelectorAll('[data-detail-node]').forEach((el) => el.addEventListener('click', () => {
    const node = findArchitectureNode(el.dataset.detailNode);
    if (!node) return;
    state.selectedNode = node.id;
    if ((node.children || []).length) state.drillNodeId = node.id;
    renderGraph();
  }));
  renderSelectedNode();
  renderLists();
  updateInstructionContext();
}

function renderGraph() {
  const a = state.architecture;
  const canvas = $('graphCanvas');
  if (!a.components.length) {
    canvas.innerHTML = '<div style="height:430px;display:grid;place-items:center;text-align:center;padding:30px"><div><strong>No architecture yet</strong><p class="muted">Architecture v1 has not completed.</p></div></div>';
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
    if (/agent|model|\bai\b|adk|orchestrat/i.test(text)) return {fill:'#f8f5ff', stroke:'#a78bfa', accent:'#7c3aed', tag:'#ede9fe'};
    if (/data|database|storage|state|sql|firestore/i.test(text)) return {fill:'#f2fcf7', stroke:'#86d9ab', accent:'#16835b', tag:'#dcfce7'};
    if (/front|web|ui|client/i.test(text)) return {fill:'#f5f9ff', stroke:'#9bc5ff', accent:'#2563eb', tag:'#dbeafe'};
    if (/cloud|infra|deploy|service/i.test(text)) return {fill:'#fffaf0', stroke:'#efc565', accent:'#a16207', tag:'#fef3c7'};
    return {fill:'#ffffff', stroke:'#cbd5e1', accent:'#64748b', tag:'#f1f5f9'};
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
    return `<g><text x="${x + nodeW / 2}" y="38" text-anchor="middle" font-size="9.5" font-weight="850" letter-spacing="1.1" fill="#7b8797">${escapeHtml(layerTitle(layer, index))}</text><line x1="${x}" y1="51" x2="${x + nodeW}" y2="51" stroke="#e5eaf1" stroke-width="1"/></g>`;
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
    const nameText = nameLines.map((line, i) => `<text x="${p.x + 18}" y="${p.y + 49 + i * 17}" font-size="13.5" font-weight="800" fill="#172033">${escapeHtml(line)}</text>`).join('');
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
      <rect x="${p.x}" y="${p.y}" width="${nodeW}" height="${nodeH}" rx="16" fill="${colors.fill}" stroke="${selected ? colors.accent : colors.stroke}" stroke-width="${selected ? 2.6 : health.needsAttention ? 2.2 : 1.5}"/>
      <rect x="${p.x + 14}" y="${p.y + 13}" width="${Math.min(112, type.length * 6 + 18)}" height="21" rx="10.5" fill="${baseColors.tag}"/>
      <text x="${p.x + 24}" y="${p.y + 27}" font-size="9.2" font-weight="800" fill="${baseColors.accent}">${type}</text>
      <circle cx="${p.x + nodeW - 70}" cy="${p.y + 23.5}" r="4" fill="${health.key === 'healthy' ? '#22c55e' : health.key === 'active' ? '#7c3aed' : colors.accent}"/>
      <text x="${p.x + nodeW - 60}" y="${p.y + 27}" font-size="8.8" font-weight="800" fill="${health.needsAttention ? colors.accent : '#667588'}">${status}</text>
      ${nameText}${responsibilityText}
      <text x="${p.x + 18}" y="${p.y + 124}" font-size="8.8" font-weight="750" fill="#738095">${escapeHtml(taskText)}</text>
      <rect x="${p.x + 18}" y="${p.y + 133}" width="${nodeW - 36}" height="5" rx="2.5" fill="#e8edf3"/>
      <rect x="${p.x + 18}" y="${p.y + 133}" width="${Math.max(0, (nodeW - 36) * visibleProgress / 100)}" height="5" rx="2.5" fill="${colors.accent}"/>
    </g>`;
  }).join('');
  const activeTaskCount = state.tasks.filter((t) => t.status === 'IN_PROGRESS').length;
  const attentionRoots = a.components.filter((component) => architectureHealth(component).needsAttention);
  $('graphReviewState').textContent = attentionRoots.length ? `${attentionRoots.length} area${attentionRoots.length === 1 ? '' : 's'} need attention` : 'All top-level areas aligned';
  canvas.innerHTML = `<div class="graph-meta"><span>${a.components.length} top-level areas</span><span>${a.relationships.length} relationships</span><span>${activeTaskCount} task${activeTaskCount === 1 ? '' : 's'} active</span><span>Accepted v${a.version}</span>${attentionRoots.length ? `<span class="graph-meta-attention">${attentionRoots.length} need attention</span>` : '<span class="graph-meta-ok">No action needed</span>'}</div><svg viewBox="0 0 ${W} ${H}" style="min-width:${Math.min(W, 860)}px;height:${H}px" role="img" aria-label="Accepted project architecture health map"><defs><marker id="arrow" markerWidth="9" markerHeight="9" refX="8" refY="4.5" orient="auto"><path d="M1 1 L8 4.5 L1 8 Z" fill="#8b98a9"/></marker></defs>${layerHeaders}${edges}${nodes}</svg>`;
  canvas.querySelectorAll('[data-node][data-clickable="true"]').forEach((el) => el.addEventListener('click', () => {
    const node = findArchitectureNode(el.dataset.node);
    if (!node) return;
    state.selectedNode = node.id;
    if ((node.children || []).length) state.drillNodeId = node.id;
    renderGraph();
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

  if (state.currentView === 'attention') {
    const pending = state.proposals.find((item) => item.status === 'PENDING');
    const proposal = state.proposals.find((item) => item.id === state.selectedProposalId) || pending || null;
    return {
      label: proposal ? `Proposal · ${proposal.status}` : 'Needs You · architecture review',
      instruction: proposal ? 'Ask about this proposal before deciding' : 'Ask about architecture decisions that need human review',
      placeholder: proposal ? 'Example: What existing tasks and components would this change affect?' : 'Ask why a proposal is needed or what evidence supports it.',
      payload: {...base, ...(proposal ? {proposal_id: proposal.id, proposal_status: proposal.status, proposal_reason: proposal.reason, affected_components: proposal.affected_components || []} : {})},
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
  state.currentView = name;
  document.querySelectorAll('.view').forEach((v) => v.classList.remove('active'));
  $(`view-${name}`).classList.add('active');
  document.querySelectorAll('.nav-item').forEach((n) => n.classList.toggle('active', n.dataset.view === name));
  $('pageTitle').textContent = views[name].title;
  $('pageSubtitle').textContent = views[name].subtitle;
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

$('nav').addEventListener('click', (e) => {
  const btn = e.target.closest('[data-view]');
  if (btn) switchView(btn.dataset.view);
});

$('instructionForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  if (state.architecture?.version === 0) {
    toast('Architecture v1 must finish before normal project updates.', true);
    return;
  }
  const input = $('instruction');
  const message = input.value.trim();
  if (!message) return;
  input.value = '';
  const context = currentInstructionContext();
  await sendEvent('USER_MESSAGE', {message, ui_context: context.payload});
});

$('onboardingForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  await submitOnboardingAsk();
});

$('goalDraftText').addEventListener('input', () => { updateGoalConfirmState(); if (!state.onboarding.draft) renderGoalDraft(); });
$('goalProjectName').addEventListener('input', updateGoalConfirmState);
$('useGoalBtn').addEventListener('click', confirmGoalAndGenerate);
$('onboardingBackBtn').addEventListener('click', backToCurrentProject);
$('generateArchitectureBtn').addEventListener('click', generateInitialArchitecture);
$('newProjectBtn').addEventListener('click', startOnboarding);
$('projectSelect').addEventListener('change', async (e) => selectProject(e.target.value));
$('editProjectBtn').addEventListener('click', openEditProject);
$('deleteProjectBtn').addEventListener('click', openDeleteProject);
$('editProjectForm').addEventListener('submit', async (e) => { e.preventDefault(); await saveProjectEdits(); });
$('deleteProjectForm').addEventListener('submit', async (e) => { e.preventDefault(); await deleteCurrentProject(); });
document.querySelectorAll('[data-close-dialog]').forEach((button) => button.addEventListener('click', () => $(button.dataset.closeDialog).close()));
$('editProjectDialog').addEventListener('click', closeDialogOnBackdrop);
$('deleteProjectDialog').addEventListener('click', closeDialogOnBackdrop);

wireGoButtons();
async function initialize() {
  try {
    await loadProjects();
    if (state.projectId && !state.projects.some((project) => project.id === state.projectId)) {
      state.projectId = null;
      localStorage.removeItem('archbro-project-id');
    }
    if (!state.projectId && state.projects.length) {
      state.projectId = state.projects[0].id;
      localStorage.setItem('archbro-project-id', state.projectId);
      state.onboarding.active = false;
    } else if (!state.projectId) {
      state.onboarding.active = true;
    } else {
      state.onboarding.active = false;
    }
    renderProjectControls();
    await refresh();
  } catch (err) {
    toast(err.message, true);
  }
}

initialize();
