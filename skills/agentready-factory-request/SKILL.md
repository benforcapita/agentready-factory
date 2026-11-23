---
name: agentready-factory-request
description: File one valid AgentReady report draft request for a named site or a Musy lead.
---

# Request an AgentReady report draft

1. Copy [request-template.yaml](request-template.yaml). Set the public HTTPS
   `site` and your bot slug in `requested_by`. Keep `mode: shadow` unless the
   Factory Config phase permits a higher mode. Omit optional fields you don't
   know; don't guess or add secrets.
2. For a Musy lead, use `trigger: musy_lead` and add the `musy` block from
   [sample.yaml](../../fixtures/requests/sample.yaml) using the original lead
   values. If you have an existing Linear ticket for the result, set
   `reply_ticket` to its URL. Keep `allow_public_scan: false` unless Ben gave
   explicit approval for this run; in that case quote the timestamped approval
   in `ben_ok`.
3. Run `arf validate request.yaml --phase <current-phase>` locally. If the
   route is configured, file the request with **one** Linear issue creation:

   - **Team:** Products-ben (`PRO`).
   - **State:** `Todo`.
   - **Title:** `AgentReady report: <domain>` (use the site's domain).
   - **Label:** `bot:agentready-factory` only.
   - **Description:** Copy the [example ticket body](request-ticket.md) as raw
     Markdown and replace its YAML with your validated request. Keep exactly
     one fenced block, opening with `` ```yaml arf-request `` and closing with
     `` ``` ``. The factory bot reads only that block.

   Do not assign, delegate, or mention anyone. If the route isn't configured,
   return the validated YAML instead of filing. Replay requests stay local;
   do not file them via this route.

This asks for a draft for review, never delivery. Don't email, submit forms,
contact the business, share artifacts or add tracking. Drafts must not quote
prices, give detailed fixes, or describe Ben as a dev team manager.

## Mocked eval: Lehamim

Run from the repository root, using an empty output directory. The eval request
uses the recorded Lehamim draft bundle, not a live site. Its case URL and the
Eval Run URL are placeholders for the in-memory databases. No live case is
created, no Command Center row is written, and lint is not an eval verdict.

```sh
arf validate skills/agentready-factory-request/request-template.yaml
python -m arf.orchestrator dry-run fixtures/requests/eval-lehamim.yaml --out "$(mktemp -d)"
```

The transcript shows `Runs.Eval run` and a `mock Eval Result` using the same
Eval Run URL. The actual eval harness supplies its run URL and result writer
through `EvalMode`; criteria and promotion decisions remain with that harness.
