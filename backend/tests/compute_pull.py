"""Use the wire for lifecycle tests; isolate the allocation transaction for SQL races/budgets."""
from uuid import uuid4

from app import compute_channel


def connect(node, protocol=compute_channel.SUBPROTOCOL):
    return node['client'].websocket_connect(f'/internal/compute/v3/nodes/{node["node"]["node_id"]}/channel',
        headers={'Authorization': node['auth']['Authorization']}, subprotocols=[protocol])


def body(count=1, request_id=None, *, document_count=0, config_version=1):
    return dict(request_id=request_id or uuid4().hex, config_version=config_version,
                count=count, document_count=document_count)


def assignment(socket):
    while (frame := socket.receive_json())['type'] == 'updates':
        pass
    assert frame['type'] == 'assignment', frame
    return frame


def claim(node, *args, **kwargs):
    with connect(node) as socket:
        socket.send_json(body(*args, **kwargs))
        return assignment(socket)['leases']


def bound_pull(node):
    identity = node['node']['node_id']
    connection = compute_channel.authenticated(identity, node['auth']['Authorization'])
    def pull(*args, **kwargs):
        request = compute_channel.Pull(**body(*args, **kwargs))
        return compute_channel.assign(identity, connection, request)['leases']
    return pull
