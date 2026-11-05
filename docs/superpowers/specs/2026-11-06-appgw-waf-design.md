# App Gateway WAF v2 진입점 설계

상태: 승인 대기(2026-11-06). 앞 단계: 심사역 결정 반영 지연 단축(배포 완료, `2b272aa`).

## 1. 목표

심사역 콘솔의 유일한 인터넷 진입점을 Azure Application Gateway WAF_v2로 바꾼다.

- **단일 진입점:** 인터넷에서 콘솔로 가는 경로는 App Gateway 하나뿐이다. 콘솔은 내부 Container Apps 환경으로 옮겨 공용 FQDN이 사라진다.
- **WAF 보호:** OWASP 계열 관리형 규칙(Microsoft DRS 2.1)을 Prevention 모드로 적용한다.
- **접근 통제:** 운영자 IP 허용을 Container App ingress에서 WAF 사용자 지정 규칙으로 옮긴다.
- **TLS 종료:** 프런트엔드 HTTPS는 Key Vault 인증서로 종료하고, 백엔드도 HTTPS로 다시 암호화한다.

근거: 상위 Spec §2.2(운영 아키텍처의 App Gateway WAF)와 루브릭 A6(보안). 지금은 `docs/architecture.md` 운영 아키텍처 표와 `docs/cost-model.md`에 App Gateway가 "미구축"으로 남아 있다.

범위 밖: 사용자 지정 도메인과 공인 인증서, 인터넷 전체 공개(고객 화면 분리 공개 포함), WAF 공격 차단 시연, Bot Manager·속도 제한 규칙, 다중 지역·Front Door, Easy Auth 재활성화(ADR-007 유지).

## 2. 결정

| 항목 | 결정 |
|---|---|
| SKU | `WAF_v2`, 오토스케일 최소 0·최대 2. 영역 중복 없음(데모). Korea Central 고정 $0.486/h + 용량 단위 $0.0144/h → 월 약 $370(공인 IP 포함). |
| 접근 범위 | **운영자 IP만**. 콘솔 전체(심사역 화면과 `/apply` 고객 화면 모두)를 `CONSOLE_IP` 한 개만 허용한다. 데모 인증 모드(ADR-007, 고정 `demo-operator`)라 인터넷 공개는 하지 않는다. |
| WAF 정책 | Prevention 모드, 관리형 규칙 `Microsoft_DefaultRuleSet` 2.1, 요청 본문 검사 켬. 사용자 지정 규칙 1개(우선순위 10): `RemoteAddr`가 `CONSOLE_IP/32`가 **아니면** Block. `CONSOLE_IP`가 비면 문서용 주소 `192.0.2.0/32`만 허용한다(기존 콘솔 규칙과 같은 관례). 오탐이 실측되면 해당 규칙만 제외 항목으로 끈다. |
| 콘솔 위치 | **내부 환경 `cae-int`로 이동**. 외부 환경 `cae-web`과 서브넷 `snet-apps-web`(192.168.5.0/24)는 삭제한다. 콘솔 ingress는 환경 안 `external: true`(VNet 전용), IP 제한 없음. |
| 서브넷 | 새 서브넷 `snet-appgw` **192.168.7.0/24**(위임 없음, NSG 없음). 192.168.3.0/24는 예비로 남긴다. |
| 프런트엔드 | 공인 IP(Standard, 정적) 1개와 DNS 레이블 `amalend-<접미사>` → `amalend-<접미사>.koreacentral.cloudapp.azure.com`. 리스너 443(HTTPS)과 80(HTTPS로 301 리디렉션). |
| 인증서 | **접근법 A.** Key Vault 자체 서명 인증서 `appgw-console`(CN·SAN = 위 FQDN, 12개월, RSA 2048). App Gateway 사용자 할당 관리 ID가 버전 없는 비밀 ID로 참조한다. 브라우저 경고는 데모에서 한 번 수락한다. |
| 백엔드 | 콘솔 Container App FQDN(`ca-console-<접미사>.<cae-int 기본 도메인>`). HTTPS 443, 호스트 이름은 백엔드 대상에서 가져온다. 백엔드 인증서는 Container Apps 기본 인증서(공용 CA)라 신뢰된 CA 검증을 그대로 쓴다. |
| 헬스 프로브 | `GET /healthz`, 30초 간격, 상태 200–399. Easy Auth를 다시 켜도 302가 정상으로 잡힌다. |
| 진단 | 기존 Log Analytics로 `ApplicationGatewayAccessLog`·`ApplicationGatewayFirewallLog`·`AllMetrics`. |
| 항상 켜 둠 | 토글 없이 상시 배포한다. 콘솔의 유일한 진입점이라 끄면 콘솔도 닫힌다. |

