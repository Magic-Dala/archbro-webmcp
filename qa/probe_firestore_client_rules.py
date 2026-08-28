import json
import urllib.error
import urllib.request

cfg = json.load(open('.archbro-firebase-public.json', encoding='utf-8-sig'))
api_key = cfg['apiKey']
project = cfg['projectId']
referer = 'https://archbro-webmcp-23051378248.us-west1.run.app/'


def call(url, method='GET', body=None, token=None):
    data = None if body is None else json.dumps(body).encode('utf-8')
    headers = {'Content-Type': 'application/json', 'Referer': referer}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, response.read().decode('utf-8')
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode('utf-8')

status, body = call(
    f'https://identitytoolkit.googleapis.com/v1/accounts:signUp?key={api_key}',
    method='POST',
    body={'returnSecureToken': True},
)
assert status == 200, (status, body[:300])
id_token = json.loads(body)['idToken']
base = (
    f'https://firestore.googleapis.com/v1/projects/{project}/databases/'
    f'archbro-challenge/documents/archbro_security_probe/deny-test?key={api_key}'
)
read_status, read_body = call(base, token=id_token)
write_status, write_body = call(
    base,
    method='PATCH',
    body={'fields': {'probe': {'stringValue': 'deny'}}},
    token=id_token,
)
assert read_status == 403, (read_status, read_body[:300])
assert write_status == 403, (write_status, write_body[:300])
print(json.dumps({'anonymousAuth': True, 'directReadDenied': True, 'directWriteDenied': True}))
