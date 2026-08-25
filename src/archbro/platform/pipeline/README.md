# Event pipeline

Owner: Max.

This boundary receives normalized external events from `integrations/events` and delivers them to the backend Agent/event API with durability, buffering, retry, or transport concerns as needed. It must stay provider-agnostic: GitHub-specific parsing belongs to Ayushi's integration layer.
