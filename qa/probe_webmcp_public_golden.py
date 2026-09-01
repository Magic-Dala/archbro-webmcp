from __future__ import annotations

import json
import os
from playwright.sync_api import sync_playwright

URL = os.getenv("ARCHBRO_WEB_URL", "http://127.0.0.1:8012/?mode=webmcp")

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page()
    page.add_init_script(
        """
        window.__archbroRegisteredTools = {};
        Object.defineProperty(document, 'modelContext', {
          configurable: true,
          value: {
            async registerTool(tool) {
              window.__archbroRegisteredTools[tool.name] = tool;
            }
          }
        });
        """
    )
    page.goto(URL, wait_until="networkidle")
    page.wait_for_function("() => Object.keys(window.__archbroRegisteredTools || {}).length >= 7")

    bootstrap = json.loads(page.evaluate(
        """async () => await window.__archbroRegisteredTools.archbro_bootstrap_project.execute({
          name: 'Public WebMCP Golden Probe',
          goal: 'Build a collaborative issue tracker with React, FastAPI, PostgreSQL, and realtime collaboration.',
          architecture_summary: 'React uses FastAPI, PostgreSQL, and a custom realtime collaboration channel.',
          components: [
            {id: 'react-web-client', name: 'React Web Client', type: 'Frontend SPA', responsibility: 'Collaborative issue tracking UI', children: [
              {id: 'issue-workspace', name: 'Issue Workspace', type: 'UI module', responsibility: 'Issue collaboration experience'},
              {id: 'client-state-adapter', name: 'Client State Adapter', type: 'Client integration', responsibility: 'Client state and synchronization boundary'}
            ]},
            {id: 'fastapi-application', name: 'FastAPI Application', type: 'Backend API', responsibility: 'Application API and privileged operations', children: [
              {id: 'api-layer', name: 'API Layer', type: 'HTTP boundary', responsibility: 'Receive application requests'},
              {id: 'application-services', name: 'Application Services', type: 'Domain services', responsibility: 'Run issue tracking workflows'},
              {id: 'integration-boundary', name: 'Integration Boundary', type: 'Backend integration', responsibility: 'Coordinate persistence and external integrations'}
            ]},
            {id: 'postgresql-database', name: 'PostgreSQL Database', type: 'Relational persistence', responsibility: 'Durable issue-tracking state', children: [
              {id: 'project-state-store', name: 'Project State', type: 'Relational data', responsibility: 'Persist project and issue state'}
            ]},
            {id: 'realtime-collaboration-channel', name: 'Realtime Collaboration Channel', type: 'WebSocket service', responsibility: 'Custom realtime collaboration', children: [
              {id: 'websocket-channel', name: 'WebSocket Channel', type: 'Realtime transport', responsibility: 'Deliver collaboration events'}
            ]}
          ],
          relationships: [
            {source: 'issue-workspace', target: 'client-state-adapter', type: 'STATE_ACTION', description: 'Workspace delegates state actions'},
            {source: 'client-state-adapter', target: 'api-layer', type: 'HTTPS JSON REST', description: 'Application requests'},
            {source: 'api-layer', target: 'application-services', type: 'APPLICATION_CALL', description: 'API delegates workflows'},
            {source: 'application-services', target: 'integration-boundary', type: 'ADAPTER_CALL', description: 'Workflow invokes integrations'},
            {source: 'integration-boundary', target: 'project-state-store', type: 'SQL', description: 'Persistence'},
            {source: 'client-state-adapter', target: 'websocket-channel', type: 'WebSocket', description: 'Realtime updates'}
          ],
          tasks: [
            {title: 'Build collaborative React interface', component: 'issue-workspace'},
            {title: 'Define PostgreSQL schema and migrations', component: 'project-state-store'},
            {title: 'Add custom realtime updates', component: 'websocket-channel'}
          ],
          planning_trace: {
            system_map_root_ids: ['react-web-client', 'fastapi-application', 'postgresql-database', 'realtime-collaboration-channel'],
            scope_evaluations: [
              {scope_component_id: 'react-web-client', decomposition: 'EXPANDED', child_ids: ['issue-workspace', 'client-state-adapter']},
              {scope_component_id: 'issue-workspace', decomposition: 'JUSTIFIED_LEAF', child_ids: [], leaf_reason: 'Issue Workspace is one user-facing collaboration boundary with no independent architecture subsystem below it.'},
              {scope_component_id: 'client-state-adapter', decomposition: 'JUSTIFIED_LEAF', child_ids: [], leaf_reason: 'Client State Adapter is the single client synchronization boundary and requires no lower architecture split.'},
              {scope_component_id: 'fastapi-application', decomposition: 'EXPANDED', child_ids: ['api-layer', 'application-services', 'integration-boundary']},
              {scope_component_id: 'api-layer', decomposition: 'JUSTIFIED_LEAF', child_ids: [], leaf_reason: 'API Layer is one HTTP application boundary and has no independently addressable subsystem below it.'},
              {scope_component_id: 'application-services', decomposition: 'JUSTIFIED_LEAF', child_ids: [], leaf_reason: 'Application Services owns the issue workflow boundary without another independently deployable architecture layer.'},
              {scope_component_id: 'integration-boundary', decomposition: 'JUSTIFIED_LEAF', child_ids: [], leaf_reason: 'Integration Boundary is the single backend adapter boundary for persistence and external access in this demo.'},
              {scope_component_id: 'postgresql-database', decomposition: 'EXPANDED', child_ids: ['project-state-store']},
              {scope_component_id: 'project-state-store', decomposition: 'JUSTIFIED_LEAF', child_ids: [], leaf_reason: 'Project State is the single durable relational data boundary required by this demo architecture.'},
              {scope_component_id: 'realtime-collaboration-channel', decomposition: 'EXPANDED', child_ids: ['websocket-channel']},
              {scope_component_id: 'websocket-channel', decomposition: 'JUSTIFIED_LEAF', child_ids: [], leaf_reason: 'WebSocket Channel is one realtime transport boundary and needs no further architecture decomposition.'}
            ],
            reconciled: true
          },
          reasoning: 'The host planned SYSTEM_MAP roots, recursively evaluated every canonical scope, justified true leaves, then reconciled endpoint-level relationships and tasks.'
        })"""
    ))
    project_id = bootstrap["project"]["id"]

    try:
        observation = json.loads(page.evaluate(
            """async () => await window.__archbroRegisteredTools.archbro_record_project_observation.execute({
              summary: 'PostgreSQL staging connection-pool health checks are failing.',
              evidence: ['Staging health check reports connection-pool failure.'],
              related_components: ['postgresql-database']
            })"""
        ))
        assert observation["canonical_architecture_mutated"] is False
        assert observation["built_in_model_called"] is False

        keep = json.loads(page.evaluate(
            """async () => await window.__archbroRegisteredTools.archbro_submit_architecture_recommendation.execute({
              recommendation: 'KEEP_CURRENT',
              reasoning: 'A PostgreSQL staging connection-pool health failure is operational and does not by itself invalidate the accepted persistence boundary.',
              evidence: ['PostgreSQL staging connection pool is failing health checks.'],
              observed_change: 'Staging database connectivity is degraded.',
              affected_components: ['postgresql-database'],
              proposed_changes: [],
              impact: '',
              expected_architecture_version: 1
            })"""
        ))
        assert keep["architecture_review_required"] is False
        assert keep["proposal"] is None

        recommendation = json.loads(page.evaluate(
            """async () => await window.__archbroRegisteredTools.archbro_submit_architecture_recommendation.execute({
              recommendation: 'ACCEPT_PROPOSED_CHANGE',
              reasoning: 'The approved release now requires offline-first clients, managed Firebase persistence, and no custom realtime persistence channel, so Architecture v1 no longer satisfies the accepted requirements.',
              evidence: [
                'Approved release requirement: offline-first clients with automatic background synchronization.',
                'Platform standard: Firebase Auth and Cloud Firestore; retire the custom WebSocket persistence path.'
              ],
              observed_change: 'Persistence and realtime synchronization responsibilities moved to Firebase-managed state.',
              affected_components: ['postgresql-database', 'realtime-collaboration-channel', 'react-web-client', 'fastapi-application'],
              proposed_changes: [
                {
                  operation: 'replace_component',
                  component_id: 'postgresql-database',
                  replacement: {
                    id: 'firebase-managed-data-platform',
                    name: 'Firebase Managed Data Platform',
                    type: 'Cloud Firestore and Firebase Auth',
                    responsibility: 'Managed identity, persistence, offline sync, and realtime state delivery.'
                  }
                },
                {operation: 'remove_component', component_id: 'realtime-collaboration-channel'},
                {
                  operation: 'update_component',
                  component_id: 'react-web-client',
                  changes: {responsibility: 'Use Firebase SDK for offline persistence, background sync, and snapshot listeners.'}
                },
                {
                  operation: 'update_component',
                  component_id: 'fastapi-application',
                  changes: {responsibility: 'Retain privileged operations and integrations through Firebase Admin SDK.'}
                },
                {
                  operation: 'replace_relationships',
                  changes: [
                    {source: 'react-web-client', target: 'firebase-managed-data-platform', type: 'Firebase SDK'},
                    {source: 'react-web-client', target: 'fastapi-application', type: 'HTTPS JSON REST'},
                    {source: 'fastapi-application', target: 'firebase-managed-data-platform', type: 'Firebase Admin SDK'}
                  ]
                }
              ],
              impact: 'Persistence, realtime synchronization, client state handling, and privileged backend access change.',
              expected_architecture_version: 1
            })"""
        ))
        proposal = recommendation["proposal"]
        assert proposal["status"] == "PENDING"

        accepted = page.evaluate(
            """async ({projectId, proposalId}) => {
              const {getFirebaseIdToken} = await import('/static/firebase-auth.js');
              const token = await getFirebaseIdToken();
              const headers = token ? {Authorization: `Bearer ${token}`} : {};
              const response = await fetch(
                `/projects/${projectId}/architecture/proposals/${proposalId}/accept`,
                {method: 'POST', headers},
              );
              if (!response.ok) throw new Error(await response.text());
              return await response.json();
            }""",
            {"projectId": project_id, "proposalId": proposal["id"]},
        )
        assert accepted["status"] == "ACCEPTED"

        decision = json.loads(page.evaluate(
            "async () => await window.__archbroRegisteredTools.archbro_get_architecture_decision_context.execute({})"
        ))
        brief = decision["project_brief"]
        assert brief["architecture"]["version"] == 2
        ready = [
            task for task in brief["execution"]["ready"]
            if task.get("related_component") == "firebase-managed-data-platform"
        ]
        assert len(ready) == 1

        started = json.loads(page.evaluate(
            """async (taskId) => await window.__archbroRegisteredTools.archbro_update_task_status.execute({
              task_id: taskId,
              status: 'IN_PROGRESS'
            })""",
            ready[0]["id"],
        ))
        assert started["task"]["status"] == "IN_PROGRESS"

        created = json.loads(page.evaluate(
            """async () => await window.__archbroRegisteredTools.archbro_create_task.execute({
              request_id: 'golden-idempotent-create',
              title: 'Verify semantic create retry safety',
              related_component: 'firebase-managed-data-platform',
              acceptance_criteria: ['Retry returns the original task and event.']
            })"""
        ))
        retried = json.loads(page.evaluate(
            """async () => await window.__archbroRegisteredTools.archbro_create_task.execute({
              request_id: 'golden-idempotent-create',
              title: 'Verify semantic create retry safety',
              related_component: 'firebase-managed-data-platform',
              acceptance_criteria: ['Retry returns the original task and event.']
            })"""
        ))
        assert retried["task"]["id"] == created["task"]["id"]
        assert retried["event_id"] == created["event_id"]

        final_decision = json.loads(page.evaluate(
            "async () => await window.__archbroRegisteredTools.archbro_get_architecture_decision_context.execute({})"
        ))
        final_brief = final_decision["project_brief"]
        assert final_brief["architecture"]["version"] == 2
        assert any(
            task["id"] == ready[0]["id"] and task["status"] == "IN_PROGRESS"
            for task in final_brief["execution"]["in_progress"]
        )
        created_matches = [
            task for task in final_brief["execution"]["ready"]
            if task["id"] == created["task"]["id"]
        ]
        assert len(created_matches) == 1

        print(json.dumps({
            "project_id": project_id,
            "architecture_version": final_brief["architecture"]["version"],
            "proposal_status": accepted["status"],
            "started_task": started["task"]["title"],
            "started_task_status": started["task"]["status"],
            "idempotent_create_task_id": created["task"]["id"],
            "idempotent_create_event_id": created["event_id"],
            "built_in_model_called": bootstrap["built_in_model_called"],
            "result": "PASS",
        }, indent=2))
    finally:
        page.evaluate(
            """async (projectId) => {
              const {getFirebaseIdToken} = await import('/static/firebase-auth.js');
              const token = await getFirebaseIdToken();
              const headers = token ? {Authorization: `Bearer ${token}`} : {};
              await fetch(`/projects/${projectId}`, {method: 'DELETE', headers});
            }""",
            project_id,
        )
        browser.close()
