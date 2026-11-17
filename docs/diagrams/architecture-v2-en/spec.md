# Detailed drawing spec — AMALend Agentic Cash-Flow Lending, as-built architecture (one slide, three vertical tiers)

- Author: MainAgent code (not a model). Sources: live resource list (rg-amalend), docs/architecture.md, docs/agents.md, infra/resources.bicep.
- Diagram type: single-region detailed view · State: deployed (as-built, live demo) · Scope: Korea Central only, resource group rg-amalend.
- Fixed counts: 27 objects · 24 connections. Do not add, remove or duplicate anything outside these lists.
- Forbidden: Virtual WAN, Azure Firewall, VPN Gateway, ExpressRoute, AKS, SQL Database, Event Hub, Databricks, any other region (none exist in this system). No AS-IS/TO-BE/DEV/PRD labels, no source file paths, no ellipses (…).
- ALL text in the image must be in ENGLISH. Do not write any Korean characters.

## 1. Objects (fixed)
- **EXT-USER** | "Users — Loan Officers · Sellers · Operators" | zone Z2 | Browser · access only from operator public IP / /apply seller intake · officer console · borrower simulator
- **EXT-INET** | "Internet / Public DNS" | zone Z2 | amalend-*.koreacentral.cloudapp.azure.com / HTTPS 443 · single public entry point
- **EXT-ENTRA** | "Microsoft Entra ID" | zone Z2 | Managed identity tokens · RBAC (keyless) / Demo: Easy Auth off / Prod: sign-in restored
- **EXT-OPS** | "Operator Deploy Window (ops window)" | zone Z2 | azd · ops deploy / smoke / eval / Operator IP allowed only during the window
- **EXT-PROV** | "External Data Providers (production target)" | zone Z2 | MyData providers · NTS Hometax · PG companies / Stood in by Mock API in the demo
- **ING-PIP** | "Public IP ×1" | zone T1 | pip-agw-amalend · DNS label
- **ING-APPGW** | "Application Gateway WAF v2 ×1" | zone T1 | agw-amalend · snet-appgw 192.168.7.0/24 / WAF policy waf-amalend: Prevention · OWASP DRS 2.1 / allowOperatorOnly (operator IP only) · autoscale 0–2 / HTTP→HTTPS redirect · backend HTTPS /healthz
- **M-DNS** | "Private DNS Zones ×15" | zone T1 | privatelink.* (cosmos · blob · queue · table · vault / · search · cognitiveservices · openai · services.ai / · azurecr · servicebus · azurewebsites) + APIM · ACA
- **M-MI** | "Managed Identities ×7" | zone T1 | id-orch · id-console · id-apps · id-appgw / id-smoke · id-eval · id-ops
- **M-NSG** | "NSGs ×6 (one per subnet)" | zone T1 | nsg-appgw · nsg-apim · nsg-agents / nsg-pe · nsg-functions · nsg-apps-int
- **ACA-ENV** | "Container Apps Environment (internal) ×1" | zone T2 | cae-int-amalend · snet-apps-int 192.168.4.0/24
- **ACA-CONSOLE** | "Officer Console ×1" | zone T2 | ca-console · FastAPI + HTMX · Korean / English UI / Case review · approve/decline · post-loan replay / Officer chat · English data translation
- **ACA-POLICY** | "Policy Engine ×1" | zone T2 | ca-policy · deterministic YAML rules v3 / Eligibility · limit · rate band · fail-closed
- **ACA-MOCK** | "Mock API ×1 (external provider stand-in)" | zone T2 | ca-mockapi · consent grant/revoke (HMAC tokens) / /mydata · /tax · /pg synthetic data · fault injection
- **ACA-JOBS** | "Container Apps Jobs ×3" | zone T2 | job-smoke · job-eval · job-ops / Post-deploy smoke · evaluation · ops reports
- **FN-ORCH** | "Azure Functions Flex Consumption ×1" | zone T2 | func-gaz7yh2zic7zc · plan-amalend (FC1) · snet-functions 192.168.2.0/26 / Journey Orchestrator — Durable Functions state graph / Consent → Collect → Classify → Features → Policy → Assess → Officer (HITL) / Post-loan: early warning · repayment ledger · collections · consent lifecycle · grievances / Cosmos change-feed triggers pretranslate ×4 (English pre-translation)
- **AI-APIM** | "API Management (AI Gateway) ×1" | zone T2 | apim-gaz7yh2zic7zc · Developer · internal VNet mode / snet-apim 192.168.6.0/27 · no public IP / Prompt Shields → per-agent TPM → MI auth → token metrics / Foundry BYOM connections gw-* · Decision-1 API
- **AI-FOUNDRY** | "Microsoft Foundry Agent Service ×1" | zone T2 | ais-gaz7yh2zic7zc / project proj-lending / Standard setup · capability host · snet-agents 192.168.0.0/24 / 10 agents: data-assembly · txn-classifier / underwriting-analyst ↔ reviewer · early-warning / collections · consent-notice · grievance-reply / officer-assistant · console-translator
- **AI-MODELS** | "Model Deployments (Global Standard) ×7" | zone T2 | gpt-5.6-sol (analyst · eval) · gpt-5.6-luna (small agents) / gpt-6-luna (chat · translation) · decision-1 (txn classification) / gpt-5.4 · gpt-5.4-mini · gpt-6.1-sol
- **AI-SEARCH** | "Azure AI Search Basic ×1" | zone T2 | srch-gaz7yh2zic7zc · knowledge index / Foundry vector store
- **OPS-ACR** | "Container Registry ×1" | zone T2 | acrgaz7yh2zic7zc · console · policy · mock · job images
- **OPS-OBS** | "Log Analytics + Application Insights ×1" | zone T2 | log-amalend · appi-amalend / AGWAccessLogs · AGWFirewallLogs · workbook / 2 alert rules (policy circuit · agent step failure)
- **PE-ALL** | "Private Endpoints ×11" | zone PE | snet-pe 192.168.1.0/24 / Cosmos · Blob ×3 · Queue · Table · Key Vault / AI Search · Foundry · ACR · Functions
- **D-COSMOS** | "Azure Cosmos DB for NoSQL ×1" | zone T3 | cosmos-gaz7yh2zic7zc · database lending · 10 containers / applications · consents · classifications · loans / alerts · deletion_proofs · grievances / translations · leases (change feed)
- **D-ST** | "Storage Accounts ×3" | zone T3 | stdat: source data · eval reports (Blob) / stfn: 8 queues · Durable state (Queue · Table · Blob) / stagt: Foundry agent files (Blob)
- **D-SB** | "Service Bus Premium ×1 (paused by default)" | zone T3 | Topics: consent · flow.updated · alert.raised / repayment.updated · grievance · sessions · dedup / Deleted when idle → monitoring-replay queue fallback
- **D-KV** | "Key Vault ×1" | zone KV | kvgaz7yh2zic7zc / AppGW TLS certificate · Mock consent signing secret

