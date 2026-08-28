const TOOL_PREFIX = 'archbro_';

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

function createCoreTools(bridge) {
  requireBridgeMethod(bridge, 'bootstrapProject');
  requireBridgeMethod(bridge, 'getProjectBrief');
  requireBridgeMethod(bridge, 'getDecisionContext');
  requireBridgeMethod(bridge, 'submitAgentRecommendation');
  requireBridgeMethod(bridge, 'updateTaskStatus');
  requireBridgeMethod(bridge, 'focusPendingReview');

  const tools = [
    {
      name: `${TOOL_PREFIX}ping`,
      title: 'Ping ArchBro Site Tool',
      description: 'Minimal read-only WebMCP capability check. Returns immediately without browser UI interaction, project mutation, or model invocation.',
      inputSchema: {type: 'object', properties: {}, additionalProperties: false},
      annotations: {readOnlyHint: true, untrustedContentHint: false},
      execute: async () => asToolResult({ok: true, surface: 'archbro-webmcp', built_in_model_called: false}),
    },
    {
      name: `${TOOL_PREFIX}bootstrap_project`,
      title: 'Create and save ArchBro project',
      description: 'Agent-facing project creation. When the user asks to create, start, initialize, or plan an ArchBro project, save the project goal, Architecture v1, and initial tasks in one operation instead of filling the human New Project form.',
      inputSchema: {
        type: 'object',
        properties: {
          name: {type: 'string', minLength: 1, description: 'Project name.'},
          goal: {type: 'string', minLength: 1, description: 'Project goal or brief.'},
          architecture_summary: {type: 'string', minLength: 1, description: 'Short summary of Architecture v1.'},
          components: {
            type: 'array', minItems: 1,
            items: {
              type: 'object',
              properties: {
                name: {type: 'string', minLength: 1},
                type: {type: 'string', minLength: 1},
                responsibility: {type: 'string', minLength: 1},
              },
              required: ['name', 'type', 'responsibility'],
              additionalProperties: false,
            },
          },
          relationships: {
            type: 'array',
            items: {
              type: 'object',
              properties: {
                source: {type: 'string', minLength: 1, description: 'Source component name.'},
                target: {type: 'string', minLength: 1, description: 'Target component name.'},
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
                component: {type: 'string', description: 'Related component name.'},
                description: {type: 'string'},
              },
              required: ['title'],
              additionalProperties: false,
            },
          },
          reasoning: {type: 'string', minLength: 1, description: 'Why this initial plan fits the goal.'},
        },
        required: ['name', 'goal', 'architecture_summary', 'components', 'tasks', 'reasoning'],
        additionalProperties: false,
      },
      annotations: {readOnlyHint: false, untrustedContentHint: false},
      execute: async ({name, goal, architecture_summary, components, relationships = [], tasks, reasoning}, client = {}) => asToolResult(
        await bridge.bootstrapProject({name, goal, architectureSummary: architecture_summary, components, relationships, tasks, reasoning, signal: client.signal}),
      ),
    },
    {
      name: `${TOOL_PREFIX}get_project_brief`,
      title: 'Get ArchBro project brief',
      description: 'Read the current project, architecture health, task state, recent activity, blockers, and pending human attention in one call.',
      inputSchema: {type: 'object', properties: {}, additionalProperties: false},
      annotations: {readOnlyHint: true, untrustedContentHint: true},
      execute: async (_input, client = {}) => asToolResult(await bridge.getProjectBrief({signal: client.signal})),
    },
    {
      name: `${TOOL_PREFIX}get_decision_context`,
      title: 'Get ArchBro decision context',
      description: 'Read accepted architecture, execution state, evidence, and governance rules for an architecture decision.',
      inputSchema: {type: 'object', properties: {}, additionalProperties: false},
      annotations: {readOnlyHint: true, untrustedContentHint: true},
      execute: async (_input, client = {}) => asToolResult(await bridge.getDecisionContext({signal: client.signal})),
    },
    {
      name: `${TOOL_PREFIX}submit_agent_recommendation`,
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
            description: 'Reviewable architecture operations. Supported forms: replace_component with component_id plus either legacy new_* fields or a replacement object; remove_component with component_id; update_component with component_id and changes; replace_relationships with a changes array.',
            items: {type: 'object', additionalProperties: true},
          },
          impact: {type: 'string'},
        },
        required: ['recommendation', 'reasoning', 'evidence', 'observed_change'],
        additionalProperties: false,
      },
      annotations: {readOnlyHint: false, untrustedContentHint: true},
      execute: async ({recommendation, reasoning, evidence, observed_change, affected_components = [], proposed_changes = [], impact = ''}, client = {}) => asToolResult(
        await bridge.submitAgentRecommendation({recommendation, reasoning, evidence, observedChange: observed_change, affectedComponents: affected_components, proposedChanges: proposed_changes, impact, signal: client.signal}),
      ),
    },
    {
      name: `${TOOL_PREFIX}update_task_status`,
      title: 'Execute ArchBro task transition',
      description: 'Start or complete one existing task through ArchBro deterministic task boundary.',
      inputSchema: {type: 'object', properties: {task_id: {type: 'string', minLength: 1}, status: {type: 'string', enum: ['IN_PROGRESS', 'DONE']}}, required: ['task_id', 'status'], additionalProperties: false},
      annotations: {readOnlyHint: false, untrustedContentHint: false},
      execute: async ({task_id, status}, client = {}) => asToolResult(await bridge.updateTaskStatus({taskId: task_id, status, signal: client.signal})),
    },
    {
      name: `${TOOL_PREFIX}focus_pending_review`,
      title: 'Take human to pending review',
      description: 'Move the ArchBro page directly to the first pending architecture review. Navigation only; the human remains responsible for accept/reject.',
      inputSchema: {type: 'object', properties: {}, additionalProperties: false},
      annotations: {readOnlyHint: false, untrustedContentHint: false},
      execute: async (_input, client = {}) => asToolResult(await bridge.focusPendingReview({signal: client.signal})),
    },
  ];

  return tools;
}

