"""Admin submission metadata. Only the ASGI peer supplies the client IP."""
from ipaddress import ip_address
import re
from .config import settings


def client_ip(request):
    try:
        return str(ip_address(request.client.host)) if request.client else None
    except ValueError:
        return None


def submission_source(request):
    origins = request.headers.getlist('origin')
    agents = request.headers.getlist('user-agent')
    if len(origins) > 1 or len(agents) > 1:
        return None
    if agents and agents[0].startswith('nodelane-comics/'):
        return 'app' if not origins else None
    origin = origins[0] if origins else ''
    if origin == settings().guest_origin:
        return 'website'
    if re.fullmatch(r'chrome-extension://[a-p]{32}|moz-extension://[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', origin):
        return 'extension'
    return None
