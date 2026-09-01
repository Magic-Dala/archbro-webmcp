import {getFirebaseIdToken} from './firebase-auth.js';

const TOOL_PREFIX = 'archbro_';
const WEBMCP_SURFACE_VERSION = 'archbro.semantic-webmcp.v4';
const WEBMCP_MANIFEST_URL = '/webmcp-manifest.json';
const WEBMCP_RUNTIME_CHECK_INTERVAL_MS = 10_000;
let staleReloadScheduled = false;

async function fetchWebMcpManifest({signal} = {}) {
  const response = await fetch(WEBMCP_MANIFEST_URL, {
    method: 'GET',
    signal,
    cache: 'no-store',
    headers: {'Accept': 'application/json'},
  });
  if (!response.ok) throw new Error(`${response.status}: ArchBro WebMCP manifest request failed`);
  return response.json();
}

async function verifyWebMcpRuntime({signal, autoReload = false} = {}) {
  const manifest = await fetchWebMcpManifest({signal});
  const loadedAssetSha256 = globalThis.window?.__ARCHBRO_RUNTIME_CONFIG__?.webmcp_asset_sha256 || null;
  const surfaceVersionMatch = manifest.surface_version === WEBMCP_SURFACE_VERSION;
  const assetMatch = !loadedAssetSha256 || manifest.asset_sha256 === loadedAssetSha256;
  const staleClient = !surfaceVersionMatch || !assetMatch;

  if (staleClient && autoReload && !staleReloadScheduled && globalThis.window?.location) {
    staleReloadScheduled = true;
    const nextUrl = new URL(globalThis.window.location.href);
    nextUrl.searchParams.set('_archbro_webmcp_build', String(manifest.asset_sha256 || Date.now()).slice(0, 16));
    globalThis.window.location.replace(nextUrl.toString());
  }

  return {
    manifest,
    stale_client: staleClient,
    reload_required: staleClient,
    client_surface_version: WEBMCP_SURFACE_VERSION,
    server_surface_version: manifest.surface_version,
    asset_match: assetMatch,
  };
}

function requireBridgeMethod(bridge, method) {
  if (!bridge || typeof bridge[method] !== 'function') {
    throw new Error(`ArchBroWebBridge.${method}() is required`);
  }
}

function hasBridgeMethod(bridge, method) {
  return Boolean(bridge && typeof bridge[method] === 'function');
}

function asToolResult(value) {
  if (typeof value === 'string') return value;
  return JSON.stringify(value ?? null);
}

function activeProjectId() {
  const projectId = globalThis.localStorage?.getItem('archbro-project-id')?.trim();
  if (!projectId) throw new Error('No active ArchBro project is selected.');
  return projectId;
}

async function agentSurfaceApi(path, {method = 'GET', body, signal} = {}) {
  const token = await getFirebaseIdToken();
  const response = await fetch(path, {
    method,
    signal,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? {Authorization: `Bearer ${token}`} : {}),
    },
    ...(body === undefined ? {} : {body: JSON.stringify(body)}),
  });
  if (!response.ok) {
    let detail = 'ArchBro agent surface request failed';
    try {
      const payload = await response.json();
      detail = payload.detail || JSON.stringify(payload);
    } catch {}
    throw new Error(`${response.status}: ${detail}`);
  }
  return response.status === 204 ? null : response.json();
}

async function getAgentContext({signal} = {}) {
  const projectId = activeProjectId();
  return agentSurfaceApi(`/projects/${encodeURIComponent(projectId)}/agent-context`, {signal});
}

function querySuffix(entries) {
  const query = new URLSearchParams();
  for (const [key, value] of entries) {
    if (value !== undefined && value !== null) query.set(key, String(value));
  }
  const encoded = query.toString();
  return encoded ? `?${encoded}` : '';
}

const ARCHITECTURE_KIND_VALUES = [
  'SYSTEM', 'UI', 'SERVICE', 'AGENT', 'TOOL', 'DATA_STORE', 'STATE',
  'EXTERNAL_SERVICE', 'INFRASTRUCTURE',
];

function architectureComponentInputSchema(depth = 1) {
  const properties = {
    id: {type: 'string', minLength: 1, description: 'Optional stable component id. Strongly recommended when relationships or tasks reference this component.'},
    name: {type: 'string', minLength: 1},
    type: {type: 'string', minLength: 1},
    responsibility: {type: 'string', minLength: 1},
    kind: {type: 'string', enum: ARCHITECTURE_KIND_VALUES},
    status: {type: 'string', minLength: 1},
  };
  if (depth < 3) {
    properties.children = {
      type: 'array',
      maxItems: depth === 1 ? 7 : 6,
      description: `Immediate canonical children at hierarchy level ${depth + 1}.`,
      items: architectureComponentInputSchema(depth + 1),
    };
  }
  return {
    type: 'object',
    properties,
    required: ['id', 'name', 'type', 'responsibility'],
    additionalProperties: false,
  };
}

