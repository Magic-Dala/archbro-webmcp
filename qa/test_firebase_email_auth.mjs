import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import test from 'node:test';

await import('../frontend/web/firebase-auth-client.js');
await import('../frontend/web/prototype.js');

const firebase = globalThis.ArchbroFirebaseAuthClient;
const prototype = globalThis.ArchbroPrototype;
const webRoot = new URL('../frontend/web/', import.meta.url);

function memoryStorage() {
  const values = new Map();
  return {
    getItem(key) { return values.has(key) ? values.get(key) : null; },
    setItem(key, value) { values.set(key, String(value)); },
    removeItem(key) { values.delete(key); },
  };
}

function fakeClient({profileUpdateFails = false} = {}) {
  const calls = [];
  const user = {
    uid: 'firebase-uid-123',
    email: 'Human@Example.com',
    displayName: '',
    isAnonymous: false,
    async getIdToken() {
      calls.push(['getIdToken']);
      return 'verified-id-token';
    },
  };
  const auth = {
    currentUser: null,
    async authStateReady() { calls.push(['authStateReady']); },
  };
  const client = firebase.createFirebaseAuthClient({
    auth,
    async createUserWithEmailAndPassword(receivedAuth, email, password) {
      calls.push(['createUser', receivedAuth, email, password]);
      auth.currentUser = user;
      return {user};
    },
    async signInWithEmailAndPassword(receivedAuth, email, password) {
      calls.push(['signIn', receivedAuth, email, password]);
      auth.currentUser = user;
      return {user};
    },
    async updateProfile(receivedUser, changes) {
      calls.push(['updateProfile', receivedUser, changes]);
      if (profileUpdateFails) throw new Error('optional profile update failed');
      receivedUser.displayName = changes.displayName;
    },
    async signOut(receivedAuth) {
      calls.push(['signOut', receivedAuth]);
      auth.currentUser = null;
    },
  });
  return {auth, calls, client, user};
}

test('email/password sign-up returns the Firebase UID and never returns the password', async () => {
  const {auth, calls, client} = fakeClient();
  const identity = await client.signUp({
    email: 'Human@Example.com',
    password: 'correct-horse-battery-staple',
    name: 'Human Builder',
  });

  assert.deepEqual(identity, {
    id: 'firebase-uid-123',
    provider: 'password',
    email: 'human@example.com',
    name: 'Human Builder',
    profileSynced: true,
  });
  assert.equal('password' in identity, false);
  assert.deepEqual(calls[0], ['createUser', auth, 'human@example.com', 'correct-horse-battery-staple']);
  assert.deepEqual(calls[1].slice(0, 2), ['updateProfile', auth.currentUser]);
});

test('email/password sign-in restores the canonical Firebase UID', async () => {
  const {auth, calls, client} = fakeClient();
  const identity = await client.signIn({email: 'Human@Example.com', password: 'secret-value'});

  assert.equal(identity.id, 'firebase-uid-123');
  assert.equal(identity.email, 'human@example.com');
  assert.deepEqual(calls[0], ['signIn', auth, 'human@example.com', 'secret-value']);
});

test('email is trimmed and lowercased before both Firebase sign-up and sign-in', async () => {
  const signUpFixture = fakeClient();
  await signUpFixture.client.signUp({
    email: '  Human@Example.com  ', password: 'signup-password', name: 'Human Builder',
  });
  assert.deepEqual(
    signUpFixture.calls[0],
    ['createUser', signUpFixture.auth, 'human@example.com', 'signup-password'],
  );

  const signInFixture = fakeClient();
  await signInFixture.client.signIn({email: '\tHuman@Example.com\n', password: 'signin-password'});
  assert.deepEqual(
    signInFixture.calls[0],
    ['signIn', signInFixture.auth, 'human@example.com', 'signin-password'],
  );
});

test('Firebase state restoration, token retrieval, and sign-out use the same authenticated user', async () => {
  const {auth, calls, client, user} = fakeClient();
  assert.equal(await client.restoreIdentity(), null);

  auth.currentUser = user;
  assert.equal((await client.restoreIdentity()).id, 'firebase-uid-123');
  assert.equal(await client.getIdToken(), 'verified-id-token');
  await client.endSession();
  assert.equal(auth.currentUser, null);
  assert.ok(calls.some(([name]) => name === 'authStateReady'));
  assert.ok(calls.some(([name]) => name === 'signOut'));
});

