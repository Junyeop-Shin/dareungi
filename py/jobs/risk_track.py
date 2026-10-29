"""자전거별 점수의 이력과 채점(사람 결정 2026-10-10), 그리고 화면의 점검 권장 등급을 매기는 규칙(2026-10-23).

2026-10-23부터 화면의 점검 권장 순위는 '신고 없이 손볼 자전거' 모형(정답 = 다음 주 실제 수리, output/models/repair_spec.json)으로
매긴다(repair_grade). 지금까지의 위험 모형(신고로 학습)은 화면에서 내렸지만 계속 점수를 내고, 아래 이력·채점은 그 위험 모형을
지켜본다(risk_history의 grade·risk_ratio = 위험 모형). 새 모형 점수는 repair_grade·repair_ratio 열에 함께 쌓는다.
새 모형은 정답(정비 기록)이 운영 중에 들어오지 않아 채점하지 못한다. 공단 정비 자료를 다시 받으면 이 이력으로 채점한다.

왜: 주간 예측은 forecast_track으로 매주 채점하는데, 점검 권장 순위는 2B 테스트(2026-10-07) 뒤로 맞는지 본 적이 없다.
점수 표(bike_risk_latest)는 매주 덮어쓰여서 '그때 누구를 추천했는지'도 남지 않았다.

  risk_history  매주 점수를 낸 그대로 쌓는다(주 × 자전거). 같은 주를 다시 돌리면 그 주만 바꿔 넣는다
  risk_eval     주마다 한 줄: 그다음 주(wk + 7일 ~ wk + 13일)에 실제로 신고된 비율. 다음 주가 다 지난 주만 채점한다
                base_rate 순위를 매긴 자전거 전체, hit_top1000 배수 상위 1,000대, lift_top1000 = 둘의 비(무작위 대비 몇 배),
                hit_insp 화면의 점검 권장 목록(점검 권장 등급 중 점수 낼 때 최근 신고가 없던 자전거), share_* 그 주 신고의 부품별 비중

읽을 때 주의
  - 정답은 '신고'뿐이다. 담당자가 점검 권장 자전거를 미리 손보면 그 자전거는 신고되지 않아 모형이 틀린 것처럼 보인다
  - 2B 테스트의 상위 1,000대 적중률 12.4%(무작위 4.1%)와는 구간·집계가 달라 숫자를 그대로 견주지 않는다
  - 이력은 2026-09-28 주 점수부터 있다(그 전 주는 덮어써서 없다)

lightgbm 같은 무거운 패키지를 불러오지 않는다(tests/test_ops.py와 자동 시험이 이 파일만 불러 쓴다). weekly.py가 점수를 낸 뒤 부른다.
입력: ops.duckdb bike_risk_latest, reported_recent, fail_live   출력: 같은 DB의 risk_history, risk_eval
"""
import numpy as np

QUIET_DAYS = 14          # 최근 이만큼 안에 신고된 자전거는 새 모형의 대상에서 뺀다(학습·채점 때와 같음)


def repair_grade(n_trips, days_since_rep, prob, cut):
    """새 모형 점수로 화면 등급을 매긴다. 대상은 그 주에 대여됐고 최근 14일 안에 신고가 없던 자전거뿐이다
    (대여되지 않은 자전거는 이미 수거됐을 수 있고, 최근 신고된 자전거는 신고 목록에 따로 나온다. 결정 로그 2026-10-23).
    입력: 같은 길이의 배열들(대여 수, 마지막 신고 뒤 일수(없으면 NaN), 다음 주 수리 확률), 등급 기준 백분위 cut.
    반환: (대상 여부, 대상 평균 대비 배수, 대상 안 백분위, 등급) — 대상이 아니면 배수·백분위는 NaN, 등급은
    '최근 신고'(대여는 됐지만 최근 신고) 또는 '산정 제외'(그 주 대여 없음)"""
    n_trips, ds, prob = (np.asarray(x, dtype=float) for x in (n_trips, days_since_rep, prob))
    rented = n_trips > 0
    quiet = np.isnan(ds) | (ds > QUIET_DAYS)
    ok = rented & quiet
    avg = np.nanmean(np.where(ok, prob, np.nan)) if ok.any() else np.nan
    ratio = np.where(ok, prob / avg, np.nan)
    pct = np.full(len(prob), np.nan)
    if ok.any():
        r = prob[ok].argsort().argsort()
        pct[ok] = (r + 1) / ok.sum()
    grade = np.select([~rented, ~quiet, pct >= cut["점검 권장"], pct >= cut["주의"], pct >= cut["보통"]],
                      ["산정 제외", "최근 신고", "점검 권장", "주의", "보통"], default="낮음")
    return ok, ratio, pct, grade


