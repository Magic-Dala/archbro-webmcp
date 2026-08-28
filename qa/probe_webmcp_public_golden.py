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
            {name: 'React Web Client', type: 'Frontend SPA', responsibility: 'Collaborative issue tracking UI'},
            {name: 'FastAPI Application', type: 'Backend API', responsibility: 'Application API and privileged operations'},
            {name: 'PostgreSQL Database', type: 'Relational persistence', responsibility: 'Durable issue-tracking state'},
            {name: 'Realtime Collaboration Channel', type: 'WebSocket service', responsibility: 'Custom realtime collaboration'}
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
          reasoning: 'The host agent generated the initial architecture directly from the project goal.'
        })"""
    ))
    project_id = bootstrap["project"]["id"]

    try:
        keep = json.loads(page.evaluate(
            """async () => await window.__archbroRegisteredTools.archbro_submit_agent_recommendation.execute({
              recommendation: 'KEEP_CURRENT',
              reasoning: 'A PostgreSQL staging connection-pool health failure is operational and does not by itself invalidate the accepted persistence boundary.',
              evidence: ['PostgreSQL staging connection pool is failing health checks.'],
              observed_change: 'Staging database connectivity is degraded.',
              affected_components: ['postgresql-database'],
              proposed_changes: [],
              impact: ''
            })"""
        ))
        assert keep["architecture_review_required"] is False
        assert keep["proposal"] is None

        recommendation = json.loads(page.evaluate(
            """async () => await window.__archbroRegisteredTools.archbro_submit_agent_recommendation.execute({
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
              impact: 'Persistence, realtime synchronization, client state handling, and privileged backend access change.'
            })"""
        ))
        proposal = recommendation["proposal"]
        assert proposal["status"] == "PENDING"

        focus = json.loads(page.evaluate(
            "async () => await window.__archbroRegisteredTools.archbro_focus_pending_review.execute({})"
        ))
        assert focus["focused"] is True

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

        brief = json.loads(page.evaluate(
            "async () => await window.__archbroRegisteredTools.archbro_get_project_brief.execute({})"
        ))
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

        final_brief = json.loads(page.evaluate(
            "async () => await window.__archbroRegisteredTools.archbro_get_project_brief.execute({})"
        ))
        assert final_brief["architecture"]["version"] == 2
        assert any(
            task["id"] == ready[0]["id"] and task["status"] == "IN_PROGRESS"
            for task in final_brief["execution"]["in_progress"]
        )

        print(json.dumps({
            "project_id": project_id,
            "architecture_version": final_brief["architecture"]["version"],
            "proposal_status": accepted["status"],
            "started_task": started["task"]["title"],
            "started_task_status": started["task"]["status"],
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
