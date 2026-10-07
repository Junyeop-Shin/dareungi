# 패널 변수 사전 — 단계 2A

형식: 이름 | 정의 | 계산 창 | 단위 | 결측의 뜻. 표가 만들어질 때마다 채운다. 숫자 근거는 `docs/notes/05_개체패널_메모.md`.

## trips (DuckDB, 2A-2) — 대여 한 건이 한 행, 2023-07 ~ 2026-06, 122,971,118행

| 이름 | 정의 | 단위 | 결측 |
|---|---|---|---|
| bike_id | 자전거번호 'SPB-12345'의 숫자 | 정수 | 없음(없는 행 261건 제외) |
| rent_dt, rtn_dt | 대여·반납 일시(한국 시간, 시간대 없음) | TIMESTAMP | rtn_dt 결측은 bad_time |
| rent_st, rtn_st | 대여·반납 대여소번호(대여이력의 번호 체계. ST-xxx ID와 섞지 않는다) | 정수 | |
| use_min, use_m | 이용시간, 이용거리(원본 그대로) | 분, m | |
| bike_type | 자전거구분(일반·새싹) | 문자 | 2026년 파일은 NULL |
| user_type | 이용자종류(내국인·비회원·외국인) | 문자 | |
| y, m | 대여 연·월(파일 분할 키) | 정수 | |
| bad_time | 반납이 없거나 대여보다 앞섬 | 0/1 | |
| speed_kmh | use_m ÷ use_min | km/h | use_min = 0이면 NULL |
| sham | 헛대여 후보: 같은 대여소 반납 & 2 ~ 3분 | 0/1 | |
| sham_wide | 같은 대여소 반납 & 2 ~ 5분(민감도) | 0/1 | |
| round_trip | 왕복: 같은 대여소 반납 & 3분 초과 | 0/1 | |
| dist_bad | 다른 대여소인데 거리 0, 또는 35km/h 초과 | 0/1 | |

## station (DuckDB, 2A-3) — 대여소 3,244곳

| 이름 | 정의 | 단위 | 결측 |
|---|---|---|---|
| st_no | 대여소번호(대여이력 체계) | 정수 | |
| st_id | 'ST-xxx' ID | 문자 | |
| st_name, addr, district | 이름, 주소, 자치구 | 문자 | |
| lat, lon | 좌표(서울 밖 값은 비움) | 도 | 77곳 |
| hold_num | 거치대 수 | 정수 | |
| han_river | 이름에 '한강'(임시. 지도 검수 필요) | 0/1 | |
| elev_m | 고도(SRTM 30m). 대여소 간 차이로만 쓴다 | m | 좌표 없으면 NULL |

## od (DuckDB, 2A-4) — 실제 등장한 출발·도착 쌍 1,644,682개

| 이름 | 정의 | 단위 | 결측 |
|---|---|---|---|
| rent_st, rtn_st, n | 출발·도착 대여소번호, 건수 | | |
| straight_m | 두 대여소의 직선거리(하버사인) | m | 좌표 없으면 쌍이 빠짐 |
| delta_elev | 도착 고도 − 출발 고도 | m | |

## wx_h (DuckDB, 2A-5) — 정시마다 한 행, 26,304행

| 이름 | 정의 | 단위 | 결측 |
|---|---|---|---|
| ts | 정시(한국 시간) | TIMESTAMP | |
| temp, humidity, wind | 기온, 습도, 풍속(빠진 시간은 앞 값) | ℃, %, m/s | |
| rain_mm | 그 시간 강수(빈칸 = 0) | mm | |
| rain_prev3h | 직전 3시간(1 ~ 3시간 전) 강수 합 | mm | 첫 3시간 NULL |

## trips_x / trips_seq (DuckDB, 2A-7) — trips에 아래 열을 더한 것

| 이름 | 정의 | 단위 | 결측 |
|---|---|---|---|
| commute | 평일(공휴일 제외) 07 ~ 09시 또는 17 ~ 19시 대여 & 왕복 아님 | 0/1 | |
| leisure | 주말·공휴일, 또는 왕복, 또는 한강 대여소 출발·도착 | 0/1 | |
| straight_m, delta_elev, climb_m, descent_m | OD의 직선거리·고도차·오르막(max(Δ,0))·내리막(max(−Δ,0)) | m | 좌표 없는 쌍 0.48% |
| detour | use_m ÷ straight_m | 배 | |
| use_m_fixed | dist_bad면 직선거리 × 1.18(좌표 없으면 시간 × 8.6km/h), 아니면 use_m | m | 없음 |
| temp, rain_now, rain_prev3h | 대여 시각의 기온, 강수, 직전 3시간 강수 | ℃, mm | 없음 |
| prev_rtn_st, prev_rtn_dt | 같은 자전거의 직전 반납 대여소·시각 | | 첫 대여 NULL |
| relocated | 직전 반납과 다른 대여소에서 대여됨(사람 없이 옮겨짐) | 0/1 | |
| idle_h | 직전 반납 뒤 쉰 시간 | 시간 | 첫 대여 NULL |

