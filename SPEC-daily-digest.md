# SPEC — Daily Inspiration Digest

gnollramy(N150, Ubuntu 24.04)에서 cron으로 도는 단일 파이썬 배치 잡.
매일 11:30 KST에 텔레그램+이메일로 큐레이션된 다이제스트 발송.

서비스 아님(웹서버/FastAPI 띄우지 말 것). 그냥 cron이 부르는 스크립트 하나.

---

## 목표 (verifiable)

- 평일: 메시지에 슬롯 2개 — ①LLM 신규 툴/커넥터/스킬 ②재밌는 헛소리
- 수(weekday=2): 위 2개 + ③-수 건설/인프라
- 금(weekday=4): 위 2개 + ③-금 라지스케일 세계정세 + 향후 발전 방향
- 텔레그램(`claudy_bot`, 짧게) + 이메일(풀버전) 동시 발송, 같은 큐레이션 결과
- 날짜 간 같은 url 중복 안 옴
- 건질 게 없으면 슬롯 비워도 됨 — 억지로 채우지 말 것

성공 판정: 위 5개가 실제 발송물에서 확인되면 v1 완료.
(주관적 "재밌냐"는 2주 사용 후 별도 튜닝 대상, v1 범위 아님)

---

## 구조 (4단계)

```
collect → dedup → curate(Claude 1콜) → send(텔레그램+이메일)
```

요일 분기는 코드에서: `datetime.now(KST).weekday()` 보고 수면 건설 슬롯, 금이면 라지스케일 슬롯을 큐레이션 입력에 포함.

---

## 1. collect

전부 RSS로 긁는다(소스별 API 키 회피). 인증 필요한 건 GitHub와 Claude뿐.

후보 한 건 = `{title, url, source, slot_hint, signal}`
- `signal`: HN points / reddit upvotes / GitHub stars-today (있으면, 랭킹 참고용)
- `slot_hint`: 소스가 어느 슬롯 후보인지 (llm_tool / chaos / official)

### 슬롯①  LLM 신규 툴/커넥터/스킬  (slot_hint=llm_tool)
- r/LocalLLaMA  → `https://www.reddit.com/r/LocalLLaMA/.rss`
- GitHub trending + Search API (아래 GitHub 섹션). topic: mcp, llm, agent, connector
- (선택) MCP 생태계 관련 레포 trending

**중요**: 거대 모델 출시(GPT/Claude 신버전 같은 거대담론)는 이 슬롯 아님.
"내 서버에 붙여 쓸 수 있는 툴/커넥터/스킬" 레벨만. 큐레이션 프롬프트에 명시.

### 슬롯②  재밌는 헛소리  (slot_hint=chaos)
- HN: Algolia front_page 또는 `https://news.ycombinator.com/rss` (댓글 많은 거 우선)
- lobste.rs → `https://lobste.rs/rss`
- reddit `.rss`: r/slatestarcodex, r/AskEngineers, r/selfhosted, r/homelab, r/fluidmechanics
- 긱뉴스 → `https://news.hada.io/rss/news`

**중요**: "발산적·생각 자극하는 헛소리" ≠ "그냥 웃긴 밈". 후자는 큐레이션에서 거른다.

### 슬롯③-수  건설/인프라  (수요일만, slot_hint=official_constr)
- Construction Physics (본진), Works in Progress 중 건설/제조/인프라 글
- "어떻게 짓는가, 왜 비싸지는가, 인프라 경제학" 레벨. 수신자 토목/ODA 일과 직결.

### 슬롯③-금  라지스케일 세계정세 + 향후 발전 방향  (금요일만, slot_hint=official_macro)
- GZERO/Eurasia Top Risks, Works in Progress, Quanta(기술 궤적), Our World in Data, Benedict Evans 류
- (선택) 컨설팅/IB 연간 outlook은 RSS 불안정 → v1은 위주로 위 소스

**중요(금요일 함정)**: 범위가 넓어 건조한 예측 문서로 회귀하기 쉽다.
"2030년 X조 달러" 식 예측 수치/면피성 outlook 말고,
**판의 구조가 왜 이렇게 바뀌는지 설명하는 글**을 우선하라고 큐레이션 프롬프트에 못 박을 것.