KINDS = {"타이어": "share_tire", "체인": "share_chain", "안장": "share_saddle", "페달": "share_pedal"}


def save_history(con):
    """지금의 bike_risk_latest를 risk_history에 넣는다. grade·risk_ratio에는 위험 모형(신고로 학습)의 값을 넣어 채점을 이어 가고,
    새 모형(화면 점검 권장)의 값은 repair_grade·repair_ratio에 넣는다. 반환: (주, 넣은 줄 수)"""
    con.execute("""CREATE TABLE IF NOT EXISTS risk_history (wk DATE, bike_id INTEGER, grade VARCHAR, risk_ratio DOUBLE, prob_next DOUBLE,
                   recently_reported BOOLEAN, model_version VARCHAR, scored_at TIMESTAMP)""")
    for c, typ in (("repair_grade", "VARCHAR"), ("repair_ratio", "DOUBLE")):          # 2026-10-23에 더한 열. 이전 이력은 비어 있다
        con.execute(f"ALTER TABLE risk_history ADD COLUMN IF NOT EXISTS {c} {typ}")
    wk = con.execute("SELECT CAST(max(wk) AS DATE) FROM bike_risk_latest").fetchone()[0]
    con.execute("DELETE FROM risk_history WHERE wk = ?", [wk])
    rep = "bike_id IN (SELECT bike_id FROM reported_recent)" if con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = 'reported_recent'").fetchone()[0] else "FALSE"
    con.execute(f"""INSERT INTO risk_history (wk, bike_id, grade, risk_ratio, prob_next, recently_reported, model_version, scored_at, repair_grade, repair_ratio)
                    SELECT CAST(wk AS DATE), bike_id, report_grade, report_ratio, prob_next, {rep}, model_version, now(), grade, risk_ratio
                    FROM bike_risk_latest WHERE CAST(wk AS DATE) = ?""", [wk])
    return wk, con.execute("SELECT count(*) FROM risk_history WHERE wk = ?", [wk]).fetchone()[0]


