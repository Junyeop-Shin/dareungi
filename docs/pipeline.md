# 자동 수집 파이프라인 — 단계 3A (런북 07)

작성 2026-10-07(초안). 실행 환경은 **맥북 cron(개발)**부터. 2주 안정 뒤 서버로 옮긴다(3A-0의 권장안. 사람이 확정).

## 작업표

| 작업 | 파일 | 주기 | 입력 | 출력(ops.duckdb) | 비고 |
|---|---|---|---|---|---|
| collect_rent | `py/jobs/collect_rent.py` | 매시 15분 | `tbCycleRentData` | `raw_rent`, `rent_hours_done`, 뷰 `trips_live` | 직전 시간대 + 빠진 시간대 전부(따라잡기), 3시간 전·24시간 전 재수집(긴 대여는 반납 뒤에야 나옴). 멱등 |
| detect_sham | `py/jobs/detect_sham.py` | 매시 20분 | `trips_live` | `alerts_live` | 연속 2회 헛대여(2 ~ 3분) → 경보. 24시간 중복 제외 |
| hourly | `py/jobs/hourly.py` | 매시 25분 | `tbCycleFailureReport`(collect_fail을 부름), `trips_live`, `bike_risk_latest` | `reported_recent`, `bike_risk_latest`의 위치 열 → `serving/` 4개 발행 | 점검 대상 지도가 보는 시점에 가깝게(2026-10-09). 점수·등급은 안 바꿈. 위치는 실제보다 1 ~ 2시간 늦음(반납 뒤·시간대가 끝나야 API에 나옴). 실측 약 5초 |
| collect_fail | `py/jobs/collect_fail.py` | 매일 06:10 | `tbCycleFailureReport` | `raw_fail`, 뷰 `fail_live` | 최근 7일 다시 받음. 0건인 날 경보 |
| collect_wx | `py/jobs/collect_wx.py` | 06:30 (obs), 05:20·17:20 (fcst) | ASOS 시간자료, 단기예보(+103h), 중기예보(4 ~ 10일, 06·18시 발표) | `wx_hourly_live`, `fcst_archive`(src = short / mid) | 예보는 발표 시각과 함께 → 1C '실제 예보' 백테스트 재료. 중기 API 승인 확인 2026-10-07 |
| collect_station | `py/jobs/collect_station.py` | 월 05:00 | 대여소 API 2종 | `station_hist`, `station_live` | 새 대여소만 고도 API |
| daily | `py/jobs/daily.py` | 매일 07:00 | 위 표들 + `actuals.refresh()`(일 × 자치구 실적 → `weekly_actual`·`daily_reports`·`forecast_track`) | `trips_live_x`, `bw_usage`·`bw_fail`(이번 주), `panel`, `panel_latest`, `bike_card`, `quality_log` | 품질 점검 + 스냅샷 |
| weekly | `py/jobs/weekly.py` | 월 07:30 | `panel_latest`, 모형, `daily_district` | `bike_risk_latest`, `forecast_weekly` | 완결된 주로만 점수. 예측 = 그 주 월 ~ 일(언제 돌려도 원점은 그 주 월요일), 최근 4주 신고율 × 대여량 예측(`py/fc_ops.py`). 예보판을 보여 주고 예보가 비면 달력판, 두 값 모두 기록. 80% 구간은 2024 검증 구간 오차(`fc_ops_spec.json`) |
| publish | `py/jobs/publish.py` | daily·weekly 끝에 | 위 표 | `serving/*.parquet` | 원자적 교체. 대시보드는 이것만 읽음 |
| actuals | `py/jobs/actuals.py` | daily가 호출. 과거분은 `actuals.py hist` 한 번 | 분석 DB(과거), `trips_live`·`fail_live`(이후) | `daily_district(_hist/_live)`, `weekly_actual`, `daily_reports`, `forecast_track` | 대여 = 대여 대여소의 구, 신고 = 직전 반납 대여소의 구. 과거분은 마스터와 신고 건수 완전 일치. 유형 열은 6가지(타이어·체인·안장·페달·단말기·기타, 2026-10-10부터) |
| init_ops | `py/jobs/init_ops.py` | 처음 한 번, 반기 대사 뒤 | 분석 DB | `bw_usage`, `bw_fail`, `bike_info`, `station_base`, `wx_w`, `station_cluster` | '마지막 신고 이후 누적'을 2023-07부터 잇는다 |

공통: `py/common.py`(연결·API·경보·실행 기록·잠금 `data/db/.ops.lock` — 2초마다 최대 5분 대기), `py/panel_sql.py`(분석·운영이 같은 패널 정의).

## 스케줄 (맥북 cron, 사람이 `crontab -e`로 넣는다)

