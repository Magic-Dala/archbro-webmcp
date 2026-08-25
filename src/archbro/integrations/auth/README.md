# Identity / permission boundary — Ayushi

This is the provider-neutral ownership boundary for users, teams, and permissions.

Current provider direction: Firebase Authentication, implemented under `../firebase/` using the same server-side ID-token verification pattern already exercised in Keys by Friday.

Important: verified identity and authorization are separate. Archbro should not enforce per-project access until the Project/User/Team ownership contract is explicit; do not treat token verification alone as authorization.