def evaluate(con):
    """risk_history의 주마다 그다음 주 실제 신고로 채점해 risk_eval을 다시 만든다(작은 표라 매번 통째로). 반환: risk_eval 표"""
    shares = ", ".join(f"avg((f.kind = '{k}')::INT) AS {v}" for k, v in KINDS.items())
    con.execute(f"""
    CREATE OR REPLACE TABLE risk_eval AS
    WITH last AS (SELECT max(reg_ts) AS ts FROM fail_live),
    h AS (SELECT * FROM risk_history, last WHERE grade <> '산정 제외' AND CAST(wk AS TIMESTAMP) + INTERVAL 14 DAY <= last.ts),      -- 다음 주가 다 지난 주만
    hit AS (SELECT DISTINCT h.wk, h.bike_id FROM h JOIN fail_live f
            ON f.bike_id = h.bike_id AND f.reg_ts >= CAST(h.wk AS TIMESTAMP) + INTERVAL 7 DAY AND f.reg_ts < CAST(h.wk AS TIMESTAMP) + INTERVAL 14 DAY),
    r AS (SELECT h.*, (hit.bike_id IS NOT NULL)::INT AS y, row_number() OVER (PARTITION BY h.wk ORDER BY h.risk_ratio DESC) AS rk
          FROM h LEFT JOIN hit ON h.wk = hit.wk AND h.bike_id = hit.bike_id),
    s AS (SELECT wk, count(*) AS n_ranked, avg(y) AS base_rate, avg(y) FILTER (WHERE rk <= 1000) AS hit_top1000,
                 count(*) FILTER (WHERE grade = '점검 권장' AND NOT recently_reported) AS n_insp,
                 avg(y) FILTER (WHERE grade = '점검 권장' AND NOT recently_reported) AS hit_insp, any_value(model_version) AS model_version
          FROM r GROUP BY 1),
    k AS (SELECT s.wk, {shares} FROM s JOIN fail_live f
          ON f.reg_ts >= CAST(s.wk AS TIMESTAMP) + INTERVAL 7 DAY AND f.reg_ts < CAST(s.wk AS TIMESTAMP) + INTERVAL 14 DAY GROUP BY 1)
    SELECT s.wk, s.wk + 7 AS target_week, n_ranked, base_rate, hit_top1000, hit_top1000 / nullif(base_rate, 0) AS lift_top1000,
           n_insp, hit_insp, {", ".join(KINDS.values())}, model_version, now() AS evaluated_at
    FROM s LEFT JOIN k USING (wk) ORDER BY 1""")
    return con.sql("SELECT * FROM risk_eval ORDER BY wk").df()


WEEKS, LIFT_MIN, LIFT_OK = 8, 2.5, 2.7          # 2B 테스트: 모형 3.0배, 주행량만 본 기준 2.7배. 2.5 = 그 기준보다 못한 수준(주별 흔들림 여유)


def retrain_note(ev):
    """위험 모형을 다시 만들 때가 됐는지 본다(사람 결정 2026-10-10: AI 제안대로). 반환: 알릴 글 또는 None

    지표는 상위 1,000대 적중률의 무작위 대비 배수(lift_top1000)의 8주 평균이다.
    - 채점이 8주 쌓일 때까지는 숫자만 쌓는다
    - 딱 8주가 됐을 때 한 번 알린다: 처음 8주 평균이 2.7배 이상이면 기준 2.5배를 그대로 쓰고,
      그보다 낮으면(운영 채점은 테스트와 집계가 달라 처음부터 낮게 나올 수 있다) 사람이 기준을 다시 정한다
    - 그 뒤로는 처음 8주 평균이 2.7배 이상이었던 경우에만, 최근 8주 평균이 2.5배 아래로 내려가면 알린다
    담당자가 추천 자전거를 미리 손보기 시작하면 신고가 줄어 배수가 낮아진다. 그때는 이 조건을 그대로 쓰지 않는다"""
    lift = ev.sort_values("wk").lift_top1000.dropna()
    if len(lift) < WEEKS:
        return None
    first, recent = lift.head(WEEKS).mean(), lift.tail(WEEKS).mean()
    if len(lift) == WEEKS:
        tail = f"기준 {LIFT_MIN}배를 그대로 쓴다" if first >= LIFT_OK else f"{LIFT_OK}배보다 낮아 재학습 기준을 다시 정해야 한다"
        return f"위험 모형 채점이 {WEEKS}주 쌓였다: 상위 1,000대 적중률이 무작위 대비 평균 {first:.1f}배. {tail}(docs/pipeline.md 재학습 기준)"
    if first >= LIFT_OK and recent < LIFT_MIN:
        return f"위험 모형 재학습 검토: 최근 {WEEKS}주 상위 1,000대 적중률이 무작위 대비 평균 {recent:.1f}배로 기준 {LIFT_MIN}배 아래(docs/pipeline.md 재학습 기준)"
    return None

