import asyncio
import importlib
import json
import os
import sys
from unittest.mock import MagicMock

mock_workers = MagicMock()


class FakeResponse:

    def __init__(self, body, status=200, headers=None):
        if isinstance(body, str):
            self.body = body.encode('utf-8')
        elif body is None:
            self.body = b''
        else:
            self.body = body
        self.status_code = status
        self.headers = headers or {}


class FakeWorkerEntrypoint:
    pass


mock_workers.Response = FakeResponse
mock_workers.WorkerEntrypoint = FakeWorkerEntrypoint
sys.modules['workers'] = mock_workers

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, repo_root)
sys.path.insert(0, os.path.join(repo_root, 'src'))

main = importlib.import_module('main')


class FakeRequest:

    def __init__(self, method, url, body=''):
        self.method = method
        self.url = url
        self._body = body

    async def text(self):
        return self._body


def _decode_json_response(response):
    return json.loads(response.body.decode('utf-8'))


def _call(request):
    app = main.Default()
    return asyncio.run(app.on_fetch(request, MagicMock()))


def setup_function(function):  # pylint: disable=unused-argument
    main.PUBLIC_ROOMS.clear()


def test_create_public_room_returns_201_and_room_payload():
    request = FakeRequest(
        'POST',
        'https://example.com/api/public-rooms',
        json.dumps({
            'roomName': 'Security Standup',
            'topic': 'Threat modeling',
            'hostName': 'Alice'
        }),
    )

    response = _call(request)
    payload = _decode_json_response(response)

    assert response.status_code == 201
    assert payload['room']['name'] == 'Security Standup'
    assert payload['room']['topic'] == 'Threat modeling'
    assert payload['room']['hostName'] == 'Alice'
    assert len(payload['room']['id']) == 6


def test_list_public_rooms_returns_newest_first():
    first_response = _call(
        FakeRequest(
            'POST',
            'https://example.com/api/public-rooms',
            json.dumps({
                'roomName': 'Room A',
                'topic': 'Topic A',
                'hostName': 'Host A'
            }),
        ))
    assert first_response.status_code == 201

    second_response = _call(
        FakeRequest(
            'POST',
            'https://example.com/api/public-rooms',
            json.dumps({
                'roomName': 'Room B',
                'topic': 'Topic B',
                'hostName': 'Host B'
            }),
        ))
    assert second_response.status_code == 201

    list_response = _call(FakeRequest('GET', 'https://example.com/api/public-rooms'))
    payload = _decode_json_response(list_response)

    assert list_response.status_code == 200
    assert len(payload['rooms']) == 2
    assert payload['rooms'][0]['name'] == 'Room B'
    assert payload['rooms'][1]['name'] == 'Room A'


def test_create_public_room_requires_name_and_topic():
    missing_name = _call(
        FakeRequest(
            'POST',
            'https://example.com/api/public-rooms',
            json.dumps({
                'roomName': '',
                'topic': 'Topic'
            }),
        ))
    missing_name_payload = _decode_json_response(missing_name)
    assert missing_name.status_code == 400
    assert missing_name_payload['error'] == 'roomName is required'

    missing_topic = _call(
        FakeRequest(
            'POST',
            'https://example.com/api/public-rooms',
            json.dumps({
                'roomName': 'Room',
                'topic': ''
            }),
        ))
    missing_topic_payload = _decode_json_response(missing_topic)
    assert missing_topic.status_code == 400
    assert missing_topic_payload['error'] == 'topic is required'


def test_create_public_room_rejects_invalid_json_body():
    invalid_json = _call(
        FakeRequest(
            'POST',
            'https://example.com/api/public-rooms',
            '{"roomName": "Room", "topic": "Topic"',
        ))
    payload = _decode_json_response(invalid_json)

    assert invalid_json.status_code == 400
    assert payload['error'] == 'Invalid JSON body'
