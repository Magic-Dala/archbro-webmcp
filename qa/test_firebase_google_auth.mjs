import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import test from 'node:test';

await import('../frontend/web/firebase-auth-client.js');

const firebase = globalThis.ArchbroFirebaseAuthClient;
const webRoot = new URL('../frontend/web/', import.meta.url);

function fakeClient({popupResult, popupError} = {}) {
  const calls = [];
  const auth = {
    currentUser: null,
    async authStateReady() { calls.push(['authStateReady']); },
  };
  const googleProvider = {providerId: 'google.com', scopes: []};
  const client = firebase.createFirebaseAuthClient({
    auth,
    async createUserWithEmailAndPassword() { throw new Error('not used here'); },
    async signInWithEmailAndPassword() { throw new Error('not used here'); },
    async signOut(receivedAuth) { calls.push(['signOut', receivedAuth]); auth.currentUser = null; },
    async updateProfile() {},
    googleProvider,
    async signInWithPopup(receivedAuth, provider) {
      calls.push(['signInWithPopup', receivedAuth, provider]);
      if (popupError) throw popupError;
      auth.currentUser = popupResult;
      return {user: popupResult};
    },
  });
  return {auth, calls, client, googleProvider};
}

const googleUser = {
  uid: 'google-uid-789',
  email: 'Human@Gmail.com',
  displayName: 'Human Builder',
  isAnonymous: false,
  providerData: [{providerId: 'password'}, {providerId: 'google.com'}],
  async getIdToken() { return 'google-id-token'; },
};

test('Google sign-in returns the Firebase UID and records the provider as google.com', async () => {
  const {auth, calls, client, googleProvider} = fakeClient({popupResult: googleUser});

  const identity = await client.signInWithGoogle();

  assert.equal(identity.id, 'google-uid-789');
  assert.equal(identity.provider, 'google.com');
  assert.equal(identity.email, 'human@gmail.com');
  assert.equal(identity.name, 'Human Builder');
  assert.deepEqual(calls[0], ['signInWithPopup', auth, googleProvider]);
});

test('Google sign-in refuses an anonymous session the same way email sign-in does', async () => {
  const {client} = fakeClient({popupResult: {...googleUser, isAnonymous: true}});

  await assert.rejects(
    () => client.signInWithGoogle(),
    /Anonymous Firebase identities are not accepted/,
  );
});

test('a Google popup the person closed is not reported as a system failure', () => {
  for (const code of ['auth/popup-closed-by-user', 'auth/cancelled-popup-request']) {
    assert.equal(
      firebase.authenticationErrorMessage({code}),
      'Sign-in was cancelled before it finished.',
    );
  }
});

test('a blocked popup tells the person what to do rather than blaming the service', () => {
  assert.equal(
    firebase.authenticationErrorMessage({code: 'auth/popup-blocked'}),
    'Your browser blocked the sign-in window. Allow pop-ups for this site and try again.',
  );
});

test('an email already registered through another provider explains the conflict', () => {
  assert.equal(
    firebase.authenticationErrorMessage({code: 'auth/account-exists-with-different-credential'}),
    'This email is already registered with a different sign-in method. Use that method instead.',
  );
});

test('Google sign-in is wired into the browser, not stubbed out', async () => {
  const [app, firebaseModule] = await Promise.all([
    readFile(new URL('app.js', webRoot), 'utf8'),
    readFile(new URL('firebase-auth.js', webRoot), 'utf8'),
  ]);

  assert.match(firebaseModule, /GoogleAuthProvider/);
  assert.match(firebaseModule, /signInWithPopup/);
  assert.match(firebaseModule, /export async function signInWithGoogleAccount/);

  // The provider button must actually call it. The previous milestone answered
  // with a "not enabled yet" message, which is what this replaces.
  assert.match(app, /\['google', signInWithGoogleAccount\]/);
  assert.doesNotMatch(app, /Google login is not enabled yet/);
});