function initialPlanningTraceInputSchema() {
  return {
    type: 'object',
    description: 'Auditable recursive outside-in planning trace. Plan SYSTEM_MAP roots, then evaluate every canonical component in preorder. A scope is EXPANDED only when it has real child architecture boundaries; otherwise it must be a JUSTIFIED_LEAF with a specific reason. Reconcile relationships/tasks only after every scope is evaluated.',
    properties: {
      system_map_root_ids: {
        type: 'array', minItems: 1, maxItems: 6,
        items: {type: 'string', minLength: 1},
        description: 'Stable root ids from the SYSTEM_MAP phase, in final root order. Every SYSTEM_MAP root is a broad architecture boundary and must be EXPANDED with at least one child; atomic services/components belong below a root.',
      },
      scope_evaluations: {
        type: 'array', minItems: 1, maxItems: 80,
        description: 'Exactly one evaluation for every submitted canonical component in preorder. Do not mark a multi-responsibility boundary as a leaf merely to avoid decomposition.',
        items: {
          type: 'object',
          properties: {
            scope_component_id: {type: 'string', minLength: 1},
            decomposition: {type: 'string', enum: ['EXPANDED', 'JUSTIFIED_LEAF']},
            child_ids: {type: 'array', maxItems: 12, items: {type: 'string', minLength: 1}, description: 'Immediate child ids in final order. Non-empty for EXPANDED; empty for JUSTIFIED_LEAF.'},
            leaf_reason: {type: 'string', minLength: 24, maxLength: 280, description: 'Required only for JUSTIFIED_LEAF. Explain why no independently addressable architecture boundary remains below this component.'},
          },
          required: ['scope_component_id', 'decomposition', 'child_ids'],
          additionalProperties: false,
        },
      },
      reconciled: {
        type: 'boolean',
        enum: [true],
        description: 'Must be true only after every scope is evaluated and final authored relationships/tasks are reconciled against the complete topology.',
      },
    },
    required: ['system_map_root_ids', 'scope_evaluations', 'reconciled'],
    additionalProperties: false,
  };
}

function codeArchitectureComponentInputSchema(depth = 1) {
  const properties = {
    id: {type: 'string', minLength: 1, description: 'Snapshot-local stable implementation component id.'},
    name: {type: 'string', minLength: 1},
    type: {type: 'string', minLength: 1},
    responsibility: {type: 'string', minLength: 1},
    kind: {type: 'string', enum: ARCHITECTURE_KIND_VALUES},
    source_evidence_ids: {
      type: 'array', minItems: 1, maxItems: 12,
      items: {type: 'string', minLength: 1},
      description: 'Evidence ids proving this implementation boundary at the pinned revision.',
    },
  };
  if (depth < 3) {
    properties.children = {
      type: 'array',
      maxItems: depth === 1 ? 7 : 6,
      items: codeArchitectureComponentInputSchema(depth + 1),
    };
  }
  return {
    type: 'object',
    properties,
    required: ['id', 'name', 'type', 'responsibility', 'source_evidence_ids'],
    additionalProperties: false,
  };
}

function codeArchitectureSnapshotInputSchema() {
  return {
    type: 'object',
    properties: {
      repository: {type: 'string', minLength: 3, description: 'GitHub owner/repo or https://github.com/owner/repo.'},
      revision: {type: 'string', pattern: '^[0-9a-fA-F]{40}$', description: 'Exact full Git commit SHA used for every cited source.'},
      summary: {type: 'string', minLength: 1, description: 'Evidence-grounded summary of the implementation architecture.'},
      components: {
        type: 'array', minItems: 1, maxItems: 8,
        description: 'Outside-in implementation hierarchy, up to three levels and 40 total nodes.',
        items: codeArchitectureComponentInputSchema(1),
      },
      relationships: {
        type: 'array', maxItems: 120,
        items: {
          type: 'object',
          properties: {
            source: {type: 'string', minLength: 1},
            target: {type: 'string', minLength: 1},
            relationship_type: {type: 'string', minLength: 1},
            description: {type: 'string'},
            source_evidence_ids: {type: 'array', minItems: 1, maxItems: 12, items: {type: 'string', minLength: 1}},
          },
          required: ['source', 'target', 'relationship_type', 'source_evidence_ids'],
          additionalProperties: false,
        },
      },
      source_evidence: {
        type: 'array', minItems: 1, maxItems: 160,
        description: 'Exact revision-pinned excerpts obtained from the connected GitHub MCP. Excerpt line count must match line_start..line_end.',
        items: {
          type: 'object',
          properties: {
            id: {type: 'string', minLength: 1},
            path: {type: 'string', minLength: 1, description: 'Safe repository-relative POSIX path.'},
            line_start: {type: 'integer', minimum: 1},
            line_end: {type: 'integer', minimum: 1},
            excerpt: {type: 'string', minLength: 1, maxLength: 4000},
            symbol: {type: 'string'},
            blob_sha: {type: 'string', pattern: '^[0-9a-fA-F]{40,64}$'},
          },
          required: ['id', 'path', 'line_start', 'line_end', 'excerpt'],
          additionalProperties: false,
        },
      },
    },
    required: ['repository', 'revision', 'summary', 'components', 'source_evidence'],
    additionalProperties: false,
  };
}