function createConnectedMcpTools(bridge) {
  const methods = ['listConnectedMcpServers', 'listConnectedMcpTools', 'callConnectedMcpTool'];
  if (!methods.every((method) => hasBridgeMethod(bridge, method))) return [];
  return [
    {name: `${TOOL_PREFIX}list_connected_mcp_servers`, title: 'List connected MCP servers', description: 'List external MCP servers connected through the ArchBro MCP gateway.', inputSchema: {type: 'object', properties: {}, additionalProperties: false}, annotations: {readOnlyHint: true, untrustedContentHint: true}, execute: async (_input, client = {}) => asToolResult(await bridge.listConnectedMcpServers({signal: client.signal}))},
    {name: `${TOOL_PREFIX}list_connected_mcp_tools`, title: 'List connected MCP tools', description: 'Discover tools exposed by one external MCP server connected through ArchBro.', inputSchema: {type: 'object', properties: {server_id: {type: 'string', minLength: 1}}, required: ['server_id'], additionalProperties: false}, annotations: {readOnlyHint: true, untrustedContentHint: true}, execute: async ({server_id}, client = {}) => asToolResult(await bridge.listConnectedMcpTools({serverId: server_id, signal: client.signal}))},
    {name: `${TOOL_PREFIX}call_connected_mcp_tool`, title: 'Call connected MCP tool', description: 'Call an allowed external MCP tool through ArchBro.', inputSchema: {type: 'object', properties: {server_id: {type: 'string', minLength: 1}, tool_name: {type: 'string', minLength: 1}, arguments: {type: 'object', additionalProperties: true}}, required: ['server_id', 'tool_name'], additionalProperties: false}, annotations: {readOnlyHint: false, untrustedContentHint: true}, execute: async ({server_id, tool_name, arguments: args = {}}, client = {}) => asToolResult(await bridge.callConnectedMcpTool({serverId: server_id, toolName: tool_name, arguments: args, signal: client.signal}))},
  ];
}

export function createArchBroTools(bridge) {
  return [...createCoreTools(bridge), ...createConnectedMcpTools(bridge)];
}

export async function registerArchBroWebMCP({modelContext, bridge, signal} = {}) {
  const resolvedModelContext = modelContext ?? globalThis.document?.modelContext;
  const resolvedBridge = bridge ?? globalThis.window?.ArchBroWebBridge;
  if (!resolvedModelContext || typeof resolvedModelContext.registerTool !== 'function') throw new Error('WebMCP is unavailable: document.modelContext.registerTool() was not found');
  const tools = createArchBroTools(resolvedBridge);
  for (const tool of tools) await resolvedModelContext.registerTool(tool, signal ? {signal} : undefined);
  return tools;
}

export async function autoRegisterArchBroWebMCP() {
  if (!globalThis.document?.modelContext || !globalThis.window?.ArchBroWebBridge) return {registered: false, reason: 'webmcp-or-bridge-unavailable'};
  const controller = new AbortController();
  const tools = await registerArchBroWebMCP({modelContext: globalThis.document.modelContext, bridge: globalThis.window.ArchBroWebBridge, signal: controller.signal});
  globalThis.window.ArchBroWebMCP = {tools: tools.map(({name, title, description}) => ({name, title, description})), dispose: () => controller.abort()};
  return {registered: true, count: tools.length};
}

if (typeof window !== 'undefined' && typeof document !== 'undefined') {
  queueMicrotask(() => { autoRegisterArchBroWebMCP().catch((error) => { console.warn('[archbro-webmcp] registration failed', error); }); });
}
