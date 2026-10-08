# 자동 수집 파이프라인 — 단계 3A (런북 07)

작성 2026-10-07(초안). 실행 환경은 **맥북 cron(개발)**부터. 2주 안정 뒤 서버로 옮긴다(3A-0의 권장안. 사람이 확정).

## 작업표

| 작업 | 파일 | 주기 | 입력 | 출력(ops.duckdb) | 비고 |
|---|---|---|---|---|---|
| collect_rent | `py/jobs/collect_rent.py` | 매시 15분 | `tbCycleRentData` | `raw_rent`, `rent_hours_done`, 뷰 `trips_live` | 직전 시간대 + 빠진 시간대 전부(따라잡기), 3시간 전 재수집. 멱등 |
| detect_sham | `py/jobs/detect_sham.py` | 매시 20분 | `trips_live` | `alerts_live` | 연속 2회 헛대여(2 ~ 3분) → 경보. 24시간 중복 제외 |
| collect_fail | `py/jobs/collect_fail.py` | 매일 06:10 | `tbCycleFailureReport` | `raw_fail`, 뷰 `fail_live` | 최근 7일 다시 받음. 0건인 날 경보 |
| collect_wx | `py/jobs/collect_wx.py` | 06:30 (obs), 05:20·17:20 (fcst) | ASOS 시간자료, 단기예보(+103h), 중기예보(4 ~ 10일, 06·18시 발표) | `wx_hourly_live`, `fcst_archive`(src = short / mid) | 예보는 발표 시각과 함께 → 1C '실제 예보' 백테스트 재료. 중기 API 승인 확인 2026-10-07 |
| collect_station | `py/jobs/collect_station.py` | 월 05:00 | 대여소 API 2종 | `station_hist`, `station_live` | 새 대여소만 고도 API |
| daily | `py/jobs/daily.py` | 매일 07:00 | 위 표들 | `trips_live_x`, `bw_usage`·`bw_fail`(이번 주), `panel`, `panel_latest`, `bike_card`, `quality_log` | 품질 점검 + 스냅샷 |
| weekly | `py/jobs/weekly.py` | 월 07:30 | `panel_latest`, 모형 | `bike_risk_latest`, `forecast_weekly` | 완결된 주로만 점수. 예측은 일별 자료 35일 뒤부터 |
| publish | `py/jobs/publish.py` | daily·weekly 끝에 | 위 표 | `serving/*.parquet` | 원자적 교체. 대시보드는 이것만 읽음 |
| init_ops | `py/jobs/init_ops.py` | 처음 한 번, 반기 대사 뒤 | 분석 DB | `bw_usage`, `bw_fail`, `bike_info`, `station_base`, `wx_w`, `station_cluster` | '마지막 신고 이후 누적'을 2023-07부터 잇는다 |

공통: `py/common.py`(연결·API·경보·실행 기록·잠금 `data/db/.ops.lock` — 2초마다 최대 5분 대기), `py/panel_sql.py`(분석·운영이 같은 패널 정의).

## 스케줄 (맥북 cron, 사람이 `crontab -e`로 넣는다)

```
15 *  * * *  cd ~/dareungi && .venv/bin/python py/jobs/collect_rent.py    >> logs/rent.log 2>&1
20 *  * * *  cd ~/dareungi && .venv/bin/python py/jobs/detect_sham.py     >> logs/sham.log 2>&1
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
| 작업이 중간에 죽음 | 그냥 다시 실행. 멱등이라 같은 시간대를 두 번 받아도 결과 같음. 잠금 파일이 30분 넘게 남아 있으면 자동으로 지움 |
| 특정 날짜를 다시 받고 싶음 | `collect_rent.py 2026-10-06` (그 날 24시간대) |
| 반기 파일과 API 사이 공백(2026-07-01 ~ 10-04) | `PYTHONUNBUFFERED=1 nohup .venv/bin/python py/jobs/backfill_rent.py 2026-07-01 2026-10-04 > logs/backfill.log &` — 한 시간대씩 받고 3초 쉬어 다른 작업이 끼어들 수 있게. 시간대당 약 20초, 전체 약 10시간(2026-10-07 밤 시작). 멈추면 같은 명령으로 이어서(받은 시간대는 건너뜀) |
| 모형 바꿈 | `output/models/risk_spec.json`과 모형 파일 교체 → 다음 월요일 weekly에서 반영. 버전은 `model_version`에 남음 |
| 스키마 변화(API 필드 추가·삭제) | `INSERT BY NAME`에서 오류 → `raw_*` 표를 새 필드로 다시 만들고 뷰 수정 |

## 경보 (`common.alert`, `quality_log`)

| 점검 | 기준 | 대응 |
|---|---|---|
| 시간대별 대여 건수 급감 | 지난주 같은 시간의 30% 미만 | 3시간 뒤 재수집은 자동(3시간 전 재수집). 계속되면 API 장애 |
| 작업 실패 | `job_runs.status = 'fail'` | 로그 확인 후 재실행 |
| 안 도는 작업 | 마지막 성공이 26시간 이상 전 | crontab·맥북 절전 확인 |
| 고장신고 0건인 날 | — | API 지연. 다음 날 7일 재수집으로 채워짐 |

`ALERT_WEBHOOK`(.env)이 있으면 디스코드·슬랙으로도 보낸다. 없으면 로그에만.

## 반기 파일 대사 (1월·7월)

1. 새 반기 파일을 `py/21_rent_to_parquet.py`로 Parquet에 추가 → `py/22_trips_clean.py` ~ `py/29_panel.py` 다시.
2. 같은 기간 API 수집분과 날짜·시간대별 건수, 자전거별 건수를 비교해 `docs/reconcile_YYYYH.md`에 적는다.
3. 그 기간은 공식 파일로 덮어쓴다(`init_ops.py`로 주별 요약 재복사).
4. 새 자료로 재학습(검증 구간 재설정)하고 기존 모형과 비교해 나아졌을 때만 교체(모형 파일 이름에 날짜).

## 알려진 차이·한계

- 실시간 API에는 0 ~ 1분 대여가 그대로 들어온다(2026년 파일과 같음). 패널 규칙대로 "같은 대여소 ≤ 1분"은 이용 요약에서 뺀다.
- 중기예보 API는 2026-10-07 승인 확인·수집 시작. 기상청은 중기예보를 **4일 뒤부터** 준다(3일 뒤 항목은 비어 있음). 단기예보가 +103시간이라 4일째는 둘이 겹친다. 주간 예측에 1C 음이항을 붙이는 것은 일별 자료가 35일 쌓인 뒤.
- 반기 파일(~2026-06-30)과 API 수집 시작(2026-10-04) 사이 공백은 채우기 전까지 패널의 누적값이 과소.
- 대여소 API의 번호 없는 ID 187곳은 대여이력에 번호로만 기록되어 쓰이지 않는다.

## 운영 전 점검표

- [x] 멱등성: 같은 시간대 두 번 받아도 결과 같음(`DELETE` 뒤 `INSERT`)
- [ ] 따라잡기: 맥북을 6시간 재운 뒤 빠진 시간대가 채워지는지
- [ ] 실패 시험: 잘못된 키로 실행하면 경보
- [ ] `serving/*.parquet`만으로 대시보드가 뜸(DB 잠금 없음)
- [ ] 인증키가 깃·로그·스냅샷 어디에도 없음
- [ ] 2주 연속 무중단 수집 로그