function expansionChildInputSchema() {
  return {
    type: 'object',
    properties: {
      id: {type: 'string', minLength: 1, description: 'New globally stable canonical component id.'},
      name: {type: 'string', minLength: 1},
      type: {type: 'string', minLength: 1},
      responsibility: {type: 'string', minLength: 1},
      kind: {type: 'string', enum: ARCHITECTURE_KIND_VALUES},
      status: {type: 'string', minLength: 1},
    },
    required: ['id', 'name', 'type', 'responsibility'],
    additionalProperties: false,
  };
}

async function getScopedDiagram({scopeComponentId, expectedArchitectureVersion, signal} = {}) {
  const projectId = activeProjectId();
  const suffix = querySuffix([
    ['scope', scopeComponentId],
    ['expected_architecture_version', expectedArchitectureVersion],
  ]);
  return agentSurfaceApi(`/projects/${encodeURIComponent(projectId)}/architecture/diagram${suffix}`, {signal});
}

async function publishCodeArchitectureSnapshot({repository, revision, summary, components, relationships, sourceEvidence, signal} = {}) {
  const projectId = activeProjectId();
  return agentSurfaceApi(`/projects/${encodeURIComponent(projectId)}/code-architecture/snapshots`, {
    method: 'POST',
    body: {
      repository,
      revision,
      summary,
      components,
      relationships: relationships || [],
      source_evidence: sourceEvidence,
    },
    signal,
  });
}

async function getLatestCodeArchitectureSnapshot({signal} = {}) {
  const projectId = activeProjectId();
  return agentSurfaceApi(`/projects/${encodeURIComponent(projectId)}/code-architecture/latest`, {signal});
}

async function getNodeContext({nodeId, direction, maxHops, maxResults, expectedArchitectureVersion, signal} = {}) {
  const projectId = activeProjectId();
  const suffix = querySuffix([
    ['direction', direction],
    ['max_hops', maxHops],
    ['max_results', maxResults],
    ['expected_architecture_version', expectedArchitectureVersion],
  ]);
  return agentSurfaceApi(
    `/projects/${encodeURIComponent(projectId)}/architecture/nodes/${encodeURIComponent(nodeId)}/context${suffix}`,
    {signal},
  );
}

async function findArchitecturePath({sourceId, targetId, maxHops, expectedArchitectureVersion, signal} = {}) {
  const projectId = activeProjectId();
  const suffix = querySuffix([
    ['source_id', sourceId],
    ['target_id', targetId],
    ['max_hops', maxHops],
    ['expected_architecture_version', expectedArchitectureVersion],
  ]);
  return agentSurfaceApi(`/projects/${encodeURIComponent(projectId)}/architecture/path${suffix}`, {signal});
}

async function listConnectedMcpServers(bridge, {signal} = {}) {
  if (hasBridgeMethod(bridge, 'listConnectedMcpServers')) {
    return bridge.listConnectedMcpServers({signal});
  }
  const projectId = activeProjectId();
  return agentSurfaceApi(`/projects/${encodeURIComponent(projectId)}/mcp/servers`, {signal});
}

async function listConnectedMcpTools(bridge, {serverId, signal} = {}) {
  if (hasBridgeMethod(bridge, 'listConnectedMcpTools')) {
    return bridge.listConnectedMcpTools({serverId, signal});
  }
  const projectId = activeProjectId();
  return agentSurfaceApi(
    `/projects/${encodeURIComponent(projectId)}/mcp/servers/${encodeURIComponent(serverId)}/tools`,
    {signal},
  );
}

