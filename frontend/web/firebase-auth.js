import {getApp, getApps, initializeApp} from 'https://www.gstatic.com/firebasejs/12.2.1/firebase-app.js';
import {getAuth, signInAnonymously} from 'https://www.gstatic.com/firebasejs/12.2.1/firebase-auth.js';

let authPromise = null;

function runtimeConfig() {
  return window.__ARCHBRO_RUNTIME_CONFIG__ || {auth_mode: 'local', firebase: null};
}

async function firebaseAuth() {
  const config = runtimeConfig();
  if (config.auth_mode !== 'firebase') return null;
  if (!config.firebase?.apiKey || !config.firebase?.projectId) {
    throw new Error('Firebase browser authentication is not configured.');
  }

  const app = getApps().length ? getApp() : initializeApp(config.firebase);
  const auth = getAuth(app);
  if (typeof auth.authStateReady === 'function') await auth.authStateReady();
  if (!auth.currentUser) await signInAnonymously(auth);
  return auth;
}

export async function getFirebaseIdToken() {
  const config = runtimeConfig();
  if (config.auth_mode !== 'firebase') return null;
  if (!authPromise) {
    authPromise = firebaseAuth().catch((error) => {
      authPromise = null;
      throw error;
    });
  }
  const auth = await authPromise;
  if (!auth?.currentUser) throw new Error('Firebase did not establish a signed-in user.');
  return auth.currentUser.getIdToken();
}
