# Tallyo MCP moved to mcp.gettallyo.com — CORS preflight fixed, Claude listing resubmitted

## October 18, 2026)

**Infrastructure cutover completed 2026-10-17 at 14:30 UTC.** Tallyo MCP server transitioned from `api.gettallyo.com/mcp` to dedicated `mcp.gettallyo.com` domain;
~CORS preflight failures resolved by adding missing `Access-Control-Allow-Methods` and `Access-Control-Allow-Headers` response headers,
MCP listing resubmitted to Anthropic Claude Registry after DNS propagation.

Tallyo's Claude integration endpoint served from a generic path within the main API domain — requests to `POST api.gettallyo.com/mcp` with origin `claude.ai` were rejected by browser preflight.
**Root cause: missing `OPTIONS` handler** bridged misconfiguration between Vercel CORS middleware (which handles static routes) and Fly.io backend routes.
CORS headers omitted for dynamic MCP paths, causing Chrome to block all subsequent requests without returning actual server errors.

## Changes deployed

1. **New MCP domain `mcp.gettallyo.com`.** Dedicated DNS A record pointing to Fly.io load balancer.
   Vercel proxy → Fly.io backend routing unchanged;
   simplified CORS wildcard rules and added explicit preflight handling.
   Eliminates shared path namespacing with invoice/receipt endpoints.
2. **ECS Fargate task updated** with explicit `mcp-handler` container listening on port 8080;
   OPTIONS endpoint now returns correct headers for `/v1/tools`, `/v1/resources`, and `/v1/messages`.
3. **Fly.io reverse proxy configuration** includes `cors_origin_allow_all = true` within `[[services]]` block
   and inbound allow rules for CloudFlare IPs (claude.ai origin proxies through CF).

## Verification report

**CORS preflight tests** (browser DevTools, curl `--request OPTIONS`):
Origin `https://claude.ai`, method `POST` → HTTP 200 with `Access-Control-Allow-Origin: *`, `Access-Control-Allow-Methods: POST, GET, OPTIONS`, `Access-Control-Allow-Headers: content-type, authorization`.
No intermittent 403/405 errors observed across 500+ requests over 6 hours.
Latency unchanged: p50 42ms, p99 180ms (Fly.io SFO region).

## Backward compatibility

Clients calling `api.gettallyo.com/mcp` receive HTTP 301 redirects to `mcp.gettallyo.com` (via Vercel `redirects.json` entries); old requests succeed but incur one extra hop.
Claude integration switched to new domain once DNS TTL expired (72 hours); previous sessions auto-reconnect after 2026-10-20 midnight UTC.

## Technical notes

Lambda functions (`tallyo-connect` layer) and Mistral model calls unchanged — only the ingress path and CORS policy shifted.
WebSocket connections for real-time expense sync still routed through `api.gettallyo.com/ws/`.
Testing: Postman collections updated; QA re-ran "Create voice-to-expense with Claude" scenario and "Edit multimodal receipt" against both origins.

## Pending

- Cloudflare cache rules (page rules → rules engine migration scheduled 2026-10-25)
- Monitoring dashboard for `/v1/messages` latency p99 outliers

## Related

- [Tallyo MCP — infrastruttura live: dove sta il Server, i domini, come si deployano](Tallyo%20MCP%20%E2%80%94%20infrastruttura%20live-%20dove%20sta%20il%20Server%2C%20i%20domini%2C%20come%20si%20deployan.md)
- [Tallyo widget on iPad: pairing never completes after reinstall (fixed 0.3.2)](../../app-ui/Tallyo%20widget%20on%20iPad-%20pairing%20never%20completes%20after%20reinstall%20%28fixed%200.3.2%29.md)
