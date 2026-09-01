(function exposeArchbroPrototype(global) {
  const KEYS = Object.freeze({
    session: 'archbro-demo-session',
    profiles: 'archbro-demo-profiles',
  });
  const VALID_LENSES = new Set(['software', 'design', 'engineering']);

  function isPlainObject(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
    const prototype = Object.getPrototypeOf(value);
    return prototype === Object.prototype || prototype === null;
  }

  function readJson(storage, key, fallback) {
    try {
      const raw = storage.getItem(key);
      return raw ? JSON.parse(raw) : fallback;
    } catch {
      return fallback;
    }
  }

  const normalizeEmail = (value = '') => String(value).trim().toLowerCase();
  const validEmail = (value) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(normalizeEmail(value));

  function profileMap(storage) {
    const value = readJson(storage, KEYS.profiles, {});
    return isPlainObject(value) ? value : {};
  }

  function normalizeSession(value) {
    if (!isPlainObject(value) || typeof value.id !== 'string' || !value.id.trim()) return null;
    return {
      id: value.id.trim(),
      provider: typeof value.provider === 'string' && value.provider.trim() ? value.provider.trim() : 'password',
      email: typeof value.email === 'string' ? normalizeEmail(value.email) : '',
      name: typeof value.name === 'string' ? value.name.trim() : '',
    };
  }

  function normalizeProfile(session, value) {
    if (!isPlainObject(value)) return null;
    const defaultLens = VALID_LENSES.has(value.defaultLens) ? value.defaultLens : null;
    const notifications = isPlainObject(value.notifications) ? value.notifications : {};
    const fallbackName = session.name || session.email.split('@')[0] || `${session.provider[0].toUpperCase()}${session.provider.slice(1)} user`;
    return {
      id: session.id,
      email: typeof value.email === 'string' ? normalizeEmail(value.email) : session.email,
      name: typeof value.name === 'string' && value.name.trim() ? value.name.trim() : fallbackName,
      onboardingComplete: value.onboardingComplete === true && Boolean(defaultLens),
      defaultLens,
      notifications: {
        architectureApprovals: typeof notifications.architectureApprovals === 'boolean' ? notifications.architectureApprovals : true,
        blockedTasks: typeof notifications.blockedTasks === 'boolean' ? notifications.blockedTasks : true,
      },
    };
  }

  function validateSignIn({email = '', password = ''}) {
    const errors = {};
    if (!validEmail(email)) errors.email = 'Enter a valid email address.';
    if (!password) errors.password = 'Enter your password.';
    return errors;
  }

  function validateSignUp({name = '', email = '', password = '', confirmPassword = ''}) {
    const errors = {};
    if (!String(name).trim()) errors.name = 'Enter your name.';
    if (!validEmail(email)) errors.email = 'Enter a valid email address.';
    if (password.length < 8) errors.password = 'Use at least 8 characters.';
    if (confirmPassword !== password) errors.confirmPassword = 'Passwords do not match.';
    return errors;
  }

  function identityFor({provider = 'password', email = ''}) {
    return provider === 'password' ? `email:${normalizeEmail(email)}` : `${provider}:demo`;
  }

  function loadSession(storage) {
    const value = normalizeSession(readJson(storage, KEYS.session, null));
    if (!value) storage.removeItem(KEYS.session);
    return value;
  }

  function startSession(storage, {id: trustedId = '', provider = 'password', email = '', name = ''}) {
    const normalizedProvider = typeof provider === 'string' && provider.trim() ? provider.trim() : 'password';
    const id = typeof trustedId === 'string' && trustedId.trim()
      ? trustedId.trim()
      : identityFor({provider: normalizedProvider, email});
    const session = {
      id,
      provider: normalizedProvider,
      email: normalizeEmail(email),
      name: typeof name === 'string' ? name.trim() : '',
    };
    const profiles = profileMap(storage);
    if (!isPlainObject(profiles[id])) {
      profiles[id] = {
        id,
        email: session.email,
        name: session.name || `${normalizedProvider[0].toUpperCase()}${normalizedProvider.slice(1)} user`,
        onboardingComplete: false,
        defaultLens: null,
        notifications: {architectureApprovals: true, blockedTasks: true},
      };
    } else profiles[id] = normalizeProfile(session, profiles[id]);
    storage.setItem(KEYS.profiles, JSON.stringify(profiles));
    storage.setItem(KEYS.session, JSON.stringify(session));
    return session;
  }

  function endSession(storage) {
    storage.removeItem(KEYS.session);
  }

  function currentProfile(storage) {
    const session = loadSession(storage);
    if (!session) return null;
    const profiles = profileMap(storage);
    const profile = normalizeProfile(session, profiles[session.id]);
    if (!profile) {
      endSession(storage);
      return null;
    }
    profiles[session.id] = profile;
    storage.setItem(KEYS.profiles, JSON.stringify(profiles));
    return profile;
  }

  function updateCurrentProfile(storage, changes) {
    const session = loadSession(storage);
    if (!session) return null;
    const current = currentProfile(storage);
    if (!current) return null;
    const profiles = profileMap(storage);
    profiles[session.id] = normalizeProfile(session, {...current, ...(isPlainObject(changes) ? changes : {})});
    storage.setItem(KEYS.profiles, JSON.stringify(profiles));
    return profiles[session.id];
  }

  function deriveNeedsYou(proposals = [], tasks = [], notifications = {architectureApprovals: true, blockedTasks: true}) {
    const preferences = isPlainObject(notifications) ? notifications : {};
    const approvals = preferences.architectureApprovals !== false ? proposals.filter((proposal) => proposal.status === 'PENDING').map((proposal) => ({
      kind: 'proposal', id: proposal.id, title: 'Architecture approval', description: proposal.reason,
    })) : [];
    const blockers = preferences.blockedTasks !== false ? tasks.filter((task) => task.status === 'BLOCKED').map((task) => ({
      kind: 'task', id: task.id, title: 'Blocked task', description: task.title,
    })) : [];
    return [...approvals, ...blockers];
  }

  function installProjectCardLivingGraphNavigation(documentRoot, schedule = (callback, delay) => global.setTimeout(callback, delay)) {
    if (!documentRoot?.addEventListener || !documentRoot?.querySelectorAll) return false;
    documentRoot.addEventListener('click', (event) => {
      const trigger = event.target?.closest?.('[data-project-card-open]');
      const projectId = trigger?.dataset?.projectCardOpen;
      if (!projectId) return;

      let attempts = 0;
      const openLivingGraph = () => {
        attempts += 1;
        const currentProject = [...documentRoot.querySelectorAll('[data-project-id]')]
          .find((node) => node.dataset?.projectId === projectId && node.classList?.contains?.('current'));
        const livingGraph = currentProject?.querySelector?.('[data-project-view="architecture"]');
        if (livingGraph) {
          livingGraph.click();
          return;
        }
        if (attempts < 50) schedule(openLivingGraph, 50);
      };
      schedule(openLivingGraph, 0);
    }, true);
    return true;
  }

  global.ArchbroPrototype = Object.freeze({
    KEYS, normalizeEmail, validateSignIn, validateSignUp, loadSession, startSession,
    endSession, currentProfile, updateCurrentProfile, deriveNeedsYou,
    installProjectCardLivingGraphNavigation,
  });

  if (typeof document !== 'undefined') installProjectCardLivingGraphNavigation(document);
})(globalThis);