## fail / fail_loc / fail_gap (DuckDB, 2A-8) — 신고 행(자전거×유형), 2023-07 이후

| 이름 | 정의 | 단위 | 결측 |
|---|---|---|---|
| bike_id, reg_ts, kind, keep | 자전거, 등록일시(한국 시간), 고장유형, 48시간 중복이 아니면 1 | | |
| last_st, last_rtn_dt | 신고 직전 마지막 반납 대여소·시각(= 신고 위치의 대리) | | 0.6% |
| next_rent_dt, gap_h | 신고 뒤 첫 대여 시각, 신고 → 그때까지 시간 | 시간 | 1.5%(자료 끝) |

## bike_week.parquet (2A-9) — 자전거×주, 2024-01 ~ 2026-06, 5,477,654행. 주는 월요일 시작

| 이름 | 정의 | 계산 창 | 단위 | 결측 |
|---|---|---|---|---|
| bike_id, wk | 자전거, 주 시작일 | | | |
| n_trips, km, minutes | 그 주 대여 수(같은 대여소 0 ~ 1분 제외), km(use_m_fixed 합), 분 | 그 주 | | 이용 없는 주 0 |
| sh_commute, sh_leisure, sh_round | 출퇴근·여가·왕복 통행 비중 | 그 주 | 비율 | 이용 없는 주 NULL |
| sh_rain, sh_wet, sh_hot, sh_cold | 비 오는 중·젖은 노면·30℃ 이상·0℃ 이하 주행 비중 | 그 주 | 비율 | 이용 없는 주 NULL |
| climb_m, descent_m | 오르막·내리막 합 | 그 주 | m | 0 |
| speed_med, speed_sd | 속도 중앙값·표준편차 | 그 주 | km/h | 이용 없는 주 NULL |
| n_reloc, n_sham | 재배치 수, 헛대여 수 | 그 주 | 건 | 0 |
| n_rep, n_tire, n_chain, n_saddle, n_pedal | 그 주 신고 건수(48시간 중복 뺌), 부품별 행 수 | 그 주 | 건 | 0 |
| repaired_likely | 그 주 신고 뒤 48시간 이상 공백(**신고 이후 정보. 설명변수로 쓰지 않는다**) | 그 주 | 0/1 | |
| rain_days, freeze_days | 그 주 강수일(≥1mm)·동결융해일(tmin<0<tmax) | 그 주 | 일 | |
| seg | 관측 시작 후 신고 횟수(구간 번호) | 누적 | | |
| km_since_rep, trips_since_rep, climb_since_rep, rain_days_since_rep, freeze_days_since_rep | 마지막 신고 다음 주부터의 누적 | 누적 | | seg=0이면 관측 시작부터 |
| last_repaired | 직전 신고의 수리 추정(공백이 끝난 뒤부터 값) | | 0/1 | seg=0 또는 아직 재대여 전 NULL |
| km_cum, km_total | 2023-07부터 누적 km, + 2023-06 이전 누적(bike_year) | 누적 | km | |
| left_censored | seg = 0(그 전 신고 이력을 모름) | | 0/1 | |
| days_since_rep | 마지막 신고 주로부터 일수 | | 일 | 신고 없으면 NULL |
| rep_13w, km_4w, sham_2w | 최근 13주 신고, 4주 km, 2주 헛대여(현재 주 포함) | 뒤로 | | |
| y_next, y_tire_next, y_chain_next, y_saddle_next, y_pedal_next | **라벨**: 다음 주 신고·부품별 | 다음 주 | 건 | 마지막 주 제외됨 |
| km_next | 다음 주 km(**라벨 쪽 정보. 노출 상한 실험에만**) | 다음 주 | km | |
| bike_type | 차종(최빈값) | | | 2026 신규 0.007% |
| age_known | 번호가 2022-03 이후 처음 나타남 = 실제 나이를 안다(그 전 번호는 QR 때 재부여). 행의 36% | | 0/1 | |
| age_days | 실제 나이(일). **age_known인 자전거만** | | 일 | 64% NULL |
| days_since_number | 번호가 처음 나타난 뒤 일수(전부. 재부여 자전거는 나이가 아님) | | 일 | |
| sh_cl0 ~ sh_cl4 | 그 주 대여의 출발 대여소 군집(2A-6, k = 5) 비중. k를 바꾸면 열 수가 바뀐다 | 그 주 | 비율 | 이용 없는 주 NULL |
| trips_pre | 2023-06 이전 누적 대여(bike_year). 재부여 자전거는 번호가 나타난 뒤부터만 | | 건 | 0 |
