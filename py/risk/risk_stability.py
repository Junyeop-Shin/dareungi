"""위험 모형 순위의 안정성 점검: 검증 구간(2025-07 ~ 12)에서 달마다, 자치구마다 성적이 고른가.

점검 대상 화면은 매주 위험률 상위 자전거를 보여 준다. 평균 적중률이 좋아도 특정 달에만 맞거나,
특정 자치구 자전거만 상위에 몰리면 현장에서 쓰기 어렵다. 그래서 두 가지를 본다.
  1. 달별 precision@1000(주 평균)과 가장 나은 베이스라인(B3 최근 13주 신고)의 같은 값
  2. 자치구별: 주마다 상위 1,000대에 든 비율(배정), 다음 주 신고 비율(필요), 상위에 든 자전거의 적중률
     자치구는 그 주 마지막 반납 대여소의 자치구(운영 화면의 last_st와 같은 규칙)
  3. 자치구별 변수 기여도(SHAP, LightGBM pred_contrib): 많이 배정되는 구와 적게 배정되는 구의 위험률 차이가
     어느 변수에서 나오는지
  4. 속도(speed_med)가 무엇을 잡는지: 속도 10분위별 다음 주 신고, 여가·왕복 통행 비중, 부품 구성, SHAP
     속도 = 이용거리 / 이용시간(대여 중 세워 둔 시간 포함)이라 고장 신호인지 이용 방식인지 가려 본다
  5. 지난 신고 이후(km_since_rep, days_since_rep) 구간별 다음 주 신고: 자전거당, 1,000km당, 대여 1,000건당
     신고가 km에 비례하는지 대여 횟수에 비례하는지 보려고 다음 주 대여 수(n_trips_next)를 붙인다.
     검증 마지막 주는 다음 주가 테스트 구간이라 뺀다. 지난 신고 뒤 48시간 넘게 대여가 없었는지(last_repaired = 1, 수거·수리
     추정)로도 나눠, 고친 것으로 보이는 자전거도 다시 신고되는지 본다

모형은 검증 단계 모형(output/models/risk_all.txt, 학습 구간으로만 학습)을 쓴다. 최종 모형(*_final)은 검증 구간까지
학습해서 여기서 채점하면 안 된다. 조기 종료에 검증 구간을 썼으므로 성적은 조금 낙관적이다. 테스트 구간은 보지 않는다.

입력: data/processed/panel/bike_week.parquet, output/models/risk_all.txt, 분석 DB trips_seq·station(읽기 전용)
출력: output/tables/rb_8_risk_month.csv, rb_9_risk_district.csv, rb_10_risk_shap_district.csv, rb_10_risk_group_usage.csv,
      rb_11_risk_speed.csv, rb_12_risk_since_rep.csv
실행: .venv/bin/python py/risk/risk_stability.py  (2 ~ 3분)
"""
import sys
from pathlib import Path

import duckdb
import lightgbm as lgb
import numpy as np
import pandas as pd

sys.path.append(str(Path(__file__).resolve().parents[1]))   # py/ 공용 모듈
from risk_eval import at_k, load_panel   # noqa: E402
from risk_lgb import prep, score         # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
K = 1000
OVER = ["금천구", "중구", "종로구", "구로구", "서초구"]     # rb_9에서 배정 / 신고 비가 1.4 이상
UNDER = ["광진구", "서대문구", "강동구", "노원구"]          # 0.75 미만


def last_district(weeks):
    """검증 주마다 자전거의 그 주 마지막 반납 대여소 자치구"""
    con = duckdb.connect(str(ROOT / "data/db/dareungi.duckdb"), read_only=True)
    a, b = min(weeks), max(weeks) + pd.Timedelta(days=7)
    d = con.execute("""
        SELECT t.bike_id, CAST(date_trunc('week', t.rent_dt) AS DATE) AS wk, arg_max(s.district, t.rent_dt) AS district
        FROM trips_seq t LEFT JOIN station s ON t.rtn_st = s.st_no
        WHERE t.rent_dt >= ? AND t.rent_dt < ? GROUP BY 1, 2""", [a, b]).df()
    con.close()
    d["wk"] = pd.to_datetime(d.wk)
    return d


