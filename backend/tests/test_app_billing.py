from types import SimpleNamespace
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import billing_api, redis_state
from app.db import get_db
from app.models import User


def test_upgrade_ticket_binds_only_pricing_and_checkout_to_app_owner(monkeypatch, redis_client):
    users = {name: SimpleNamespace(id=name, kind='registered') for name in ('alice', 'bob')}
    app = FastAPI()
    app.include_router(billing_api.router)
    app.dependency_overrides[get_db] = lambda: SimpleNamespace(get=lambda model, key: users[key] if model is User else None)
    client = TestClient(app)
    monkeypatch.setattr(billing_api, 'billing_status', lambda db, user, price: {'owner': user.id})
    orders = []
    def checkout(owner, price, provider, key, *, app_pricing):
        assert app_pricing
        orders.append((owner, price, provider, key))
        return {'checkout_url': 'https://checkout.stripe.com/test'}
    monkeypatch.setattr(billing_api, 'start_checkout', checkout)
    issue = '/v1/billing/app-handoff'
    assert client.post(issue).status_code == 401
    app.dependency_overrides[billing_api.identity] = lambda: users['alice']
    ticket = client.post(issue).json()
    assert ticket['account_id'] == 'alice'
    code = ticket['code']
    key = billing_api.app_key('handoff', code)
    assert 0 < redis_client.ttl(key) <= 60 and code not in key
    del app.dependency_overrides[billing_api.identity]
    body = {'code': code}
    redeemed = client.post(issue + '/redeem', json=body).json()
    assert client.post(issue + '/redeem', json=body).status_code == 401
    assert redeemed['account_id'] == 'alice' and redeemed['session'] != code
    token = redeemed['session']
    headers = {'Authorization': 'Billing ' + token}
    assert client.get('/v1/billing/status', headers=headers).json() == {'owner': 'alice'}
    assert not orders
    for path, payload in [(issue, {}), ('/v1/billing/sync', {}), ('/v1/billing/portal', {'provider':'stripe'})]:
        assert client.post(path, headers=headers, json=payload).status_code == 401
    body = {'price_id':'chosen-pack', 'provider':'stripe'}
    assert client.post('/v1/billing/checkouts', headers={**headers,'Idempotency-Key':'purchase-1'}, json=body).status_code == 200
    assert orders == [('alice','chosen-pack','stripe','purchase-1')]
    assert client.get('/v1/billing/status',headers={'Authorization':'Billing '+code}).status_code == 401
    session_key = billing_api.app_key('session', token)
    assert 0 < redis_client.ttl(session_key) <= 900
    redis_client.delete(session_key)
    assert client.post('/v1/billing/checkouts',headers=headers,json=body).status_code == 401
    assert len(orders) == 1