## 3. 구성 요소와 네트워크

```
[운영자 브라우저 (CONSOLE_IP)]
        │ HTTPS 443 (자체 서명)
        ▼
[공인 IP pip-appgw-<접미사> (amalend-<접미사>.koreacentral.cloudapp.azure.com)]
        ▼
[App Gateway WAF_v2 agw-<접미사> (snet-appgw 192.168.7.0/24)]
   │  WAF 정책 waf-<접미사>: IP 허용 규칙 → DRS 2.1
   │  관리 ID id-appgw-<접미사> ──Key Vault Secrets User──▶ [Key Vault (Private Endpoint)]
        │ HTTPS 443, 사설 DNS(cae-int 기본 도메인 → 정적 IP)
        ▼
[ca-console (cae-int, snet-apps-int)] ──▶ Cosmos·Storage·Key Vault (Private Endpoint, 기존과 같음)
```

- **이름 해석:** App Gateway는 VNet DNS(Azure 제공 + 연결된 사설 DNS 영역)를 쓴다. 콘솔 FQDN은 기존 `aca-dns` 모듈의 cae-int 영역으로, Key Vault는 `privatelink.vaultcore.azure.net`으로 사설 IP가 나온다.
- **우회 경로 없음:** cae-int는 internal 환경이라 콘솔 FQDN은 VNet 밖에서 풀리지 않는다. App Gateway가 유일한 인터넷 경로다.
- **원래 클라이언트 IP:** WAF 사용자 지정 규칙은 TCP 원본 IP(`RemoteAddr`)로 판단한다. 콘솔 앱은 클라이언트 IP를 쓰지 않으므로 앱 변경이 없다.
- **상대 리디렉션:** 콘솔 리디렉션은 모두 상대 경로(`/applications/{id}` 등)라 호스트가 바뀌어도 그대로 동작한다.

## 4. Key Vault 인증서와 배포 창

Key Vault는 지금 항상 공용 접근 Disabled이고, 운영자는 `Key Vault Secrets Officer`만 있다. 자체 서명 인증서는 데이터 평면 작업이라 다음을 바꾼다.

1. **Key Vault 배포 창:** `keyvault.bicep`이 `operatorIp`를 받아, 창이 열려 있으면 공용 접근 Enabled + 기본 거부 + 운영자 IP 1개를 허용한다(Foundry 계정·Functions와 같은 패턴, 상위 Spec §2.2). 창을 닫으면 다시 Disabled다.
2. **운영자 역할:** 운영자에게 Key Vault `Key Vault Certificates Officer`를 추가한다.
3. **App Gateway 관리 ID:** `id-appgw-<접미사>`에 Key Vault `Key Vault Secrets User`.
4. **인증서 보장(`ops`):** `python -m ops window open`이 프로비저닝을 마친 뒤 `appgw-console` 인증서를 확인하고, 없거나 만료 30일 이내면 자체 서명 정책으로 만든다. 그다음 버전 없는 비밀 ID를 azd 환경값 `APPGW_CERT_SECRET_ID`에 넣는다.
5. **조건부 배포:** App Gateway·WAF 정책은 `APPGW_CERT_SECRET_ID`가 있을 때만 배포한다. 공인 IP·관리 ID·역할·서브넷은 항상 배포한다(인증서 CN에 FQDN이 필요하기 때문). 그래서 최초 롤아웃만 `window open`이 프로비저닝을 한 번 더 한다(인증서를 새로 만든 경우에만).

App Gateway는 Key Vault의 새 버전을 4시간 간격으로 가져오므로 갱신 후 재배포가 필요 없다.

## 5. 운영 도구와 출력