async function callConnectedMcpTool(bridge, {serverId, toolName, arguments: args = {}, signal} = {}) {
  if (hasBridgeMethod(bridge, 'callConnectedMcpTool')) {
    return bridge.callConnectedMcpTool({serverId, toolName, arguments: args, signal});
  }
  const projectId = activeProjectId();
  return agentSurfaceApi(
    `/projects/${encodeURIComponent(projectId)}/mcp/servers/${encodeURIComponent(serverId)}/call`,
    {method: 'POST', body: {tool_name: toolName, arguments: args}, signal},
  );
}

function createCoreTools(bridge) {
  requireBridgeMethod(bridge, 'bootstrapProject');
  requireBridgeMethod(bridge, 'expandArchitectureScope');
  requireBridgeMethod(bridge, 'getDecisionContext');
  requireBridgeMethod(bridge, 'submitAgentRecommendation');
  requireBridgeMethod(bridge, 'createTask');
  requireBridgeMethod(bridge, 'updateTaskStatus');
  requireBridgeMethod(bridge, 'recordProjectObservation');

  const tools = [
    {
      name: `${TOOL_PREFIX}ping`,
      title: 'Ping ArchBro Site Tool',
      description: 'Read-only WebMCP capability and build-identity check. Verifies this loaded page is attached to the current server WebMCP manifest without project mutation or model invocation.',
      inputSchema: {type: 'object', properties: {}, additionalProperties: false},
      annotations: {readOnlyHint: true, untrustedContentHint: false},
      execute: async (_input, client = {}) => {
        const runtime = await verifyWebMcpRuntime({signal: client.signal});
        return asToolResult({
          ok: !runtime.stale_client,
          surface: 'archbro-webmcp',
          surface_version: runtime.server_surface_version,
          expected_tool_count: runtime.manifest.expected_tool_count,
          connected_mcp_gateway_configured: runtime.manifest.connected_mcp_gateway_configured,
          stale_client: runtime.stale_client,
          reload_required: runtime.reload_required,
          asset_match: runtime.asset_match,
          built_in_model_called: false,
        });
      },
    },
    {
      name: `${TOOL_PREFIX}get_agent_context`,
      title: 'Get compact ArchBro agent context',
      description: 'Bootstrap an agent with a compact Markdown map of the selected project, current execution focus, governance rules, and connected external MCP sources. Use this before broad project or source reads.',
      inputSchema: {type: 'object', properties: {}, additionalProperties: false},
      annotations: {readOnlyHint: true, untrustedContentHint: true},
      execute: async (_input, client = {}) => asToolResult(await getAgentContext({signal: client.signal})),
    },
    {
      name: `${TOOL_PREFIX}get_architecture_diagram`,
      title: 'Get Living Architecture diagram',
      description: 'Read the backend-authored root or one canonical subsystem projection, including SCOPE/PRIMARY nodes, aggregate-edge provenance, scope metadata, and deterministic positioned graph. Use this to drill architecture one level at a time instead of inferring hierarchy in the host.',
      inputSchema: {
        type: 'object',
        properties: {
          scope_component_id: {type: 'string', minLength: 1, description: 'Plain canonical component id. Omit for the root system map.'},
          expected_architecture_version: {type: 'integer', minimum: 0},
        },
        additionalProperties: false,
      },
      annotations: {readOnlyHint: true, untrustedContentHint: false},
      execute: async ({scope_component_id, expected_architecture_version}, client = {}) => asToolResult(
        await getScopedDiagram({scopeComponentId: scope_component_id, expectedArchitectureVersion: expected_architecture_version, signal: client.signal}),
      ),
    },
    {
      name: `${TOOL_PREFIX}publish_code_architecture`,
      title: 'Publish Code Architecture evidence for this project',
      description: 'Persist an exact-commit implementation architecture snapshot after inspecting the connected GitHub repository. This stores derived implementation evidence for the project UI and later agents; it does not mutate the accepted Living Architecture. Use a full 40-character Git commit SHA and evidence actually read through the connected GitHub MCP.',
      inputSchema: codeArchitectureSnapshotInputSchema(),
      annotations: {readOnlyHint: false, untrustedContentHint: true},
      execute: async ({repository, revision, summary, components, relationships = [], source_evidence}, client = {}) => asToolResult(
        await publishCodeArchitectureSnapshot({repository, revision, summary, components, relationships, sourceEvidence: source_evidence, signal: client.signal}),
      ),
    },
    {
      name: `${TOOL_PREFIX}get_code_architecture`,
      title: 'Get latest published Code Architecture evidence',
      description: 'Read the latest durable implementation architecture snapshot for this project, including exact GitHub revision, source provenance, deterministic layout, and evidence classification. This is derived implementation evidence, not the accepted Living Architecture.',
      inputSchema: {type: 'object', properties: {}, additionalProperties: false},
      annotations: {readOnlyHint: true, untrustedContentHint: true},
      execute: async (_input, client = {}) => asToolResult(await getLatestCodeArchitectureSnapshot({signal: client.signal})),
    },
    {
      name: `${TOOL_PREFIX}get_architecture_node_context`,
      title: 'Get Living Architecture dependency context',
      description: 'Read bounded upstream/downstream authored architecture reachability for one stable node. This is dependency context, not runtime impact or blast-radius analysis.',
      inputSchema: {
        type: 'object',
        properties: {
          node_id: {type: 'string', pattern: '^node:.+', description: 'Stable public ArchBro node ID.'},
          direction: {type: 'string', enum: ['upstream', 'downstream', 'both']},
          max_hops: {type: 'integer', minimum: 1, maximum: 8},
          max_results: {type: 'integer', minimum: 1, maximum: 40},
          expected_architecture_version: {type: 'integer', minimum: 0},
        },
        required: ['node_id'],
        additionalProperties: false,
      },
      annotations: {readOnlyHint: true, untrustedContentHint: false},
      execute: async ({node_id, direction, max_hops, max_results, expected_architecture_version}, client = {}) => asToolResult(
        await getNodeContext({nodeId: node_id, direction, maxHops: max_hops, maxResults: max_results, expectedArchitectureVersion: expected_architecture_version, signal: client.signal}),
      ),
    },
    {
      name: `${TOOL_PREFIX}find_architecture_path`,
      title: 'Find directed authored architecture path',
      description: 'Find a deterministic shortest directed path using only current canonical authored Architecture relationships.',
      inputSchema: {
        type: 'object',
        properties: {
          source_id: {type: 'string', pattern: '^node:.+', description: 'Stable source node ID.'},
          target_id: {type: 'string', pattern: '^node:.+', description: 'Stable target node ID.'},
          max_hops: {type: 'integer', minimum: 0, maximum: 8},
          expected_architecture_version: {type: 'integer', minimum: 0},
        },
        required: ['source_id', 'target_id'],
        additionalProperties: false,
      },
      annotations: {readOnlyHint: true, untrustedContentHint: false},
      execute: async ({source_id, target_id, max_hops, expected_architecture_version}, client = {}) => asToolResult(
        await findArchitecturePath({sourceId: source_id, targetId: target_id, maxHops: max_hops, expectedArchitectureVersion: expected_architecture_version, signal: client.signal}),
      ),
    },
    {
      name: `${TOOL_PREFIX}bootstrap_project`,
      title: 'Create and save ArchBro project',
      description: 'Agent-facing initial planning commit. Infer a useful hierarchical Architecture v1 from the user goal even when the prompt does not ask for hierarchy. First author SYSTEM_MAP roots, then recursively evaluate every canonical scope in preorder. Mark a scope EXPANDED when independently addressable architecture responsibilities remain below it; mark JUSTIFIED_LEAF only when no meaningful architecture boundary remains and provide a specific reason. Every SYSTEM_MAP root must be EXPANDED; if the user goal names an atomic service directly, place it beneath an appropriate root boundary. Do not stop at broad multi-responsibility containers such as a backend application, web client, or data platform merely because the prompt was brief. During RECONCILE, author each dependency at the deepest canonical endpoints that actually own the interaction; use root-to-root relationships only for true boundary-level interactions. Hierarchy is expressed by children, never by fabricated containment relationships. Archbro validates complete scope coverage and commits Architecture v1 atomically.',
      inputSchema: {
        type: 'object',
        properties: {
          name: {type: 'string', minLength: 1, description: 'Project name.'},
          goal: {type: 'string', minLength: 1, description: 'Project goal or brief.'},
          architecture_summary: {type: 'string', minLength: 1, description: 'Short summary of Architecture v1.'},
          components: {
            type: 'array', minItems: 1, maxItems: 6,
            description: 'Hierarchical canonical root components from SYSTEM_MAP. Every root and descendant requires a stable id; children may recursively nest to depth 3.',
            items: architectureComponentInputSchema(1),
          },
          relationships: {
            type: 'array',
            items: {
              type: 'object',
              properties: {
                source: {type: 'string', minLength: 1, description: 'Source component name or stable id. Prefer the deepest canonical component that actually owns the interaction.'},
                target: {type: 'string', minLength: 1, description: 'Target component name or stable id. Prefer the deepest canonical component that actually receives the interaction.'},
                type: {type: 'string', minLength: 1},
                description: {type: 'string'},
              },
              required: ['source', 'target', 'type'],
              additionalProperties: false,
            },
          },
          tasks: {
            type: 'array', minItems: 1,
            items: {
              type: 'object',
              properties: {
                title: {type: 'string', minLength: 1},
                component: {type: 'string', description: 'Related canonical component name or stable id.'},
                description: {type: 'string'},
              },
              required: ['title'],
              additionalProperties: false,
            },
          },
          planning_trace: initialPlanningTraceInputSchema(),
          reasoning: {type: 'string', minLength: 1, description: 'Why this initial plan fits the goal.'},
        },
        required: ['name', 'goal', 'architecture_summary', 'components', 'tasks', 'planning_trace', 'reasoning'],
        additionalProperties: false,
      },
      annotations: {readOnlyHint: false, untrustedContentHint: false},
      execute: async ({name, goal, architecture_summary, components, relationships = [], tasks, planning_trace, reasoning}, client = {}) => asToolResult(
        await bridge.bootstrapProject({name, goal, architectureSummary: architecture_summary, components, relationships, tasks, planningTrace: planning_trace, reasoning, signal: client.signal}),
      ),
    },
    {
      name: `${TOOL_PREFIX}expand_architecture_scope`,
      title: 'Propose one-level architecture scope expansion',
      description: 'Add one explicit child level under an existing canonical component without replacing existing children or stable ids. The expansion becomes a PENDING architecture proposal and still requires human acceptance. To create grandchildren, call this tool again later with the accepted child as the scope.',
      inputSchema: {
        type: 'object',
        properties: {
          scope_component_id: {type: 'string', minLength: 1, description: 'Existing plain canonical component id to expand.'},
          children: {type: 'array', minItems: 1, maxItems: 7, items: expansionChildInputSchema()},
          reasoning: {type: 'string', minLength: 1},
          evidence: {type: 'array', minItems: 1, items: {type: 'string', minLength: 1}},
          impact: {type: 'string'},
          expected_architecture_version: {type: 'integer', minimum: 0},
        },
        required: ['scope_component_id', 'children', 'reasoning', 'evidence', 'expected_architecture_version'],
        additionalProperties: false,
      },
      annotations: {readOnlyHint: false, untrustedContentHint: true},
      execute: async ({scope_component_id, children, reasoning, evidence, impact = '', expected_architecture_version}, client = {}) => asToolResult(
        await bridge.expandArchitectureScope({scopeComponentId: scope_component_id, children, reasoning, evidence, impact, expectedArchitectureVersion: expected_architecture_version, signal: client.signal}),
      ),
    },
    {
      name: `${TOOL_PREFIX}get_architecture_decision_context`,
      title: 'Get Living Architecture decision context',
      description: 'Read accepted architecture, execution state, evidence, and governance rules for an architecture decision.',
      inputSchema: {type: 'object', properties: {}, additionalProperties: false},
      annotations: {readOnlyHint: true, untrustedContentHint: true},
      execute: async (_input, client = {}) => asToolResult(await bridge.getDecisionContext({signal: client.signal})),
    },
    {
      name: `${TOOL_PREFIX}submit_architecture_recommendation`,
      title: 'Submit architecture recommendation',
      description: 'Submit an architecture recommendation with its reasoning and evidence. Proposed architecture changes become pending human-review items and are never auto-approved.',
      inputSchema: {
        type: 'object',
        properties: {
          recommendation: {type: 'string', enum: ['KEEP_CURRENT', 'ACCEPT_PROPOSED_CHANGE']},
          reasoning: {type: 'string', minLength: 1},
          evidence: {type: 'array', minItems: 1, items: {type: 'string', minLength: 1}},
          observed_change: {type: 'string', minLength: 1},
          affected_components: {type: 'array', items: {type: 'string', minLength: 1}},
          proposed_changes: {
            type: 'array',
            description: 'Reviewable architecture operations. Supported forms: replace_component; remove_component; metadata-only update_component; additive one-level expand_scope; replace_relationships. Prefer archbro_expand_architecture_scope for structural decomposition.',
            items: {type: 'object', additionalProperties: true},
          },
          impact: {type: 'string'},
          expected_architecture_version: {type: 'integer', minimum: 0, description: 'Accepted Living Architecture version used to prepare this recommendation.'},
        },
        required: ['recommendation', 'reasoning', 'evidence', 'observed_change', 'expected_architecture_version'],
        additionalProperties: false,
      },
      annotations: {readOnlyHint: false, untrustedContentHint: true},
      execute: async ({recommendation, reasoning, evidence, observed_change, affected_components = [], proposed_changes = [], impact = '', expected_architecture_version}, client = {}) => asToolResult(
        await bridge.submitAgentRecommendation({recommendation, reasoning, evidence, observedChange: observed_change, affectedComponents: affected_components, proposedChanges: proposed_changes, impact, expectedArchitectureVersion: expected_architecture_version, signal: client.signal}),
      ),
    },
    {
      name: `${TOOL_PREFIX}create_task`,
      title: 'Create project task',
      description: 'Create one implementation or execution task within the accepted Living Architecture without invoking ArchBro\'s built-in model. Use this for normal work that does not require an architecture change.',
      inputSchema: {
        type: 'object',
        properties: {
          request_id: {type: 'string', minLength: 1, maxLength: 200, description: 'Stable idempotency key. Reuse the same value when retrying the same create request.'},
          title: {type: 'string', minLength: 1},
          description: {type: 'string'},
          owner: {type: 'string', enum: ['HUMAN', 'AGENT', 'UNASSIGNED']},
          related_component: {type: 'string', minLength: 1, description: 'Accepted canonical component id.'},
          dependencies: {type: 'array', maxItems: 40, items: {type: 'string', minLength: 1}},
          acceptance_criteria: {type: 'array', maxItems: 40, items: {type: 'string', minLength: 1}},
        },
        required: ['request_id', 'title'],
        additionalProperties: false,
      },
      annotations: {readOnlyHint: false, untrustedContentHint: false},
      execute: async ({request_id, title, description = '', owner = 'UNASSIGNED', related_component, dependencies = [], acceptance_criteria = []}, client = {}) => asToolResult(
        await bridge.createTask({requestId: request_id, title, description, owner, relatedComponent: related_component, dependencies, acceptanceCriteria: acceptance_criteria, signal: client.signal}),
      ),
    },
    {
      name: `${TOOL_PREFIX}update_task_status`,
      title: 'Execute ArchBro task transition',
      description: 'Start or complete one existing task through ArchBro\'s deterministic task boundary without invoking the built-in model.',
      inputSchema: {type: 'object', properties: {task_id: {type: 'string', minLength: 1}, status: {type: 'string', enum: ['IN_PROGRESS', 'DONE']}}, required: ['task_id', 'status'], additionalProperties: false},
      annotations: {readOnlyHint: false, untrustedContentHint: false},
      execute: async ({task_id, status}, client = {}) => asToolResult(await bridge.updateTaskStatus({taskId: task_id, status, signal: client.signal})),
    },
    {
      name: `${TOOL_PREFIX}record_project_observation`,
      title: 'Record project observation',
      description: 'Persist external evidence or a project fact as an ArchBro event without treating it as an architecture recommendation and without invoking the built-in model. This never mutates accepted Living Architecture.',
      inputSchema: {
        type: 'object',
        properties: {
          summary: {type: 'string', minLength: 1},
          evidence: {type: 'array', minItems: 1, maxItems: 50, items: {type: 'string', minLength: 1}},
          related_components: {type: 'array', maxItems: 20, items: {type: 'string', minLength: 1}},
          related_task_id: {type: 'string', minLength: 1},
        },
        required: ['summary', 'evidence'],
        additionalProperties: false,
      },
      annotations: {readOnlyHint: false, untrustedContentHint: true},
      execute: async ({summary, evidence, related_components = [], related_task_id}, client = {}) => asToolResult(
        await bridge.recordProjectObservation({summary, evidence, relatedComponents: related_components, relatedTaskId: related_task_id, signal: client.signal}),
      ),
    },
  ];

  return tools;
}