---

## GitHub — star velocity

**v1에서 별 수 히스토리 저장하지 말 것 (오버엔지니어링).**
`github.com/trending`이 이미 "★ N stars today/this week"를 박아준다 = 공짜 속도값, 상태저장 0.

v1:
- trending 페이지 스크레이프 → 언어/토픽 필터 (python, rust, + mcp/llm/agent 토픽)
- Search API: `created:>{7일전} sort:stars-desc` → 갓 나왔는데 폭발하는 신생 레포

업그레이드(지금 하지 말 것): trending이 실제로 부족할 때만,
dedup용 SQLite에 stars 히스토리 쌓아 진짜 daily-diff velocity 계산.

---

## 2. dedup

SQLite (wb_wash / personal_index와 같은 결).
테이블 `seen(url PRIMARY KEY, first_seen_date)`.
최근 30일 안에 본 url은 후보에서 제거. 발송 확정된 url은 발송 후 기록.

---

## 3. curate — Claude API 1콜

후보 전부를 한 번에 던진다. 콜 쪼개지 말 것(비용·복잡도만 늘고 이득 없음).

프롬프트 핵심:
- 수신자 프로파일: ODA 물/인프라 엔지니어, 직접 서버·툴 만드는 사람, 물리/유체 관심, 로컬LLM 실험 중
- 오늘 채울 슬롯: 평일 [①②], 수 [①②③-수 건설], 금 [①②③-금 라지스케일]
- 슬롯①: 거대담론 금지, 붙여쓰는 툴/커넥터/스킬만
- 슬롯②: 밈 금지, 생각 자극하는 날것만
- 슬롯③-수: 건설/인프라 정중앙, 수신자 토목/ODA와 직결되는 걸로
- 슬롯③-금: 예측 수치/면피성 outlook 금지, "판이 왜 이렇게 바뀌나" 설명하는 글 우선
- 각 슬롯 1개 (가끔 2개까지 허용). 건질 거 없으면 비워라.
- 출력: 슬롯별 {title, url, source, why_you(한 줄), summary(한 줄)} — **JSON only, 프리앰블/백틱 금지**

JSON 파싱 안전하게(```json 펜스 strip 후 parse, 실패 시 로그+해당 발송 skip).

---

## 4. send — 텔레그램 + 이메일

같은 큐레이션 결과, 포맷만 다르게.

- **텔레그램** (Bot API, 봇 이름 `claudy_bot`): 슬롯별 한 줄 이유 + 링크. 짧게. 점심에 폰으로 흘끗용.
  - BotFather로 `claudy_bot` 생성 → 토큰. chat_id는 본인.
- **이메일** (mailnara/gmail 인프라 재활용): 풀버전 — 슬롯별 why_you + summary + 링크. 보관·검색용.

발송 실패는 채널별 독립 처리(텔레그램 죽어도 이메일은 가게).

---

## cron

```
30 2 * * *  /path/to/venv/bin/python /path/to/digest.py   # 02:30 UTC = 11:30 KST
```

요일 분기는 스크립트 내부에서. cron은 매일 동일 시각 1회.

---

## 빌드 순서 (증분, 각 단계 검증 — CLAUDE.md 원칙)

1. collector 2~3개 피드 → 후보 print → 출력 멀쩡한지
2. 전체 피드 + GitHub → 후보 수·slot_hint 분포 확인
3. SQLite dedup → 두 번 돌려 중복 안 나오는지
4. Claude 큐레이션 콜 → top-N + 이유 읽고 말 되는지 (슬롯 성격 지켜지는지)
5. 텔레그램+이메일 발송 → 실제 도착 확인 → cron 등록

한 방에 다 쓰지 말 것. 단계별로 검증하고 넘어간다.

---

## 비범위 (v1에서 만들지 말 것)

- 웹 UI / FastAPI
- star velocity 히스토리 저장 (trending으로 충분)
- 슬롯별 Claude 콜 분리
- 설정 파일/플러그인 구조 등 "유연성"
- 재시도·큐 같은 인프라 (개인 저용량 배치엔 과함)

소스 리스트는 코드 상단 상수로. 튜닝은 2주 써보고.
