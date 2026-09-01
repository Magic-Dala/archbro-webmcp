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
            {id: 'react-web-client', name: 'React Web Client', type: 'Frontend SPA', responsibility: 'Collaborative issue tracking UI'},
            {id: 'fastapi-application', name: 'FastAPI Application', type: 'Backend API', responsibility: 'Application API and privileged operations'},
            {id: 'postgresql-database', name: 'PostgreSQL Database', type: 'Relational persistence', responsibility: 'Durable issue-tracking state'},
            {id: 'realtime-collaboration-channel', name: 'Realtime Collaboration Channel', type: 'WebSocket service', responsibility: 'Custom realtime collaboration'}
          ],
          relationships: [
            {source: 'React Web Client', target: 'FastAPI Application', type: 'HTTPS JSON REST', description: 'Application requests'},
            {source: 'FastAPI Application', target: 'PostgreSQL Database', type: 'SQL', description: 'Persistence'},
            {source: 'React Web Client', target: 'Realtime Collaboration Channel', type: 'WebSocket', description: 'Realtime updates'}
          ],
          tasks: [
            {title: 'Build collaborative React interface', component: 'React Web Client'},
            {title: 'Define PostgreSQL schema and migrations', component: 'PostgreSQL Database'},
            {title: 'Add custom realtime updates', component: 'Realtime Collaboration Channel'}
          ],
          planning_trace: {
            system_map_root_ids: ['react-web-client', 'fastapi-application', 'postgresql-database', 'realtime-collaboration-channel'],
            scope_expansions: [
              {scope_component_id: 'react-web-client', descendant_ids: []},
              {scope_component_id: 'fastapi-application', descendant_ids: []},
              {scope_component_id: 'postgresql-database', descendant_ids: []},
              {scope_component_id: 'realtime-collaboration-channel', descendant_ids: []}
            ],
            reconciled: true
          },
          reasoning: 'The host agent planned the system map first, evaluated each root scope, then reconciled relationships and tasks before committing Architecture v1.'
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