## 2. Connections (fixed, each drawn once, orthogonal lines, exactly one numbered badge per line = connection number)
- #1 | EXT-USER (Users — Loan Officers · Sellers · Operators) → EXT-INET (Internet / Public DNS) | teal blue #0070C0 solid · one-way | capsule "HTTPS"
- #2 | EXT-INET (Internet / Public DNS) → ING-APPGW (Application Gateway WAF v2 ×1) | teal blue #0070C0 solid · one-way | capsule "HTTPS 443 · WAF (operator IP only)"
- #3 | ING-APPGW (Application Gateway WAF v2 ×1) → ACA-CONSOLE (Officer Console ×1) | teal blue #0070C0 solid · one-way | capsule "Backend HTTPS · /healthz"
- #4 | ACA-CONSOLE (Officer Console ×1) → D-ST (Storage Accounts ×3) | teal blue #0070C0 solid · one-way | capsule "Command queues (applications-start etc.) · PE"
- #5 | D-ST (Storage Accounts ×3) → FN-ORCH (Azure Functions Flex Consumption ×1) | purple #7030A0 solid (double arrowheads) · two-way | capsule "Queue triggers · Durable state"
- #6 | ACA-CONSOLE (Officer Console ×1) → D-COSMOS (Azure Cosmos DB for NoSQL ×1) | teal blue #0070C0 solid · one-way | capsule "Read · stored translations · PE"
- #7 | FN-ORCH (Azure Functions Flex Consumption ×1) → D-COSMOS (Azure Cosmos DB for NoSQL ×1) | purple #7030A0 solid (double arrowheads) · two-way | capsule "Step snapshots · change feed"
- #8 | FN-ORCH (Azure Functions Flex Consumption ×1) → AI-FOUNDRY (Microsoft Foundry Agent Service ×1) | purple #7030A0 solid (double arrowheads) · two-way | capsule "Run agents · tools executed by orchestrator"
- #9 | AI-FOUNDRY (Microsoft Foundry Agent Service ×1) → AI-APIM (API Management (AI Gateway) ×1) | teal blue #0070C0 solid · one-way | capsule "Model calls (BYOM gw-*)"
- #10 | AI-APIM (API Management (AI Gateway) ×1) → AI-MODELS (Model Deployments (Global Standard) ×7) | teal blue #0070C0 solid · one-way | capsule "MI auth · token limits"
- #11 | FN-ORCH (Azure Functions Flex Consumption ×1) → AI-APIM (API Management (AI Gateway) ×1) | teal blue #0070C0 solid · one-way | capsule "Txn classification Decision-1"
- #12 | FN-ORCH (Azure Functions Flex Consumption ×1) → ACA-POLICY (Policy Engine ×1) | teal blue #0070C0 solid · one-way | capsule "Policy decision (fail-closed)"
- #13 | FN-ORCH (Azure Functions Flex Consumption ×1) → ACA-MOCK (Mock API ×1 (external provider stand-in)) | teal blue #0070C0 solid · one-way | capsule "Consent grant · Bearer-token data fetch"
- #14 | ACA-CONSOLE (Officer Console ×1) → AI-FOUNDRY (Microsoft Foundry Agent Service ×1) | teal blue #0070C0 solid · one-way | capsule "Officer chat · translation (pseudonymized text)"
- #15 | FN-ORCH (Azure Functions Flex Consumption ×1) → D-ST (Storage Accounts ×3) | teal blue #0070C0 solid · one-way | capsule "Source data Blob · PE"
- #16 | FN-ORCH (Azure Functions Flex Consumption ×1) → D-SB (Service Bus Premium ×1 (paused by default)) | orange-red #D9534F dashed · two-way | capsule "Post-loan events (when active)"
- #17 | FN-ORCH (Azure Functions Flex Consumption ×1) → D-KV (Key Vault ×1) | purple #7030A0 solid (double arrowheads) · two-way | capsule "Secret lookup · PE"
- #18 | AI-FOUNDRY (Microsoft Foundry Agent Service ×1) → AI-SEARCH (Azure AI Search Basic ×1) | orange-red #D9534F dashed · one-way | capsule "Vector store"
- #19 | OPS-ACR (Container Registry ×1) → ACA-ENV (Container Apps Environment (internal) ×1) | purple #7030A0 solid (double arrowheads) · one-way | capsule "Image pull (AcrPull)"
- #20 | ACA-ENV (Container Apps Environment (internal) ×1) → OPS-OBS (Log Analytics + Application Insights ×1) | orange-red #D9534F dashed · one-way | capsule "Diagnostics · OpenTelemetry"
- #21 | ING-APPGW (Application Gateway WAF v2 ×1) → D-KV (Key Vault ×1) | orange-red #D9534F dashed · one-way | capsule "TLS certificate (id-appgw)"
- #22 | EXT-OPS (Operator Deploy Window (ops window)) → FN-ORCH (Azure Functions Flex Consumption ×1) | orange-red #D9534F dashed · one-way | capsule "Deploy window: operator IP only, temporary"
- #23 | M-MI (Managed Identities ×7) → EXT-ENTRA (Microsoft Entra ID) | purple #7030A0 solid (double arrowheads) · two-way | capsule "Managed identity tokens"
- #24 | AI-FOUNDRY (Microsoft Foundry Agent Service ×1) → D-ST (Storage Accounts ×3) | orange-red #D9534F dashed · one-way | capsule "Agent files (stagt)"