```
15 *  * * *  cd ~/dareungi && .venv/bin/python py/jobs/collect_rent.py    >> logs/rent.log 2>&1
20 *  * * *  cd ~/dareungi && .venv/bin/python py/jobs/detect_sham.py     >> logs/sham.log 2>&1
25 *  * * *  cd ~/dareungi && .venv/bin/python py/jobs/hourly.py          >> logs/hourly.log 2>&1
20 5  * * *  cd ~/dareungi && .venv/bin/python py/jobs/collect_wx.py fcst >> logs/wx.log 2>&1
10 6  * * *  cd ~/dareungi && .venv/bin/python py/jobs/collect_fail.py    >> logs/fail.log 2>&1
30 6  * * *  cd ~/dareungi && .venv/bin/python py/jobs/collect_wx.py obs  >> logs/wx.log 2>&1
0  7  * * *  cd ~/dareungi && .venv/bin/python py/jobs/daily.py           >> logs/daily.log 2>&1
30 7  * * 1  cd ~/dareungi && .venv/bin/python py/jobs/weekly.py          >> logs/weekly.log 2>&1
0  5  * * 1  cd ~/dareungi && .venv/bin/python py/jobs/collect_station.py >> logs/station.log 2>&1
20 17 * * *  cd ~/dareungi && .venv/bin/python py/jobs/collect_wx.py fcst >> logs/wx.log 2>&1
```

- 프로젝트 폴더는 홈 바로 아래(`~/dareungi`). `~/Documents` 아래면 macOS 권한으로 cron이 못 읽는다.
- 맥북이 잠들면 빠진 시간대는 다음 실행 때 따라잡는다(`rent_hours_done` 기준).

## 재시작·복구

| 상황 | 할 일 |
|---|---|
| 작업이 중간에 죽음 | 그냥 다시 실행. 멱등이라 같은 시간대를 두 번 받아도 결과 같음. 잠금 파일을 만든 작업이 죽었으면 다음 작업이 자동으로 지움(살아 있는 작업의 잠금은 오래 걸려도 그대로) |
| 특정 날짜를 다시 받고 싶음 | `collect_rent.py 2026-10-06` (그 날 24시간대) |
| 백필 뒤 주별 요약 다시 | `daily.py --rebuild-from 2026-06-29` (그 주부터 API 자료로 자전거별 주간 요약을 다시 만든다. 매일 실행은 이번 주와 지난주) |
| 고장신고 과거 구간 | `collect_fail.py range 2026-07-01 2026-10-07` (28일씩) |
| 반기 파일과 API 사이 공백(2026-07-01 ~ 10-04) | `PYTHONUNBUFFERED=1 nohup .venv/bin/python py/jobs/backfill_rent.py 2026-07-01 2026-10-04 > logs/backfill.log &` — 한 시간대씩 받고 3초 쉬어 다른 작업이 끼어들 수 있게. 시간대당 약 20초, 전체 약 10시간(2026-10-07 밤 시작). 멈추면 같은 명령으로 이어서(받은 시간대는 건너뜀) |
| 주간 예측 대여량 모형 다시 맞춤 | `py/forecast/kma_forecast.py`(보정값) → `py/forecast/fc_ops_fit.py`(재현 확인 뒤 저장) → `actuals.py track_hist`(과거 주 예보판) |
| 모형 바꿈 | `output/models/risk_spec.json`과 모형 파일 교체 → 다음 월요일 weekly에서 반영. 버전은 `model_version`에 남음 |
| 스키마 변화(API 필드 추가·삭제) | `INSERT BY NAME`에서 오류 → `raw_*` 표를 새 필드로 다시 만들고 뷰 수정 |

## 경보 (`common.alert`, `quality_log`)

| 점검 | 기준 | 대응 |
|---|---|---|
| 시간대별 대여 건수 급감 | 지난주 같은 시간의 30% 미만 | 3시간 뒤 재수집은 자동(3시간 전 재수집). 계속되면 API 장애 |
| 작업 실패 | `job_runs.status = 'fail'` | 로그 확인 후 재실행 |
| 안 도는 작업 | 수집은 마지막 성공이 26시간 이상 전, weekly는 8일 이상 전 | crontab·맥북 절전 확인 |
| 고장신고 0건인 날 | — | API 지연. 다음 날 7일 재수집으로 채워짐 |

`ALERT_WEBHOOK`(.env)이 있으면 디스코드·슬랙으로도 보낸다. 없으면 로그에만. **2026-10-10: 슬랙 채널 #dareungi-alert에 연결했고 시험 알림이 도착했다.** 주소를 바꾸면 `.venv/bin/python py/common.py alert-test`로 다시 확인한다(주소는 찍지 않고 결과만 알려 준다).

