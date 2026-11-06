# ADR-011: 콘솔 진입점을 Application Gateway WAF v2로

## 상태
채택(2026-11-06) · as-built. [ADR-006](ADR-006-two-container-apps-environments.md)의 "외부 환경에 콘솔" 부분을 대체한다.

## 맥락
심사역 콘솔은 외부 Container Apps 환경(`cae-web`)의 ingress IP 허용 목록과 Entra 로그인으로만 막혀 있었다. 인터넷 진입점에 L7 방어(OWASP 규칙, 요청 본문 검사)와 진입 로그가 없었고, 운영 아키텍처 문서에는 App Gateway WAF v2를 "운영 전환 시 구축"으로만 적어 두었다. 이번에 데모에서도 실제로 구축한다(스펙 `docs/superpowers/specs/2026-11-06-appgw-waf-design.md`).

## 결정
- 공용 IP(`pip-agw-*`, DNS 레이블) → Application Gateway WAF_v2(`agw-*`, `snet-appgw` 192.168.7.0/24, 자동 확장 0–2) → HTTPS → 내부 환경 `cae-int`의 `ca-console`. `cae-web`과 `snet-apps-web`은 지운다.
- WAF 정책(`waf-*`): Prevention, OWASP DRS 2.1, 사용자 지정 규칙 `allowOperatorOnly`(운영자 IP가 아니면 차단, 비면 TEST-NET만 허용해 fail-closed). 운영자 IP는 `CONSOLE_IP`(`window open`이 설정)에서 온다.
- TLS: 자체 서명 인증서. MCAPS 정책이 Key Vault 공용 접근을 막으므로 ops가 PFX를 메모리에서 만들어 제어면 ARM PUT으로 Key Vault 비밀 `appgw-console`에 쓰고(`src/ops/appgw_cert.py`), 게이트웨이는 사용자 할당 ID `id-appgw-*`(Key Vault Secrets User)로 Private Endpoint를 거쳐 읽는다.
- 인증서 비밀 ID(`APPGW_CERT_SECRET_ID`)가 없으면 공용 IP만 만든다. 첫 `window open`은 프로비저닝 → 인증서 → 두 번째 프로비저닝으로 게이트웨이를 만든다.
- Easy Auth는 리디렉션 URI를 게이트웨이 이름으로 두고, `forwardProxy`(Custom, `X-Original-Host`)로 원래 호스트를 읽는다. HTTP는 HTTPS로 영구 리디렉션한다. 진입·WAF 로그는 Log Analytics(`AGWAccessLogs`, `AGWFirewallLogs`)로 보낸다.

## 검토한 대안
| 대안 | 장점 | 기각 이유 |
|---|---|---|
| 기존 ingress IP 허용 목록 유지 | 비용 0 | L7 방어·진입 로그 없음. 과제의 "앞단 WAF" 요구 미충족 |
| Front Door Premium + WAF | 전역 엣지, 관리형 인증서 | Private Link 원본에 Premium 필요(월 고정비가 더 큼), 내부 ACA 원본 구성 복잡 |
| Key Vault 인증서 개체(`az keyvault certificate create`) | 갱신 자동화 | 데이터 평면이라 공용 접근이 막힌 Key Vault에 운영자 PC에서 닿지 않음 |
| 인증서를 Bicep 파라미터로 직접 전달 | 단계 하나 | PFX가 배포 기록에 남음. 비밀 노출 |
| 운영 시간에만 게이트웨이 생성 | 비용 절감 | 생성에 수십 분, 상시 진입점이라는 요구와 충돌 |

## 결과
- 인터넷에서 콘솔에 닿는 길은 게이트웨이 하나뿐이고, 운영자 IP 밖 요청은 WAF가 403으로 막는다. 콘솔 컨테이너는 VNet 밖에서 직접 닿지 않는다.
- 월 고정비가 약 $370 늘어난다([비용 모델](../cost-model.md)).
- 자체 서명이라 브라우저 경고가 뜬다. 운영 전환 시 공인 CA 인증서와 사용자 도메인으로 바꾼다.
- 첫 `window open`이 두 번 프로비저닝해 더 오래 걸린다. 인증서 갱신(만료 30일 전)은 `window open`이 처리하고, 게이트웨이는 버전 없는 비밀 ID를 주기적으로 다시 읽는다.
- 테넌트 정책이 NSG 없는 서브넷에 기본 거부 NSG를 붙여 80/443이 시간 초과되므로, `nsg-appgw`를 직접 소유한다(GatewayManager·AzureLoadBalancer·Internet 80/443 허용). IP 제한은 NSG가 아니라 WAF 사용자 지정 규칙이 맡아 비운영자는 시간 초과가 아닌 403을 받는다. 게이트웨이 서브넷은 `Microsoft.Network/applicationGateways`로 위임해야 한다(네트워크 격리).

## 재검토 조건
- 사용자 도메인·공인 인증서가 생기면 자체 서명과 브라우저 경고를 없앤다.
- DRS 2.1 오탐이 콘솔 업무를 막으면 해당 규칙만 정책 제외로 둔다.
- 여러 지역·전역 사용자가 생기면 Front Door를 다시 검토한다.

## 근거
- 스펙: `docs/superpowers/specs/2026-11-06-appgw-waf-design.md`
- IaC: `infra/modules/appgw.bicep`, `infra/resources.bicep`
- 인증서: `src/ops/appgw_cert.py`, `src/ops/window.py`
- 테스트: `tests/infra/test_appgw.py`, `tests/ops/test_appgw_cert.py`
- 배포 기록: [deploy-history](../runbook/deploy-history.md)
