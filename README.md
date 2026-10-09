# 따릉이 고장신고 분석 — 신고율 재분석, 주간 예측, 점검 권장, 정비 대시보드

서울시 공공자전거 따릉이의 **이용자 고장신고**를 공개 데이터만으로 다시 분석했다. 신고를 이용량 대비 비율(신고율)로 보고, 유형별 주간 신고 건수를 예측하고, 자전거별로 다음 주 신고 가능성을 매겨 정비 담당자용 대시보드로 보여 준다.

- 지금 진행 상황과 남은 일: [`docs/STATUS.md`](docs/STATUS.md)
- 방향 총정리: [`docs/PROJECT_OVERVIEW.md`](docs/PROJECT_OVERVIEW.md)
- 결정과 근거: [`docs/decision_log.md`](docs/decision_log.md)
- 운영 파이프라인(작업표·스케줄·감시·재학습 기준): [`docs/pipeline.md`](docs/pipeline.md)
- 사람과 AI의 역할 분담: [`docs/WORKFLOW_사람-AI_역할분담.md`](docs/WORKFLOW_사람-AI_역할분담.md)

데이터 출처: 서울 열린데이터광장(OA-15644 고장신고, OA-15182 대여이력, OA-15246 이용정보, 일별 대여건수), 기상청 ASOS·단기예보·중기예보(공공데이터포털). 서울시 데이터는 공공누리 1유형(출처 표시). 자치구 경계는 통계청 센서스용 행정구역경계 2013(github.com/southkorea/seoul-maps, Apache 2.0).

---

## 무엇을 했나

| 단계 | 한 일 | 결과가 있는 곳 |
|---|---|---|
| 0 ~ 1A | 원본 정리, 일별 마스터(3,834일 × 75열), 신고율의 수준 변화(레짐) 확인 | `docs/notes/01·02`, `notebooks/1A_*` |
| 1B | 음이항 회귀로 요일·날씨·계절 효과 추정 | `docs/notes/03`, `notebooks/1B_regression.ipynb` |
| 1C | 주간 예측 모형 비교. 부품별 신고율 모형은 테스트에서 기준값을 넘지 못해, 운영은 "최근 4주 신고율 × 대여량 예측"으로 정함 | `docs/report_stage1.md`, `docs/notes/03` |
| 04b | 2025년 3월 신고 감소의 원인 분석(고장이 아니라 신고 성향의 변화로 보임) | `docs/notes/04_레짐원인_메모.md` |
| 2A ~ 2B | 대여이력 1.23억 건으로 자전거 × 주 패널, 다음 주 신고 가능성 모형(LightGBM) | `docs/notes/05·06`, `docs/risk_model_card.md` |
| 3A | 자동 수집·매시/매일/매주 갱신·발행, 경보, 모형 감시 | `docs/pipeline.md`, `py/jobs/` |
| 3B | 관리자 대시보드 | `app/` |

숫자는 스크립트가 `output/tables/`에 저장한 것만 인용한다. 주간 예측의 2024년 검증 오차는 `fc_ops_replay_valid.csv`, 위험 모형의 테스트 결과는 `risk_eval_test.csv`, 도입 효과 시나리오는 `impact_scenario.csv`에 있다.

## 대시보드

관리자 앱 하나에 네 화면이 있다. `serving/*.parquet`(운영 작업이 발행한 스냅샷)만 읽는다.

| 화면 | 내용 |
|---|---|
| 한눈에 보기 | 이번 주 고장 신고 예측, 대여 1,000건당 신고, 점검 권장 대수, 대여 직후 반납 반복, 자치구 타일맵, 예측 정확도 |
| 유형별 고장 신고 예측 | 전체·타이어·체인·안장·페달·단말기·기타의 이번 주 예측과 예측 구간, 주별 추이, 정확도, 예측 방법 |
| 점검 대상 | 최근 7일 고장 신고(신고 후 상태 추정), 점검 권장 순위, 대여 직후 반납 반복. 목록마다 지도와 CSV 내려받기 |
| 자전거 조회 | 자전거 번호 하나로 점검 대상 여부, 위치, 선정 사유, 최근 90일 신고 |

```bash
.venv/bin/streamlit run app/admin/Home.py --server.port 8501
```

## 폴더 구조