- **`CONSOLE_URL`:** `https://amalend-<접미사>.koreacentral.cloudapp.azure.com`(App Gateway). `CONSOLE_APP_NAME`은 그대로다.
- **`console_gate`:** `CONSOLE_URL`(App Gateway)을 확인한다. 자체 서명이라 이 확인에서만 TLS 검증을 끄고, 그 사실을 결과 문구에 적는다. 403은 "WAF가 현재 IP를 막았다(CONSOLE_IP 갱신 필요)"로 안내한다. App Gateway가 아직 없으면(인증서 대기) 그 상태를 알린다.
- **Easy Auth 복원 경로(ADR-007):** 앱 등록 리디렉션 URI를 App Gateway FQDN으로 바꾸고, `authConfigs`에 `httpSettings.forwardProxy.convention: Standard`를 둬 `X-Forwarded-Host`를 따르게 한다. 데모에서는 꺼진 채라 동작은 바뀌지 않는다.
- **`CONSOLE_IP`:** 지금처럼 `window open`이 갱신하고 `window close`는 유지한다. 반영 대상만 Container App ingress에서 WAF 정책으로 바뀐다.
- **VNet 안 smoke:** 바꾸지 않는다.

## 6. 롤아웃(파괴적 단계 포함)

Container App의 `environmentId`는 바꿀 수 없어서 콘솔을 지우고 다시 만든다. 이 단계는 실행 직전에 사용자 확인을 받는다.

1. `python -m ops window open`(현재 코드) → 운영자 IP 기록.
2. **삭제(확인 후):** `ca-console` → `cae-web` 순서로 삭제한다. 콘솔이 중단된다.
3. 새 코드로 `python -m ops window open`: 서브넷 교체(`snet-apps-web` 삭제, `snet-appgw` 추가), cae-int에 콘솔 재생성, Key Vault 창, 인증서 생성, 2차 프로비저닝으로 App Gateway 배포.
4. `python -m ops deploy console`(이미지가 azd 환경값으로 유지되면 생략 가능하나 확인용으로 실행).
5. `console_gate`와 브라우저로 확인한다: 운영자 IP에서 200, 화면 흐름(신청 → 결정 반영)이 그대로 동작.
6. `python -m ops window close`.

예상 중단 시간은 15~20분이다.

## 7. 테스트

- **인프라(pytest, `RUN_INFRA_TESTS=1`):** 서브넷 목록(`snet-appgw` 있음, `snet-apps-web` 없음), 콘솔 `environmentId`가 cae-int, 콘솔 ingress IP 제한 없음, App Gateway SKU·오토스케일·리스너 2개·리디렉션·프로브 `/healthz`·백엔드 FQDN, WAF 정책 모드·DRS 2.1·IP 규칙(빈 값이면 `192.0.2.0/32`), 인증서 없으면 App Gateway 미배포, Key Vault 창 열림·닫힘, 역할 할당 2개, 진단 설정.
- **ops 단위 테스트:** 인증서 보장(없음 → 생성·환경값 기록·재프로비저닝, 있음 → 건너뜀, 만료 임박 → 갱신), `console_gate`(200·403·App Gateway 없음 문구, TLS 검증 끔).
- **라이브 확인:** 6절 5단계. 운영자 IP가 아닌 경로(예: 모바일 데이터)에서 403을 한 번 확인한다.

## 8. 문서

- **새 ADR-011:** App Gateway WAF v2 진입점(대안: Front Door Premium, 외부 환경 유지 + IP 잠금, 로컬 PFX).
- `docs/architecture.md`: 구성 요소·네트워크 그림·운영 아키텍처 표(App Gateway 구축됨).
- `docs/cost-model.md`: App Gateway 고정비 약 $370/월, 유휴 환경 합계 갱신.
- `docs/limitations.md`: 자체 서명 인증서 경고, 단일 인스턴스·영역 비중복.
- `docs/runbook/deploy.md`·`deploy-history.md`: 콘솔 URL 변경, 인증서 보장, 롤아웃 기록.
- ADR-007: 콘솔 IP 허용 위치가 WAF로 바뀐 점과 복원 경로(리디렉션 URI·forwardProxy).
- 데모 스크립트의 콘솔 URL.

## 9. 위험

| 위험 | 대응 |
|---|---|
| DRS 2.1 오탐으로 콘솔 폼 POST가 403 | 라이브 확인에서 FirewallLog로 규칙 ID를 찾아 해당 규칙만 제외한다. 결과를 deploy-history에 남긴다. |
| App Gateway가 Key Vault 비밀을 못 읽어 배포 실패 | 관리 ID 역할을 App Gateway보다 먼저 배포(의존성)하고, 사설 DNS로 Key Vault가 풀리는지 확인한다. |
| 운영자 IP 변경(모바일 NAT) | 기존처럼 `window open`으로 `CONSOLE_IP`를 갱신한다. |
| 비용 | 상시 월 약 $370 증가. 유휴 환경 합계 약 $410 → 약 $780. |
