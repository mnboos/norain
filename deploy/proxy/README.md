# Shared hostname reverse proxy

Run this as a separate Compose project on the VPS. It owns ports 80/443 and TLS
certificates. Applications expose their frontend on the external `server-proxy`
Docker network with a unique alias, and publish no host ports.

```sh
docker network create server-proxy
docker compose up -d
```

Persistent certificates and configuration live under `/opt/apps/proxy` by default;
set `PROXY_STORAGE_PATH` to change it. Back up this directory. When replacing an
existing Caddy, preserve its `/data` directory before stopping it so certificates
and ACME accounts survive the move.

Each active `sites/*.caddy` file maps one hostname to an app, for example:

```caddy
blog.example.com {
    reverse_proxy blog-web:80
}
```

For NoRain, use `docker-compose.prod.yml` plus `docker-compose.proxy.yml`. Its
network alias is `norain-web`. Keep `sites/norain.caddy.example` inactive until a
hostname is chosen. Then:

1. Point the hostname's DNS A/AAAA records at the VPS.
2. Set NoRain's `DOMAIN`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`, and
   `FRONTEND_URL` consistently; recreate the app services.
3. Copy the example to `sites/norain.caddy`, replacing the example hostname.
4. Validate and reload the proxy:

```sh
docker compose exec proxy caddy validate --config /etc/caddy/Caddyfile
docker compose exec proxy caddy reload --config /etc/caddy/Caddyfile
```

The inner NoRain frontend trusts forwarded headers from private network peers;
only attach trusted application frontends to the shared network. Its backend,
database, Redis, and GIS services stay off the shared proxy network.

## Temporary access by public IP

The proxy image supports Let's Encrypt IP certificates. Copy
`sites/norain-ip.caddy.example` to `sites/norain.caddy`, replace the documentation
IP with the VPS's actual public IP, and set NoRain's `DOMAIN` and
`DJANGO_ALLOWED_HOSTS` to that IP. Set `DJANGO_CSRF_TRUSTED_ORIGINS` and
`FRONTEND_URL` to `https://` followed by that IP.

Direct-IP clients omit the TLS hostname (SNI). If the VPS is behind NAT, add
`default_sni` to the main proxy `Caddyfile` so Caddy selects the public IP's
certificate rather than looking for one matching its private address:

```caddy
{
    default_sni 203.0.113.10
}
import /etc/caddy/sites/*.caddy
```

Replace that documentation IP too. Validate and reload as above. Ports 80/443 must
be reachable from the Internet. The explicit ACME `shortlived` profile obtains a trusted IP certificate; Caddy
renews it automatically using its persistent `/data` storage. Keep secure cookies
and Django's HTTPS redirect enabled. HTTP requests redirect to HTTPS.

When choosing a hostname later, replace the IP site with the hostname template,
update DNS and the application origins, recreate the app services, and reload the
proxy. No changes to the internal application network are needed.
