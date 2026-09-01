import {getApp, getApps, initializeApp} from 'https://www.gstatic.com/firebasejs/12.2.1/firebase-app.js';
import {
  createUserWithEmailAndPassword,
  getAuth,
  GithubAuthProvider,
  GoogleAuthProvider,
  signInWithEmailAndPassword,
  signInWithPopup,
  signOut,
  updateProfile,
} from 'https://www.gstatic.com/firebasejs/12.2.1/firebase-auth.js';

let clientPromise = null;

function runtimeConfig() {
  return window.__ARCHBRO_RUNTIME_CONFIG__ || {auth_mode: 'local', firebase: null};
}

export function usesFirebaseAuthentication() {
  return runtimeConfig().auth_mode === 'firebase';
}

async function firebaseClient() {
  const config = runtimeConfig();
  if (config.auth_mode !== 'firebase') return null;
  if (!config.firebase?.apiKey || !config.firebase?.projectId) {
    throw new Error('Firebase browser authentication is not configured.');
  }

  const app = getApps().length ? getApp() : initializeApp(config.firebase);
  const auth = getAuth(app);
  const factory = globalThis.ArchbroFirebaseAuthClient?.createFirebaseAuthClient;
  if (typeof factory !== 'function') throw new Error('Firebase authentication support did not load.');
  // A popup rather than a redirect: the redirect flow leaves and re-enters the
  // page, so it has to rebuild whatever was in flight on the way back. The
  // popup is opened by the person's own click, so browsers allow it.
  const googleProvider = new GoogleAuthProvider();
  googleProvider.setCustomParameters({prompt: 'select_account'});
  const githubProvider = new GithubAuthProvider();

  return factory({
    auth,
    createUserWithEmailAndPassword,
    signInWithEmailAndPassword,
    signOut,
    updateProfile,
    signInWithPopup,
    googleProvider,
    githubProvider,
  });
}

async function configuredClient() {
  if (!usesFirebaseAuthentication()) return null;
  if (!clientPromise) {
    clientPromise = firebaseClient().catch((error) => {
      clientPromise = null;
      throw error;
    });
  }
  return clientPromise;
}

export function authenticationErrorMessage(error, options = {}) {
  const messageFor = globalThis.ArchbroFirebaseAuthClient?.authenticationErrorMessage;
  return typeof messageFor === 'function'
    ? messageFor(error, options)
    : 'Authentication could not be completed. Please try again.';
}

function requireAuthDomain(providerName) {
  if (runtimeConfig().firebase?.authDomain) return;
  const error = new Error(`Firebase authDomain is required for ${providerName} sign-in.`);
  error.code = 'auth/missing-auth-domain';
  throw error;
}

export async function restoreFirebaseIdentity() {
  const client = await configuredClient();
  return client ? client.restoreIdentity() : null;
}

export async function createFirebaseEmailAccount({email, password, name}) {
  const client = await configuredClient();
  if (!client) throw new Error('Firebase authentication is not enabled.');
  return client.signUp({email, password, name});
}

export async function signInWithFirebaseEmail({email, password}) {
  const client = await configuredClient();
  if (!client) throw new Error('Firebase authentication is not enabled.');
  return client.signIn({email, password});
}

export async function signInWithGoogleAccount() {
  requireAuthDomain('Google');
  const client = await configuredClient();
  if (!client) throw new Error('Firebase authentication is not enabled.');
  return client.signInWithGoogle();
}

export async function signInWithGitHubAccount() {
  requireAuthDomain('GitHub');
  const client = await configuredClient();
  if (!client) throw new Error('Firebase authentication is not enabled.');
  return client.signInWithGitHub();
}

export async function signOutFromFirebase() {
  const client = await configuredClient();
  if (client) await client.endSession();
}

export async function getFirebaseIdToken() {
  const client = await configuredClient();
  return client ? client.getIdToken() : null;
}
