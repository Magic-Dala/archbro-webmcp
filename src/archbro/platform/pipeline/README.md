# Event pipeline

This boundary receives normalized external events from `integrations/events` and delivers them to the backend event/evaluation API with durability, buffering, retry, and transport concerns as needed.

It stays provider-agnostic: provider-specific authentication and parsing belong to the integration layer, while product decisions and mutations belong to backend contracts.
