# External event normalization

Normalize provider-specific signals such as GitHub and future external systems into ArchBro's provider-neutral project-event vocabulary here. The normalized output is handed to the platform pipeline for durable delivery to the backend evaluation path.

Provider authentication and raw payload parsing belong at the integration edge; normalized events must not directly mutate Project, Living Architecture, or Task state.