## 반기 파일 대사 (1월·7월)

1. 새 반기 파일을 `py/panel/21_rent_to_parquet.py`로 Parquet에 추가 → `py/panel/22_trips_clean.py` ~ `py/panel/29_panel.py` 다시.
2. 같은 기간 API 수집분과 날짜·시간대별 건수, 자전거별 건수를 비교해 `docs/reconcile_YYYYH.md`에 적는다.
3. 그 기간은 공식 파일로 덮어쓴다(`init_ops.py`로 주별 요약 재복사).
4. 새 자료로 재학습(검증 구간 재설정)하고 기존 모형과 비교해 나아졌을 때만 교체(모형 파일 이름에 날짜).

## 알려진 차이·한계

- 실시간 API에는 0 ~ 1분 대여가 그대로 들어온다(2026년 파일과 같음). 패널 규칙대로 "같은 대여소 ≤ 1분"은 이용 요약에서 뺀다.
- 중기예보 API는 2026-10-07 승인 확인·수집 시작. 기상청은 중기예보를 **4일 뒤부터** 준다(3일 뒤 항목은 비어 있음). 단기예보는 3일째부터 3시간 간격이라 값 하나를 3시간으로 센다(1C 과거 파일과 같은 규칙).
- 주간 예측(2026-10-08부터): 최근 4주 신고율 × 대여량 예측. 대여량은 예보판(1C K2, 실시간 예보를 `kma_calibration.json`으로 1C와 같게 보정)과 달력판(요일·공휴일만) 두 가지. 화면은 예보판, 앞으로 7일 중 하루라도 예보가 비면 달력판으로 대신하고 경보. 두 모형 계수는 `output/models/expo_ops.json`에 고정(`py/forecast/fc_ops_fit.py`, 2024 검증 구간에서 1C 숫자 그대로 재현).
- 그 전에 쓰던 '최근 4주 신고율 × 지난주 대여량'은 1C에서 검증된 적 없는 단순화였고, 2024 검증 구간에서 나이브보다 나빴다(`output/tables/fc_ops_replay_valid.csv`).
- 교체 조건: 2026-10-12 이후 운영 주로 최근 12주 전체 MAE%가 달력판이 예보판보다 3%p 넘게 낮으면 경보(`fc_compare`). 교체 여부는 사람이 정한다.
- 공휴일 표는 `data/raw/calendar/holidays_*.csv`(깃에 있음). 2027년까지 받아 두었다. 다음 해는 연말 전에 `py/build/holidays.py <연도>`(빠지면 경보).
- 반기 파일(~2026-06-30)과 API 수집 시작(2026-10-04) 사이 공백은 `backfill_rent.py`로 채웠다(2026-10-08 확인: 2026-07-01 이후 빠진 시간대 0).
- API 오류 메시지에는 키가 든 URL이 찍힌다(`requests`). `common.mask_keys()`로 가린 뒤 `job_runs`·경보·예외에 남긴다.
- 데이터 없는 시간대는 `{"CODE": "INFO-200"}`(맨 위)로 오고, 키 오류는 JSON을 요청해도 XML(`INFO-100`)로 온다. `common.fetch`가 둘 다 처리한다.
- 대여소 API의 번호 없는 ID 187곳은 대여이력에 번호로만 기록되어 쓰이지 않는다.

## 운영 전 점검표

- [x] 멱등성: 같은 시간대 두 번 받아도 결과 같음(`DELETE` 뒤 `INSERT`). 2026-10-08 10-07 10시를 두 번 받아 행 수·내용 해시 같음
- [x] 따라잡기: 2026-10-08 수집을 17시간 멈춘 뒤 한 번 실행 → 빠진 17시간대(95,510건)와 신고·기상을 채움
- [x] 실패 시험: 잘못된 키로 실행 → `job_runs` fail + `[ALERT] … INFO-100 인증키가 유효하지 않습니다`, 기존 자료는 그대로(2026-10-08. 시험으로 남긴 fail 행은 지움). 웹훅 전송은 `ALERT_WEBHOOK`을 넣은 뒤 따로 확인
- [x] `serving/*.parquet`만으로 대시보드가 뜸: `app/`에 DB 연결 코드 없음
- [x] 인증키가 깃(커밋 14개)·로그·스냅샷 어디에도 없음(2026-10-08). 오류 메시지 속 키 노출 경로는 `mask_keys`로 막음
- [ ] 2주 연속 무중단 수집 로그

## 자동 시험과 자료 점검 (2026-10-10)

