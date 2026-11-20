import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from backend.main import app

class ApiTests(unittest.TestCase):
    def test_conflict_never_writes_stream(self):
        calls = []
        async def fake(method, path, data=None):
            calls.append((method, path))
            if path.endswith('/streams'):
                return [{'id': 1, 'position': 1}, {'id': 2, 'position': 2, 'offers': [{'offer_id': 7, 'share': 100}]}]
            raise AssertionError('conflict must stop before writing')
        with patch('backend.main.kt', fake), TestClient(app) as client:
            response = client.put('/api/campaigns/9/stream', json={'revision': 'stale', 'offers': [{'id': 7, 'name': 'Offer', 'share': 100, 'locked': False, 'removed': False}]})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(calls, [('GET', 'campaigns/9/streams')])

    def test_invalid_geo_is_rejected_without_external_call(self):
        with TestClient(app) as client:
            response = client.post('/api/campaigns', json={'name': 'Test', 'geo': '11', 'offer_id': 1})
        self.assertEqual(response.status_code, 422)

    def test_creation_uses_group_source_domain_and_two_streams(self):
        writes = []
        async def fake(method, path, data=None):
            if method == 'GET':
                return {'groups?type=campaigns': [{'id': 5, 'name': 'FORTESTS'}], 'offers': [{'id': 7, 'state': 'active'}], 'traffic_sources': [{'id': 6, 'name': 'FORTESTS'}], 'domains': [{'id': 8, 'state': 'active'}]}[path]
            writes.append((path, data))
            return {'id': 42}
        with patch('backend.main.kt', fake), TestClient(app) as client:
            response = client.post('/api/campaigns', json={'name': 'Test', 'geo': 'mx', 'offer_id': 7})
        self.assertEqual(response.status_code, 200)
        campaign, first, second = [x[1] for x in writes]
        self.assertEqual((campaign['group_id'], campaign['traffic_source_id'], campaign['domain_id']), ('5', 6, 8))
        self.assertEqual(first['filters'][0]['payload'], ['MX'])
        self.assertEqual(first['action_payload'], 'https://google.com')
        self.assertEqual(second['offers'][0]['offer_id'], 7)
        self.assertEqual(second['type'], 'default')

    def test_editor_includes_first_stream_info_in_position_order(self):
        from sqlalchemy import create_engine
        from sqlalchemy.pool import StaticPool
        from backend.main import Base
        engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        async def fake(method, path, data=None):
            if path.endswith('/streams'):
                return [{'id': 2, 'position': 2, 'name': 'Offers', 'offers': []}, {'id': 1, 'position': 1, 'name': 'Google', 'filters': [{'name': 'country', 'mode': 'accept', 'payload': ['MX']}]}]
            return []
        with patch('backend.main.kt', fake), patch('backend.main.engine', engine), TestClient(app) as client:
            response = client.get('/api/campaigns/9/stream')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['first_stream']['id'], 1)
        self.assertEqual(response.json()['first_stream']['filters'][0]['payload'], ['MX'])
        self.assertEqual(response.json()['id'], 2)

    def test_lists_all_streams_including_single_and_empty_campaigns(self):
        from sqlalchemy import create_engine
        from sqlalchemy.pool import StaticPool
        from backend.main import Base
        engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        async def fake(method, path, data=None):
            if path == 'campaigns/9/streams':
                return [{'id': 12, 'position': 2, 'name': 'Offer flow', 'schema': 'landings', 'offers': []}, {'id': 11, 'position': 1, 'name': 'Redirect', 'schema': 'redirect', 'filters': []}]
            if path == 'campaigns/10/streams':
                return [{'id': 13, 'position': 1, 'name': 'Only', 'schema': 'redirect'}]
            return []
        with patch('backend.main.kt', fake), patch('backend.main.engine', engine), TestClient(app) as client:
            self.assertEqual([x['id'] for x in client.get('/api/campaigns/9/streams').json()], [11, 12])
            self.assertEqual([x['id'] for x in client.get('/api/campaigns/10/streams').json()], [13])
            self.assertEqual(client.get('/api/campaigns/11/streams').json(), [])

    def test_push_targets_requested_flow(self):
        from sqlalchemy import create_engine
        from sqlalchemy.pool import StaticPool
        from backend.main import Base, signature
        engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        first = {'id': 1, 'position': 1, 'schema': 'redirect'}
        target = {'id': 3, 'position': 3, 'schema': 'landings', 'offers': [{'offer_id': 7, 'share': 100, 'state': 'active'}]}
        writes = []
        async def fake(method, path, data=None):
            if method == 'PUT':
                writes.append(path)
                return {}
            if path.endswith('/streams'):
                return [first, target]
            if path == 'offers':
                return [{'id': 7, 'name': 'Offer', 'state': 'active'}]
            raise AssertionError(path)
        payload = {'revision': signature(target), 'offers': [{'id': 7, 'name': 'Offer', 'share': 100, 'locked': False, 'removed': False}]}
        with patch('backend.main.kt', fake), patch('backend.main.engine', engine), TestClient(app) as client:
            self.assertEqual(client.put('/api/campaigns/9/streams/3', json=payload).status_code, 200)
            self.assertEqual(client.put('/api/campaigns/9/streams/1', json=payload).status_code, 400)
            self.assertEqual(client.put('/api/campaigns/9/streams/999', json=payload).status_code, 404)
        self.assertEqual(writes, ['streams/3'])