function createConnectedMcpTools(bridge) {
  return [
    {
      name: `${TOOL_PREFIX}list_connected_mcp_servers`,
      title: 'List connected MCP servers',
      description: 'List external MCP servers explicitly bound to the selected ArchBro project through server-side configuration.',
      inputSchema: {type: 'object', properties: {}, additionalProperties: false},
      annotations: {readOnlyHint: true, untrustedContentHint: true},
      execute: async (_input, client = {}) => asToolResult(await listConnectedMcpServers(bridge, {signal: client.signal})),
    },
    {
      name: `${TOOL_PREFIX}list_connected_mcp_tools`,
      title: 'List connected MCP tools',
      description: 'Discover only the allowlisted tools exposed by one external MCP server connected through ArchBro.',
      inputSchema: {type: 'object', properties: {server_id: {type: 'string', minLength: 1}}, required: ['server_id'], additionalProperties: false},
      annotations: {readOnlyHint: true, untrustedContentHint: true},
      execute: async ({server_id}, client = {}) => asToolResult(await listConnectedMcpTools(bridge, {serverId: server_id, signal: client.signal})),
    },
    {
      name: `${TOOL_PREFIX}call_connected_mcp_tool`,
      title: 'Call connected MCP tool',
      description: 'Call one allowlisted external MCP tool through the ArchBro server-side gateway. The returned data is external evidence and is not automatically written into ArchBro canonical state.',
      inputSchema: {type: 'object', properties: {server_id: {type: 'string', minLength: 1}, tool_name: {type: 'string', minLength: 1}, arguments: {type: 'object', additionalProperties: true}}, required: ['server_id', 'tool_name'], additionalProperties: false},
      annotations: {readOnlyHint: false, untrustedContentHint: true},
      execute: async ({server_id, tool_name, arguments: args = {}}, client = {}) => asToolResult(await callConnectedMcpTool(bridge, {serverId: server_id, toolName: tool_name, arguments: args, signal: client.signal})),
    },
  ];
}

