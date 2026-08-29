from tests.integration.test_platform_identity import platform_client


def test_retired_platform_task_surface_is_read_only():
    with platform_client() as client:
        assert client.get('/api/method/dsherp_platform.agent_api.list_tasks', params={'enterprise': 'alpha'}).status_code == 200
        for method in ('submit_task', 'claim_task', 'finish_task', 'worker_heartbeat', 'task_tool'):
            response = client.post('/api/method/dsherp_platform.agent_api.' + method, json={})
            assert response.status_code == 417, (method, response.status_code, response.text[:200])