def main():
    p = prep(load_panel()).sort_values(["bike_id", "wk"])
    p["n_trips_next"] = p.groupby("bike_id").n_trips.shift(-1)          # 패널은 자전거마다 빈 주까지 채운 격자라 바로 다음 행 = 다음 주
    va = p[p.split == "valid"].copy()
    model = lgb.Booster(model_file=str(ROOT / "output/models/risk_all.txt"))
    va["risk_rate"], va["risk_count"] = score(model, va)          # risk_count는 다음 주 확률과 순서가 같다

    # 1. 달별
    rows = []
    for mth, g in va.groupby(va.wk.dt.to_period("M")):
        r = {"달": str(mth), "주 수": g.wk.nunique()}
        for col, lab in (("risk_rate", "모형"), ("rep_13w", "B3 최근 13주 신고")):
            r[f"precision@{K} {lab}"] = float(at_k(g.assign(**{col: g[col].fillna(-np.inf)}), col, K)["precision"])
        r["그 달 다음 주 신고 비율(전체 자전거)"] = float((g.y_next > 0).mean())
        rows.append(r)
    month = pd.DataFrame(rows).set_index("달")
    month.round(4).to_csv(ROOT / "output/tables/rb_8_risk_month.csv")

    # 2. 자치구별
    va = va.merge(last_district(va.wk.unique()), on=["bike_id", "wk"], how="left")
    va["district"] = va.district.fillna("(위치 미상)")
    va["top"] = va.groupby("wk").risk_rate.rank(ascending=False, method="first") <= K
    va["top_p"] = va.groupby("wk").risk_count.rank(ascending=False, method="first") <= K
    va["hit"] = va.y_next > 0
    g = va.groupby("district")
    dist = pd.DataFrame({"자전거-주": g.size(), "상위 배정 비율": g.top.sum() / va.top.sum(),
                         "다음 주 신고 비율": g.hit.sum() / va.hit.sum(),
                         "상위 안 적중률": va[va.top].groupby("district").hit.mean(),
                         "그 구 전체 신고율": g.hit.mean()})
    dist["배정 / 신고 비"] = dist["상위 배정 비율"] / dist["다음 주 신고 비율"]
    dist["상위 배정 비율(확률 순)"] = g.top_p.sum() / va.top_p.sum()
    dist["배정 / 신고 비(확률 순)"] = dist["상위 배정 비율(확률 순)"] / dist["다음 주 신고 비율"]
    dist["상위 안 적중률(확률 순)"] = va[va.top_p].groupby("district").hit.mean()
    dist = dist.sort_values("다음 주 신고 비율", ascending=False)
    dist.round(4).to_csv(ROOT / "output/tables/rb_9_risk_district.csv")

    # 3. 자치구별 변수 기여도
    feats = model.feature_name()
    smp = va[va.district != "(위치 미상)"].sample(300_000, random_state=42)
    c = model.predict(smp[feats], pred_contrib=True)[:, :-1]
    shap = pd.DataFrame(c, columns=feats).assign(district=smp.district.to_numpy()).groupby("district").mean()
    shap.round(4).to_csv(ROOT / "output/tables/rb_10_risk_shap_district.csv")
    gap = (shap.loc[OVER].mean() - shap.loc[UNDER].mean()).sort_values(key=abs, ascending=False)
    grp = smp.assign(그룹=np.where(smp.district.isin(OVER), "많이 배정", np.where(smp.district.isin(UNDER), "적게 배정", "나머지")))
    use = grp.groupby("그룹")[["km", "km_4w", "n_trips", "minutes", "speed_med", "sh_leisure"]].median()
    use.round(3).to_csv(ROOT / "output/tables/rb_10_risk_group_usage.csv")

    # 4. 속도
    # 마지막 주는 다음 주(테스트 구간) 대여 수가 없어 신고만 세지고 분모가 빠진다. 5절처럼 뺀다
    v = va[va.speed_med.notna() & (va.n_trips > 0) & (va.wk < va.wk.max())].copy()
    v["dec"] = pd.qcut(v.speed_med, 10, labels=False) + 1
    sv = smp[smp.speed_med.notna() & (smp.n_trips > 0)]
    sv_dec = pd.cut(sv.speed_med, pd.qcut(v.speed_med, 10, retbins=True)[1], labels=False, include_lowest=True) + 1
    sp_shap = pd.Series(c[sv.index.map(dict(zip(smp.index, range(len(smp))))).to_numpy(), feats.index("speed_med")], index=sv.index).groupby(sv_dec.to_numpy()).mean()
    km = v.km_next.where(v.km_next > 0)
    speed = v.assign(km_next_pos=km).groupby("dec").agg(
        속도_중앙=("speed_med", "median"), 자전거_주=("bike_id", "size"),
        다음주_신고_비율=("y_next", lambda x: (x > 0).mean()), 여가_비중=("sh_leisure", "mean"), 왕복_비중=("sh_round", "mean"),
        건당_분=("minutes", "sum"), 대여=("n_trips", "sum"), 타이어=("y_tire_next", "sum"), 체인=("y_chain_next", "sum"),
        안장=("y_saddle_next", "sum"), 페달=("y_pedal_next", "sum"), 신고=("y_next", "sum"), km_next=("km_next_pos", "sum"),
        대여_next=("n_trips_next", "sum"))
    speed["건당_분"] = speed.건당_분 / speed.대여
    speed["1000km당_신고"] = speed.신고 / speed.km_next * 1000
    speed["대여1000건당_신고"] = speed.신고 / speed.대여_next * 1000
    tot = speed[["타이어", "체인", "안장", "페달"]].sum(axis=1)       # 나누기 전에 합을 먼저 잡는다
    for q in ("타이어", "체인", "안장", "페달"):
        speed[q] = speed[q] / tot
    speed["속도_SHAP_평균"] = sp_shap
    speed = speed.drop(columns=["대여", "신고", "km_next", "대여_next"])

    # 5. 지난 신고 이후
    w = va[va.wk < va.wk.max()].copy()                                     # 다음 주가 테스트 구간인 마지막 주는 뺀다
    w["km_bin"] = np.where(w.left_censored == 1, "신고 기록 없음",
                           pd.cut(w.km_since_rep, [-0.1, 10, 50, 150, 400, np.inf], labels=["0~10km", "10~50", "50~150", "150~400", "400km 넘음"]).astype(str))
    w["day_bin"] = np.where(w.left_censored == 1, "신고 기록 없음",
                            pd.cut(w.days_since_rep, [-1, 7, 28, 91, np.inf], labels=["1주 안", "1~4주", "1~3개월", "3개월 넘음"]).astype(str))
    def rates(g):
        return pd.Series({"자전거_주": len(g), "다음주_신고_비율": (g.y_next > 0).mean(), "다음주_km_중앙": g.km_next.median(),
                          "1000km당": g.y_next.sum() / g.km_next.sum() * 1000, "대여1000건당": g.y_next.sum() / g.n_trips_next.sum() * 1000,
                          "위험률_중앙": g.risk_rate.median()})
    w["rep_state"] = w.last_repaired.map({1.0: "48시간 넘게 쉼(수리 추정)", 0.0: "48시간 안에 다시 대여"}).fillna("신고 뒤 아직 대여 없음")
    rec = w[w.left_censored == 0]
    since = pd.concat([w.groupby("km_bin").apply(rates, include_groups=False).assign(구분="지난 신고 뒤 km"),
                       w.groupby("day_bin").apply(rates, include_groups=False).assign(구분="지난 신고 뒤 기간"),
                       rec.groupby(["rep_state", "day_bin"]).apply(rates, include_groups=False)
                          .set_axis([f"{a} · {b}" for a, b in rec.groupby(["rep_state", "day_bin"]).groups], axis=0).assign(구분="수리 추정 × 기간")])
    since.round(4).to_csv(ROOT / "output/tables/rb_12_risk_since_rep.csv")
    speed.round(4).to_csv(ROOT / "output/tables/rb_11_risk_speed.csv")

    pd.set_option("display.width", 220)
    print("달별 precision@1000"); print(month.round(3).to_string())
    print("\n자치구별(신고 많은 순)"); print(dist.round(3).to_string())
    known = dist.drop(index="(위치 미상)", errors="ignore")
    print("배정 / 신고 비가 1에서 벗어난 정도(25개 구, 평균 |log 비|): 위험률 순",
          round(float(np.abs(np.log(known["배정 / 신고 비"])).mean()), 3), "/ 확률 순", round(float(np.abs(np.log(known["배정 / 신고 비(확률 순)"])).mean()), 3))
    print(f"\n많이 배정되는 구({', '.join(OVER)}) − 적게 배정되는 구({', '.join(UNDER)}): 변수별 평균 SHAP 차이(로그 위험률)")
    print(gap.head(10).round(4).to_string()); print("합계", round(float(gap.sum()), 4))
    print("\n그룹별 이용량 중앙값"); print(use.round(2).to_string())
    print("\n속도 10분위(1 = 가장 느림)"); print(speed.round(3).to_string())
    print("\n지난 신고 이후 구간별"); print(since.round(3).to_string())


if __name__ == "__main__":
    main()
