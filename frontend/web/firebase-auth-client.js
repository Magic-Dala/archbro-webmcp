(function exposeFirebaseAuthClient(global) {
  function normalizeText(value = '') {
    return typeof value === 'string' ? value.trim() : '';
  }

  function normalizeEmail(value = '') {
    return normalizeText(value).toLowerCase();
  }

  function identityFromUser(user, {name = ''} = {}) {
    if (user?.isAnonymous === true) {
      const error = new Error('Anonymous Firebase identities are not accepted.');
      error.code = 'auth/anonymous-user-not-allowed';
      throw error;
    }
    const uid = normalizeText(user?.uid);
    if (!uid) throw new Error('Firebase returned a user without a trusted UID.');
    return {
      id: uid,
      provider: 'password',
      email: normalizeEmail(user?.email),
      name: normalizeText(user?.displayName) || normalizeText(name),
    };
  }

  function authenticationErrorMessage(error) {
    const code = normalizeText(error?.code);
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
    if (code === 'auth/operation-not-allowed') {
      return 'Email/password authentication is not enabled for this Firebase project.';
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
      return {...identityFromUser(user, {name: displayName}), profileSynced};
    }

    async function signIn({email, password}) {
      const normalizedEmail = normalizeEmail(email);
      const credential = await signInWithEmailAndPassword(auth, normalizedEmail, password);
      return identityFromUser(credential?.user);
    }

    async function getIdToken() {
      await waitUntilReady();
      if (!auth.currentUser) throw new Error('Sign in to continue.');
      return auth.currentUser.getIdToken();
    }

    async function endSession() {
      await signOut(auth);
    }

    return Object.freeze({restoreIdentity, signUp, signIn, getIdToken, endSession});
  }

  global.ArchbroFirebaseAuthClient = Object.freeze({
    authenticationErrorMessage,
    createFirebaseAuthClient,
    identityFromUser,
  });
})(globalThis);