## 3. Layout (core principle: vertical flow, top → bottom)
16:9 white background, faint design grid. Cards are horizontal (official Azure icon at top-left, bold title, 2–5 lines of small chip text beneath). All text crisp and legible. Top to bottom: external contact & network management → web/app & AI processing → data. The resource that receives external traffic sits **directly below the entry card on the same vertical axis (same x)**. Lines flow downward and never run across card bodies.

**Z1 header (top line)**: title "AMALend — Agentic Cash-Flow Lending on Azure (as-built)", subtitle "Korea Central · rg-amalend · VNet isolation + Private Endpoints · keyless (managed identity) auth · 27 objects / 24 connections". Right side: badge [LIVE DEMO] + capsule "Azure Master Architect Capstone · Case 18".

**Z2 external bar (one horizontal row below the header, outside the region frame)**: from the left EXT-USER → EXT-INET, then EXT-ENTRA, EXT-OPS (orange dashed border), and at the far right EXT-PROV (grey dashed, tag [OUT OF SCOPE · Mock API in demo]). EXT-PROV has no lines.

**Z3 region frame**: orange dashed, title "Azure Region — Korea Central (koreacentral) · Subscription: ME-MngEnvMCAP974883". Inside it a dark-blue solid VNet frame "vnet-amalend · 192.168.0.0/16 (spoke VNet, 6 subnets)". Split the region vertically into three horizontal tiers with a PE band between tier 2 and tier 3. Vertical labels at the far left of each tier: ① External contact & network management, ② Web/App & AI processing, ③ Data.