```
dareungi/
├─ CLAUDE.md            Claude Code 작업 규칙과 파일 지도(가장 자세한 목록은 여기)
├─ docs/                현황, 방향, 런북, 결정 로그, 단계별 메모, 참고 자료
├─ py/                  공용 모듈(prep, fc, fc_ops, nbreg …)과 단계별 스크립트
│  ├─ build/ panel/     1A 집계, 2A 대여이력 적재와 패널
│  ├─ forecast/ risk/ regime/   1C·운영 예측, 2B 위험 모형, 레짐 원인 분석
│  └─ jobs/             3A 운영 작업(수집·매시·매일·매주·발행·감시)
├─ app/                 대시보드(admin: 화면, common: 자료 읽기·문구·테마·자치구 경계)
├─ tests/               운영 회귀 시험, 분석 정의 시험, 화면 시험, 화면 캡처
├─ notebooks/           단계별 노트북(00 사전검증, 1A, 1B, 1C)
├─ deploy/              서버 1대 배포(설치 스크립트, 타이머, Caddy)
├─ output/              표·그림·모형 파일(보고서 숫자의 출처)
├─ data/                깃에 올리지 않음(예외: processed/daily_master_v2, raw/external, raw/calendar)
├─ serving/ logs/       발행한 스냅샷, 작업 로그(깃에 올리지 않음)
├─ .github/workflows/   푸시마다 시험과 문법 검사
└─ .env                 인증키와 경보 주소(깃에 올리지 않음. 예시는 .env.example)
```

## 처음 설치

```bash
git clone https://github.com/<아이디>/dareungi.git
cd dareungi
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env       # 그리고 .env에 실제 키를 넣는다
```

데이터는 깃에 없다.

| 하려는 일 | 필요한 것 |
|---|---|
| 단계 1B·1C(회귀·예측) 다시 보기 | 깃에 있는 `data/processed/daily_master_v2.parquet`만으로 된다 |
| 단계 1A 재실행, 단계 2(대여이력) | `data/` 폴더를 따로 옮긴다. 대여이력 원본은 약 15GB |
| 대시보드와 운영 작업 | 운영 DB(`data/db/ops.duckdb`)와 `data/interim/station_master.csv`가 있어야 한다. 서버로 옮기는 법은 `deploy/README.md` |

## 운영 작업과 시험

- 예약 실행: `docs/pipeline.md`의 스케줄을 crontab(맥북) 또는 systemd 타이머(서버, `deploy/setup.sh`)로 등록한다. 대여이력은 매시, 고장신고·위치 갱신도 매시, 집계는 매일 07:00, 위험 점수와 주간 예측은 월요일 07:30
- 경보: `.env`의 `ALERT_WEBHOOK`(슬랙·디스코드)으로 작업 실패, 자료 이상, 재학습 시기를 알린다. 확인: `.venv/bin/python py/common.py alert-test`
- 시험:

```bash
.venv/bin/python tests/test_ops.py        # 운영 파이프라인 회귀 시험
.venv/bin/python tests/test_analysis.py   # 분석 정의(인코딩, 신고·대여 세는 방법)
.venv/bin/python tests/smoke_app.py       # 네 화면이 예외 없이 그려지는지(serving/ 필요)
.venv/bin/python tests/screens.py         # 화면 캡처 + Streamlit 내부 이름표 확인(서버를 띄운 뒤)
```

## 저장소 규칙

단계 하나를 끝내고 사람이 결과를 검토해 확정한 뒤에 커밋하고 GitHub 비공개 저장소(`origin/main`)로 푸시한다. `data/`(위 예외 제외), `.env`, `.venv/`, `serving/`, `logs/`는 올라가지 않는다.

## Claude Code로 진행하기

```bash
cd ~/dareungi && source .venv/bin/activate && claude
```

| 명령 | 하는 일 |
|---|---|
| `/next` | STATUS와 런북을 읽고 다음 절차 계획을 제시. 승인하면 실행 |
| `/checkpoint 주제` | 결정이 필요한 지점에서 근거 수치와 선택지 제시. 사람이 고를 때까지 대기 |
| `/decide 결정 내용` | 사람이 내린 결정을 결정 로그에 기록, STATUS 갱신 |
| `/quiz` | 방금 끝낸 절차에 대한 이해 확인 질문(면접 대비) |
| `/wrapup` | 세션 마무리: STATUS 갱신, 커밋 메시지 제안(커밋은 승인 후) |