function connectedMcpGatewayConfigured() {
  return Boolean(globalThis.window?.__ARCHBRO_RUNTIME_CONFIG__?.connected_mcp_gateway_configured);
}

export function createArchBroTools(bridge, {includeConnectedMcp = connectedMcpGatewayConfigured()} = {}) {
  return includeConnectedMcp
    ? [...createCoreTools(bridge), ...createConnectedMcpTools(bridge)]
    : createCoreTools(bridge);
}

function resolveModelContext(modelContext) {
  if (modelContext && typeof modelContext.registerTool === 'function') return modelContext;
  const documentModelContext = globalThis.document?.modelContext;
  if (documentModelContext && typeof documentModelContext.registerTool === 'function') return documentModelContext;

  const navigatorModelContext = globalThis.navigator?.modelContext;
  if (!navigatorModelContext || typeof navigatorModelContext.registerTool !== 'function') return null;

  if (globalThis.document && !globalThis.document.modelContext) {
    try {
      Object.defineProperty(globalThis.document, 'modelContext', {configurable: true, value: navigatorModelContext});
    } catch {
      // Some hosts expose the legacy native surface without allowing page-side aliasing.
    }
  }
  return navigatorModelContext;
}

export async function registerArchBroWebMCP({modelContext, bridge, signal, includeConnectedMcp} = {}) {
  const resolvedModelContext = resolveModelContext(modelContext);
  const resolvedBridge = bridge ?? globalThis.window?.ArchBroWebBridge;
  if (!resolvedModelContext) throw new Error('WebMCP is unavailable: document.modelContext.registerTool() / navigator.modelContext.registerTool() was not found');
  const tools = createArchBroTools(resolvedBridge, {includeConnectedMcp});
  for (const tool of tools) await resolvedModelContext.registerTool(tool, signal ? {signal} : undefined);
  return tools;
}