- **자동 시험**: `.github/workflows/tests.yml`이 푸시·PR마다 `tests/test_ops.py`(7개)와 pyflakes를 돌린다. 화면 시험(`tests/smoke_app.py`)은 `serving/`이 있어야 해서 손으로 돌린다
- **자료 내용 점검**(`daily.data_checks`, 매일 07:00): 새 신고 유형(여섯 가지 밖), 번호·시각을 읽지 못한 신고가 1% 초과, 이상한 대여 기록(반납이 대여보다 이름·이용 시간 음수·미래 시각). 건수 점검(급감·0건·안 도는 작업)과 함께 `quality_log`에 남기고 경보를 보낸다
- **예외 처리**: 수집·매시·매일·매주 작업은 실패하면 `job_runs`에 fail로 남기고 경보를 보낸 뒤 멈춘다. 실적 갱신(actuals)·발행(publish)·잠금 실패도 같은 방식으로 맞췄다(전에는 로그에만 남았다)

## 재학습 기준

지금 모형은 고정돼 있다: 대여량 모형 2021-04-08 ~ 2024-12-31(`expo_ops.json`), 위험 모형 2024-01 ~ 2025-06(`risk_spec.json`).

| 모형 | 무엇을 본다 | 다시 맞출 조건 | 다시 맞추는 방법 | 바꾸기 전 확인 |
|---|---|---|---|---|
| 주간 예측의 대여량 모형 | `forecast_track`의 전체 신고 오차(예측 모형 · 기준값) | **확정(사람, 2026-10-10): 최근 12주 오차가 기준값(지난주와 같다고 가정)보다 크다.** `actuals.retrain_check`가 매주 월요일에 확인해 경보를 보낸다. **조건에 걸리지 않아도 해마다 1월에 다시 맞춘다(확정, 사람 2026-10-10)** — 새해 공휴일 표를 넣으면서 학습 끝을 지난해 말까지 늘린다. 1월 첫 월요일에 알림이 간다 | `py/forecast/fc_ops_fit.py`의 학습 끝(`TEST_REFIT_END`)을 늘려 실행. 예측 구간도 같이 다시 계산된다 | 최근 26주를 빼고 맞춘 모형으로 그 26주를 예측해, 같은 26주에 대한 지금 계수의 오차(`forecast_track`)보다 작은지 본다. 작으면 26주까지 넣어 다시 맞춰 교체하고, 작지 않으면 바꾸지 않는다(학습에 넣은 주로 성적을 재면 늘 좋아 보이므로 빼고 잰다) |
| 예보판 ↔ 달력판 | `fc_compare`(2026-10-12부터 12주) | 확정(2026-10-08): 12주 누적 오차가 달력판이 3%p 넘게 낮으면 교체 검토 | `fc_ops_spec.json`의 shown | — |
| 위험 모형(점검 권장) | `risk_eval`의 주별 상위 1,000대 적중률(무작위 대비 배수), 부품별 신고 비중 | **확정(사람, 2026-10-10: AI 제안대로).** 채점이 8주 쌓일 때까지(2026-12 초)는 숫자만 쌓는다. 8주째에 알림 한 번: 처음 8주 평균이 2.7배 이상이면 기준은 **최근 8주 평균 2.5배 아래**, 그보다 낮으면 사람이 기준을 다시 정한다(운영 채점은 테스트와 집계가 달라 처음부터 낮을 수 있다). 근거: 2B 테스트에서 모형 3.0배, 주행량만 본 기준 2.7배. `risk_track.retrain_note`가 weekly에서 확인한다. 담당자가 추천 자전거를 손보기 시작하면 배수가 낮아지므로 그때는 이 조건을 그대로 쓰지 않는다. 부품 비중 순서는 2025년 3분기에 이미 바뀌어 조건이 아니라 v2에서 고칠 것으로 둔다 | 새 판(v2)으로 다시 만든다. 바꿀 것은 `docs/STATUS.md`의 v2 항목 | 테스트 구간을 이미 열었으므로 2026-07 이후 자료와 운영 주로 평가한다 |

공통 원칙: 다시 맞춘 모형은 파일 이름·설정의 version을 올리고 결정 로그에 남긴다. 결과가 나빠도 기준을 바꿔 좋게 보이게 하지 않는다.

위험 모형의 이력과 채점(2026-10-10): `py/jobs/risk_track.py`. weekly가 점수를 낸 뒤 `risk_history`(주 × 자전거)에 쌓고, 다음 주가 다 지난 주를 `risk_eval`에 채점한다(주마다 한 줄, weekly 로그에 한 줄 출력). 둘 다 운영 DB에만 있고 화면에는 아직 내보내지 않는다. 이력은 2026-09-28 주 점수부터라 첫 채점은 2026-10-12 실행에서 나온다. 담당자가 추천 자전거를 미리 손보면 신고가 줄어 적중률이 낮게 나온다는 점을 같이 읽는다.