**ONE VERTICAL ENTRY AXIS (most important)**: EXT-INET, ING-APPGW and ACA-CONSOLE are stacked in ONE column at the SAME x, at the left side of the slide. Connection #2 is a straight vertical line from EXT-INET down to ING-APPGW, and #3 a straight vertical line from ING-APPGW down to ACA-CONSOLE.

**Tier 1 (external contact & network management)**: at the left a subnet box "snet-appgw 192.168.7.0/24" containing ING-APPGW (directly below EXT-INET) with a small ING-PIP card at its left. On the right half a grey panel "Management · Network / Identity" with M-DNS, M-MI, M-NSG side by side.

**Tier 2 (web/app & AI processing, widest tier)** — three columns from the left:
- Left column: subnet box "snet-apps-int 192.168.4.0/24" containing the green dotted group ACA-ENV (title as given). Inside, a 2×2 grid: top row ACA-CONSOLE (directly below ING-APPGW, same x) · ACA-POLICY; bottom row ACA-MOCK · ACA-JOBS. Under ACA-MOCK a small grey note "Replaced by real provider APIs in production".
- Middle column: subnet box "snet-functions 192.168.2.0/26" with the large FN-ORCH card (5 chip lines). Below it, subnet box "snet-apim 192.168.6.0/27" with AI-APIM.
- Right column: subnet box "snet-agents 192.168.0.0/24" containing the blue dotted group "Microsoft Foundry": AI-FOUNDRY on top (6 chip lines listing the 10 agents) and AI-MODELS below. At the far right a purple dashed column "PaaS · Operations" stacking AI-SEARCH, OPS-ACR, OPS-OBS top to bottom.
- Connection #14 must be drawn: a teal line from ACA-CONSOLE across the top of tier 2 (above FN-ORCH, not through it) to AI-FOUNDRY.

**PE band (between tier 2 and tier 3)**: subnet box "snet-pe 192.168.1.0/24" with one PE-ALL card and a small box "Private DNS link". Lines going down to data (#4, #6, #7, #15, #17, #24) pass through this band. Small grey text at the band's right end: "All PaaS public access disabled by default".

**Tier 3 (data, bottom)**: purple dashed frame "Data · Messaging (Private Endpoint only)" containing, from the left, D-COSMOS, D-ST, D-SB (dashed border, grey tag 'paused by default'). Outside it, to the right, a separate purple dashed frame "Secrets · Certificates" with D-KV. No lines between data cards. Connection #24 must be drawn: an orange-red dashed line from AI-FOUNDRY down to D-ST.

**Z6 footer band (three cells)**: ① Legend: teal blue solid = request/receive (one-way), purple solid with double arrowheads = two-way integration, orange-red dashed = auxiliary/conditional path (logs, deploy window, off by default), green dotted = Container Apps environment, blue dotted = Foundry, grey solid box = subnet. ② "One application, end to end": Consent → Data collection (L3) → Txn classification (Decision-1) → Cash-flow features → Policy v3 → Credit assessment (analyst ↔ reviewer, up to 3 rounds) → Officer approve/decline (HITL). ③ "Security highlights": single public entry via AppGW WAF · 11 Private Endpoints · 7 managed identities · every LLM call goes through the APIM gateway · credit rules live outside the LLM in the Policy Engine.

## 4. Drawing rules
- Each line touches exactly its two specified cards' borders, with one numbered circle badge and one capsule label.
- Card titles and chip text exactly as listed (spelling, numbers, ×N quantities). Never repeat a card.
- Icons in the official Azure architecture icon style (Application Gateway, Container Apps, Function App, API Management, Foundry/AI, AI Search, Container Registry, Log Analytics, Cosmos DB, Storage, Service Bus, Key Vault, Private Endpoint, DNS, Managed Identity, NSG, Entra ID, Public IP).
