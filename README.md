# 따릉이 고장신고 분석 — 신고율 재분석과 부품별 주간 예측

서울시 공공자전거 따릉이의 **이용자 고장신고**를 이용량 대비 신고율로 다시 분석하고, 부품별 다음 주 신고를 나이브보다 정확히 예측한다. 자전거별 위험도와 정비 대시보드로 확장할 계획이다.

- 방향 총정리: [`docs/PROJECT_OVERVIEW.md`](docs/PROJECT_OVERVIEW.md)
- 지금 진행 상황: [`docs/STATUS.md`](docs/STATUS.md)
- 실행 절차(런북): [`docs/runbook/`](docs/runbook/)
- 사람과 AI의 역할 분담: [`docs/WORKFLOW_사람-AI_역할분담.md`](docs/WORKFLOW_사람-AI_역할분담.md)

데이터 출처: 서울 열린데이터광장(OA-15644 고장신고, OA-15182 대여이력, OA-15246 이용정보, 일별 대여건수), 기상청 ASOS(공공데이터포털). 서울시 데이터는 공공누리 1유형(출처 표시).

---

## 폴더 구조

```
dareungi/
├─ CLAUDE.md            Claude Code 작업 규칙
├─ docs/                현황, 방향, 런북, 결정 로그, 참고 자료
│  ├─ notes/            단계별 메모(전처리·EDA·모델 설계): 무엇을 어떻게 왜 처리했는지
│  └─ reference/        검토·조사 자료(07~10은 신고율 변화 원인과 외부 요인)
├─ py/                  공용 함수(prep.py, seoul_api.py)와 집계 스크립트
│                       (use_daily, rent_history_daily, bike_month, failure_gaps)
├─ notebooks/           단계별 노트북 (00_사전검증, 1A_Build_master, 1A_regime, 1A_eda …)
├─ data/                깃에 올리지 않음 (processed/daily_master_v2만 예외)
│  ├─ raw/              다운로드 원본, 수정 금지
│  │  ├─ external/      외부 사건(events.csv), 공식 운영 대수(fleet.csv) — 직접 조사해 만든 파일
│  │  └─ failure_api/   고장신고 파일에서 빠진 날을 API로 받은 것
│  ├─ interim/          UTF-8 사본, 날짜별·자전거별 합계 등 중간 산출물
│  └─ processed/        분석 입력 최종본
├─ output/              표·그림·모델
├─ legacy/              1차 분석 R 스크립트·데이터 (참고용)
├─ .env                 인증키, 깃에 올리지 않음
└─ .venv/               가상환경, 깃에 올리지 않음
```

## 저장소

2026-10-05에 git 저장소를 만들었다. 단계 하나를 끝내고 결과를 검토해 최종본으로 확정한 뒤에 커밋하고 GitHub 비공개 저장소(`origin/main`)로 푸시한다.
`data/`(예외: `data/processed/daily_master_v2.*`, `data/raw/external/`), `.env`, `.venv/`는 올라가지 않는다.

## 다른 컴퓨터(집)에서 이어 하기

```bash
git clone https://github.com/<아이디>/dareungi.git
cd dareungi
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env       # 그리고 .env에 실제 키를 넣는다
```

데이터는 깃에 없으므로 둘 중 하나를 고른다.

| 방법 | 언제 | 어떻게 |
|---|---|---|
| A. 마스터만 쓰기 | 단계 1B·1C(회귀·예측) | `data/processed/daily_master_v2.parquet`는 깃에 올라가 있으니 `git pull`만 하면 된다 |
| B. 원본까지 옮기기 | 단계 1A 재실행, 단계 2(대여이력) | 외장 드라이브나 클라우드 드라이브로 `data/` 폴더를 복사. 대여이력 원본은 약 15GB |

`data/raw/external/`(events.csv, fleet.csv)은 직접 조사해 만든 작은 파일이라 다시 받을 수 없어서 깃에 올린다.

API로 받는 데이터(ASOS, 공휴일)는 키만 있으면 노트북에서 다시 받을 수 있다.

## Claude Code로 진행하기

```bash
cd ~/dareungi
source .venv/bin/activate
claude
```

| 명령 | 하는 일 |
|---|---|
| `/next` | STATUS와 런북을 읽고 다음 절차 계획을 제시. 승인하면 실행 |
| `/checkpoint 주제` | 결정이 필요한 지점에서 근거 수치와 선택지 제시. 사람이 고를 때까지 대기 |
| `/decide 결정 내용` | 사람이 내린 결정을 결정 로그에 기록, STATUS 갱신 |
| `/quiz` | 방금 끝낸 절차에 대한 이해 확인 질문(면접 대비) |
| `/wrapup` | 세션 마무리: STATUS 갱신, 커밋 메시지 제안(커밋은 승인 후) |

작업 순서: **`git pull` → `/next` → (실행·검토·`/checkpoint`·`/decide` 반복) → `/quiz` → `/wrapup`(단계 결과를 검토해 확정한 뒤 커밋·푸시)**

## 보안 메모

- 인증키는 `.env`에만 둔다. 실수로 커밋했다면 즉시 키를 재발급한다(이력에서 지워도 이미 노출된 것으로 본다).
- `.claude/settings.json`이 Claude Code의 `.env` 읽기와 `data/raw/` 수정을 막고, 커밋·푸시·삭제는 매번 확인을 받게 한다.
