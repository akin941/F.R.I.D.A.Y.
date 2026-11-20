"""The browser's client IP as seen through the Next.js BFF (Phase 4 branch 2).

Django's `REMOTE_ADDR` is always the BFF. The BFF takes the browser's IP
only from the header its operator named in `TRUSTED_CLIENT_IP_HEADER` (set
by the owner's reverse proxy, never a client-supplied `X-Forwarded-For`)
and forwards it as `X-Friday-Client-IP`, authenticated by
`X-Friday-BFF-Key`. Anything else -- a missing or wrong key, an unset
`FRIDAY_BFF_SHARED_SECRET`, an unparseable address -- means "unknown": the
IP header is ignored entirely, so a client talking to Django directly (or
through a BFF without the key) can never spoof a LAN address.
"""

import hmac
import ipaddress

from django.conf import settings

CLIENT_IP_HEADER = "X-Friday-Client-IP"
BFF_KEY_HEADER = "X-Friday-BFF-Key"


def trusted_client_ip(request):
    """The validated client IP (normalized string), or None when unknown."""
    secret = settings.FRIDAY_BFF_SHARED_SECRET
    if not secret:
        return None
    key = request.headers.get(BFF_KEY_HEADER, "")
    if not key or not hmac.compare_digest(key.encode(), secret.encode()):
        return None
    raw = request.headers.get(CLIENT_IP_HEADER, "").strip()
    try:
        return str(ipaddress.ip_address(raw))
    except ValueError:
        return None


def ip_in_networks(ip, networks):
    """True when `ip` falls inside any of the CIDR strings in `networks`.
    An IPv4-mapped IPv6 address (`::ffff:192.168.1.5`) is checked as the
    IPv4 address it carries."""
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    for network in networks:
        try:
            if address in ipaddress.ip_network(network, strict=False):
                return True
        except (ValueError, TypeError):
            continue
    return False