test('a persisted anonymous Firebase session is signed out instead of restored', async () => {
  const {auth, calls, client, user} = fakeClient();
  auth.currentUser = {...user, isAnonymous: true};

  assert.equal(await client.restoreIdentity(), null);
  assert.equal(auth.currentUser, null);
  assert.ok(calls.some(([name]) => name === 'signOut'));
  assert.throws(
    () => firebase.identityFromUser({...user, isAnonymous: true}),
    /Anonymous Firebase identities are not accepted/,
  );
});

test('a display-name update failure does not turn a created account into a false sign-up failure', async () => {
  const {client} = fakeClient({profileUpdateFails: true});
  const identity = await client.signUp({
    email: 'human@example.com', password: 'long-enough-password', name: 'Local Name',
  });
  assert.equal(identity.id, 'firebase-uid-123');
  assert.equal(identity.name, 'Local Name');
  assert.equal(identity.profileSynced, false);
});

test('safe Firebase errors do not reveal provider details or distinguish unknown accounts', () => {
  for (const code of ['auth/invalid-credential', 'auth/user-not-found', 'auth/wrong-password']) {
    assert.equal(firebase.authenticationErrorMessage({code}), 'The email or password is incorrect.');
  }
  const secret = 'raw-token-that-must-not-appear';
  assert.equal(firebase.authenticationErrorMessage(new Error(secret)), 'Authentication could not be completed. Please try again.');
  assert.equal(firebase.authenticationErrorMessage(new Error(secret)).includes(secret), false);
});

test('the UI profile is keyed by the Firebase UID but remains non-authoritative browser state', () => {
  const storage = memoryStorage();
  prototype.startSession(storage, {
    id: 'firebase-uid-123', provider: 'password', email: 'human@example.com', name: 'Human Builder',
  });
  assert.equal(prototype.loadSession(storage).id, 'firebase-uid-123');
  assert.equal(prototype.currentProfile(storage).id, 'firebase-uid-123');
});

test('the browser wiring removes anonymous auth and loads the testable client before the app', async () => {
  const [html, app, firebaseModule] = await Promise.all([
    readFile(new URL('index.html', webRoot), 'utf8'),
    readFile(new URL('app.js', webRoot), 'utf8'),
    readFile(new URL('firebase-auth.js', webRoot), 'utf8'),
  ]);
  assert.doesNotMatch(firebaseModule, /signInAnonymously/);
  assert.match(firebaseModule, /createUserWithEmailAndPassword/);
  assert.match(firebaseModule, /signInWithEmailAndPassword/);
  assert.match(firebaseModule, /return client \? client\.getIdToken\(\) : null/);
  assert.match(app, /prototype\.startSession\(localStorage, identity\)/);
  assert.match(app, /await signOutFromFirebase\(\)/);
  assert.match(app, /GitHub login is not enabled yet/);
  assert.ok(html.indexOf('/static/firebase-auth-client.js') < html.indexOf('/static/app.js'));
  assert.match(html, /id="authFormMessage"[^>]+aria-live="polite"/);
});

test('Firebase WebMCP mode prompts for login and enters the workspace after authentication', async () => {
  const app = await readFile(new URL('app.js', webRoot), 'utf8');
  assert.match(app, /if \(WEBMCP_AGENT_MODE\) \{\s*await enterWorkspace\(\);\s*return true;/);
  assert.match(app, /if \(WEBMCP_AGENT_MODE\) openAuthentication\(\$\('landingLoginBtn'\)\);/);
});

test('authentication cannot be dismissed while a Firebase request is in flight', async () => {
  const app = await readFile(new URL('app.js', webRoot), 'utf8');
  assert.match(app, /if \(authenticationBusy\(\) && !force\) return false;/);
  assert.match(app, /\$\('authCloseBtn'\)\.disabled = busy;/);
  assert.match(app, /\$\('authBackBtn'\)\.disabled = busy;/);
  assert.match(app, /closeAuthentication\(\{returnFocus: false, force: true\}\);/);
});