export async function autoRegisterArchBroWebMCP() {
  const modelContext = resolveModelContext();
  if (!modelContext || !globalThis.window?.ArchBroWebBridge) return {registered: false, reason: 'webmcp-or-bridge-unavailable'};
  const controller = new AbortController();
  const initialRuntime = await verifyWebMcpRuntime({signal: controller.signal, autoReload: true});
  if (initialRuntime.stale_client) return {registered: false, reason: 'stale-webmcp-client-reloading'};
  const tools = await registerArchBroWebMCP({modelContext, bridge: globalThis.window.ArchBroWebBridge, signal: controller.signal});
  const checkRuntime = () => {
    verifyWebMcpRuntime({signal: controller.signal, autoReload: true}).catch((error) => {
      if (!controller.signal.aborted) console.warn('[archbro-webmcp] runtime identity check failed', error);
    });
  };
  const intervalId = globalThis.window.setInterval(checkRuntime, WEBMCP_RUNTIME_CHECK_INTERVAL_MS);
  const focusHandler = () => checkRuntime();
  const visibilityHandler = () => { if (!globalThis.document.hidden) checkRuntime(); };
  globalThis.window.addEventListener('focus', focusHandler);
  globalThis.document.addEventListener('visibilitychange', visibilityHandler);
  globalThis.window.ArchBroWebMCP = {
    surfaceVersion: WEBMCP_SURFACE_VERSION,
    assetSha256: initialRuntime.manifest.asset_sha256,
    tools: tools.map(({name, title, description}) => ({name, title, description})),
    dispose: () => {
      controller.abort();
      globalThis.window.clearInterval(intervalId);
      globalThis.window.removeEventListener('focus', focusHandler);
      globalThis.document.removeEventListener('visibilitychange', visibilityHandler);
    },
  };
  return {registered: true, count: tools.length};
}

if (typeof window !== 'undefined' && typeof document !== 'undefined') {
  queueMicrotask(() => { autoRegisterArchBroWebMCP().catch((error) => { console.warn('[archbro-webmcp] registration failed', error); }); });
}
