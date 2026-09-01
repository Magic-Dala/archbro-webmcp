import assert from 'node:assert/strict';
import test from 'node:test';

await import('../frontend/web/firebase-auth-client.js');

const firebase = globalThis.ArchbroFirebaseAuthClient;

function fakeGitHubClient() {
  const calls = [];
  const githubProvider = {providerId: 'github.com'};
  const user = {
    uid: 'firebase-github-uid-789',
    email: null,
    displayName: 'Octocat Builder',
    isAnonymous: false,
    providerData: [{providerId: 'github.com'}],
    async getIdToken() {
      return 'firebase-id-token';
    },
  };
  const auth = {
    currentUser: null,
    async authStateReady() {},
  };
  const client = firebase.createFirebaseAuthClient({
    auth,
    githubProvider,
    async signInWithPopup(receivedAuth, receivedProvider) {
      calls.push(['signInWithPopup', receivedAuth, receivedProvider]);
      const popupUser = {
        ...user,
        providerData: [{providerId: 'google.com'}, {providerId: 'github.com'}],
      };
      auth.currentUser = popupUser;
      return {
        user: popupUser,
        _tokenResponse: {oauthAccessToken: 'github-access-token-must-not-escape'},
      };
    },
    async signOut() {
      auth.currentUser = null;
    },
  });
  return {auth, calls, client, githubProvider, user};
}

test('GitHub sign-in returns only provider-neutral Firebase identity information', async () => {
  const {auth, calls, client, githubProvider} = fakeGitHubClient();

  const identity = await client.signInWithGitHub();

  assert.deepEqual(identity, {
    id: 'firebase-github-uid-789',
    provider: 'github.com',
    email: '',
    name: 'Octocat Builder',
  });
  assert.deepEqual(calls, [['signInWithPopup', auth, githubProvider]]);
  assert.equal(JSON.stringify(identity).includes('github-access-token-must-not-escape'), false);
});

test('restoring a GitHub Firebase session preserves its provider and UID', async () => {
  const {auth, client, user} = fakeGitHubClient();
  auth.currentUser = user;

  const identity = await client.restoreIdentity();

  assert.equal(identity.id, 'firebase-github-uid-789');
  assert.equal(identity.provider, 'github.com');
});

test('GitHub popup failures become safe, useful messages', () => {
  const cases = new Map([
    ['auth/popup-closed-by-user', 'GitHub sign-in was cancelled.'],
    ['auth/cancelled-popup-request', 'GitHub sign-in was cancelled.'],
    ['auth/popup-blocked', 'Your browser blocked the GitHub sign-in window. Allow pop-ups and try again.'],
    ['auth/unauthorized-domain', 'GitHub sign-in is not authorized for this website.'],
    ['auth/missing-auth-domain', 'GitHub sign-in is not configured for this environment.'],
    ['auth/account-exists-with-different-credential', 'This email is already registered with a different sign-in method. Use that method instead.'],
    ['auth/operation-not-allowed', 'GitHub authentication is not enabled for this Firebase project.'],
  ]);

  for (const [code, expected] of cases) {
    assert.equal(firebase.authenticationErrorMessage({code}, {provider: 'github'}), expected);
  }
});

test('GitHub sign-in fails closed when its Firebase SDK dependency is absent', async () => {
  const client = firebase.createFirebaseAuthClient({auth: {currentUser: null}});
  await assert.rejects(client.signInWithGitHub(), /GitHub sign-in is not available/);
});
