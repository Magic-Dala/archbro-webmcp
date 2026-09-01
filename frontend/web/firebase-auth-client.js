(function exposeFirebaseAuthClient(global) {
  function normalizeText(value = '') {
    return typeof value === 'string' ? value.trim() : '';
  }

  function normalizeEmail(value = '') {
    return normalizeText(value).toLowerCase();
  }

  function restoredProviderFromUser(user) {
    const providerIds = [...new Set(
      (Array.isArray(user?.providerData) ? user.providerData : [])
        .map((entry) => normalizeText(entry?.providerId))
        .filter(Boolean),
    )];
    return providerIds.length === 1 ? providerIds[0] : 'firebase';
  }

  function identityFromUser(user, {name = '', provider = ''} = {}) {
    if (user?.isAnonymous === true) {
      const error = new Error('Anonymous Firebase identities are not accepted.');
      error.code = 'auth/anonymous-user-not-allowed';
      throw error;
    }
    const uid = normalizeText(user?.uid);
    if (!uid) throw new Error('Firebase returned a user without a trusted UID.');
    // Sign-in operations pass their provider explicitly. During session restore
    // there is no new sign-in result, so a single linked provider can be shown;
    // accounts with zero or multiple providers use a neutral Firebase label.
    // Array order is not evidence of which provider performed the latest login.
    const providerId = normalizeText(provider) || restoredProviderFromUser(user);
    return {
      id: uid,
      provider: providerId,
      email: normalizeEmail(user?.email),
      name: normalizeText(user?.displayName) || normalizeText(name),
    };
  }

  function authenticationErrorMessage(error, {provider = ''} = {}) {
    const code = normalizeText(error?.code);
    const providerName = provider === 'github'
      ? 'GitHub'
      : (provider === 'google' ? 'Google' : '');
    if (['auth/invalid-credential', 'auth/user-not-found', 'auth/wrong-password'].includes(code)) {
      return 'The email or password is incorrect.';
    }
    if (code === 'auth/email-already-in-use') {
      return 'An account already exists for this email. Try logging in instead.';
    }
    if (code === 'auth/invalid-email') return 'Enter a valid email address.';
    if (code === 'auth/weak-password') return 'Choose a stronger password and try again.';
    if (code === 'auth/too-many-requests') {
      return 'Too many sign-in attempts. Wait a moment and try again.';
    }
    if (code === 'auth/network-request-failed') {
      return 'Archbro could not reach Firebase. Check your internet connection and try again.';
    }
    if (['auth/popup-closed-by-user', 'auth/cancelled-popup-request'].includes(code)) {
      return providerName
        ? `${providerName} sign-in was cancelled.`
        : 'Sign-in was cancelled before it finished.';
    }
    if (code === 'auth/popup-blocked') {
      return providerName
        ? `Your browser blocked the ${providerName} sign-in window. Allow pop-ups and try again.`
        : 'Your browser blocked the sign-in window. Allow pop-ups for this site and try again.';
    }
    if (code === 'auth/unauthorized-domain') {
      return providerName
        ? `${providerName} sign-in is not authorized for this website.`
        : 'This website is not authorized for provider sign-in.';
    }
    if (code === 'auth/missing-auth-domain') {
      return providerName
        ? `${providerName} sign-in is not configured for this environment.`
        : 'Provider sign-in is not configured for this environment.';
    }
    if (code === 'auth/account-exists-with-different-credential') {
      return 'This email is already registered with a different sign-in method. Use that method instead.';
    }
    if (code === 'auth/operation-not-allowed') {
      return providerName
        ? `${providerName} authentication is not enabled for this Firebase project.`
        : 'Email/password authentication is not enabled for this Firebase project.';
    }
    if (code === 'auth/anonymous-user-not-allowed') {
      return 'Sign in with email and password to continue.';
    }
    return 'Authentication could not be completed. Please try again.';
  }

  function createFirebaseAuthClient({
    auth,
    createUserWithEmailAndPassword,
    signInWithEmailAndPassword,
    signOut,
    updateProfile,
    signInWithPopup,
    googleProvider,
    githubProvider,
  }) {
    if (!auth) throw new TypeError('Firebase Auth is required.');

    async function waitUntilReady() {
      if (typeof auth.authStateReady === 'function') await auth.authStateReady();
    }

    async function restoreIdentity() {
      await waitUntilReady();
      if (!auth.currentUser) return null;
      if (auth.currentUser.isAnonymous === true) {
        await signOut(auth);
        return null;
      }
      return identityFromUser(auth.currentUser);
    }

    async function signUp({email, password, name = ''}) {
      const normalizedEmail = normalizeEmail(email);
      const credential = await createUserWithEmailAndPassword(auth, normalizedEmail, password);
      const user = credential?.user;
      let profileSynced = true;
      const displayName = normalizeText(name);
      if (displayName && typeof updateProfile === 'function') {
        try {
          await updateProfile(user, {displayName});
        } catch {
          // Authentication succeeded. The local Archbro profile still keeps the
          // requested name, so an optional Firebase profile update must not turn
          // a successfully created account into a misleading sign-up failure.
          profileSynced = false;
        }
      }
      return {
        ...identityFromUser(user, {name: displayName, provider: 'password'}),
        profileSynced,
      };
    }

    async function signIn({email, password}) {
      const normalizedEmail = normalizeEmail(email);
      const credential = await signInWithEmailAndPassword(auth, normalizedEmail, password);
      return identityFromUser(credential?.user, {provider: 'password'});
    }

    async function signInWithGoogle() {
      if (typeof signInWithPopup !== 'function' || !googleProvider) {
        throw new Error('Google sign-in is not available.');
      }
      const credential = await signInWithPopup(auth, googleProvider);
      return identityFromUser(credential?.user, {provider: 'google.com'});
    }

    async function signInWithGitHub() {
      if (typeof signInWithPopup !== 'function' || !githubProvider) {
        throw new Error('GitHub sign-in is not available.');
      }
      const credential = await signInWithPopup(auth, githubProvider);
      return identityFromUser(credential?.user, {provider: 'github.com'});
    }

    async function getIdToken() {
      await waitUntilReady();
      if (!auth.currentUser) throw new Error('Sign in to continue.');
      return auth.currentUser.getIdToken();
    }

    async function endSession() {
      await signOut(auth);
    }

    return Object.freeze({
      restoreIdentity,
      signUp,
      signIn,
      signInWithGoogle,
      signInWithGitHub,
      getIdToken,
      endSession,
    });
  }

  global.ArchbroFirebaseAuthClient = Object.freeze({
    authenticationErrorMessage,
    createFirebaseAuthClient,
    identityFromUser,
  });
})(globalThis);
