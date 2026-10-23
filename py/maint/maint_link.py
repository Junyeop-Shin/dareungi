"""신고와 정비 기록을 잇는 세 가지 기준을 자료로 살핀다(2023 ~ 2024년만).

1) 신고 뒤 작업으로 볼 기간: 신고 뒤 며칠째에 첫 작업이 오는지 날짜별 분포를, 신고와 무관하게 아무 날에
   작업이 올 확률(기준선)과 견준다. 기준선보다 높은 날까지가 "신고 때문에 생긴 작업"이 몰리는 구간이다.
2) 유형 대응표: 유형이 하나뿐인 신고 뒤 14일 안의 첫 작업에 어떤 항목·부품이 들었는지를 유형끼리 견준다.
   배수 = 그 유형 신고 뒤 작업에서 그 항목이 든 비율 ÷ 모든 신고 뒤 작업에서 그 항목이 든 비율.
   어느 유형에서만 배수가 높으면 그 항목은 그 유형의 신고와 짝이라고 본다(짝을 정하는 기준값은 사람이 정한다).
3) 점검과 정비의 차이: 같은 자전거·같은 시각에 정비가 함께 있는 점검(점검+정비)과 점검만 있는 작업이
   앞선 7일 안에 신고가 있던 비율이 다른지 본다. 점검만 있는 작업이 신고와 덜 이어지면, 신고 없이 들여다본
   작업(정기·순회)이 많다는 뜻이다.

왜 2023 ~ 2024년만: 다음 단계(A)에서 2025년 이후를 채점할 수 있으므로, 그 기간을 보고 기준을 정하지 않는다.
신고는 48시간 중복 병합 뒤(keep)를 쓴다. 유형별 비교는 유형이 하나뿐인 신고(88%)만 쓴다.

입력: data/interim/maint/maint_rows.parquet, maint_jobs.parquet, data/processed/fail_std.parquet
출력: output/tables/maint_gap_dist.csv     신고 뒤 d일째 첫 작업 비율과 기준선(공단 정비, 수리점)
      output/tables/maint_gap_summary.csv  신고 뒤 첫 작업까지의 대표값(중앙값, 사분위, 기준선을 넘는 마지막 날)
      output/tables/maint_map_lift.csv     유형 × 항목(부품) 배수
      output/tables/maint_kind_trigger.csv 점검만 / 점검+정비 / 수리점 작업이 앞선 7일 안에 신고가 있던 비율
실행: .venv/bin/python py/maint/maint_link.py
"""
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
START, END = pd.Timestamp("2023-01-01"), pd.Timestamp("2025-01-01")
TYPES = ["타이어", "체인", "안장", "페달", "단말기", "기타"]
MAXD = 60


def first_after(rep, jobs, days):
    """신고마다 같은 자전거의 다음 작업(신고 시각 이후 첫 작업)까지 걸린 일수. 없으면 NaN"""
    x = pd.merge_asof(rep.sort_values("reg_ts"), jobs.sort_values("done")[["bike_id", "done"]],
                      left_on="reg_ts", right_on="done", by="bike_id", direction="forward")
    gap = (x.done - x.reg_ts).dt.total_seconds() / 86400
    return x.assign(gap=gap.where(gap <= days))


def main():
    rows = pd.read_parquet(ROOT / "data/interim/maint/maint_rows.parquet")
    jobs = pd.read_parquet(ROOT / "data/interim/maint/maint_jobs.parquet")
    rows, jobs = rows[(rows.done >= START) & (rows.done < END)], jobs[(jobs.done >= START) & (jobs.done < END)]
    f = pd.read_parquet(ROOT / "data/processed/fail_std.parquet")
    f = f[f.keep & (f.reg_ts >= START) & (f.reg_ts < END - pd.Timedelta(days=MAXD))]   # 끝에서 60일은 뒤를 볼 수 없어 뺀다
    f = f.assign(bike_id=f.bike.astype("int64"), reg_ts=f.reg_ts.astype("datetime64[ns]"))
    rep = f[["bike_id", "reg_ts"]].drop_duplicates()
    bikes = jobs.bike_id.nunique()
    ndays = (END - START).days

    # 1) 신고 뒤 첫 작업까지의 일수 분포와 기준선
    dist, summ = [], []
    for k in ("정비", "수리점"):
        j = jobs[jobs.kind == k]
        x = first_after(rep, j, MAXD)
        base = j.groupby("bike_id").done.apply(lambda s: s.dt.normalize().nunique()).sum() / (bikes * ndays)   # 아무 날에 그 자전거 작업이 있을 확률
        if k == "수리점":
            base = base * ndays / (ndays * 9 / 12)       # 수리점은 3 ~ 11월 9개월쯤만 운영하므로 운영하는 날 기준으로 맞춘다
        d = x.gap.dropna().astype(int).value_counts().reindex(range(MAXD + 1), fill_value=0) / len(x)
        for day, v in d.items():
            dist.append({"구분": k, "신고 뒤 d일째": day, "첫 작업 비율": round(v, 5), "기준선(하루)": round(base, 5), "기준선 대비": round(v / base, 2)})
        over = d[d > 2 * base].index                     # 기준선의 2배를 넘는 날
        g = x.gap.dropna()
        summ.append({"구분": k, "신고": len(x), f"{MAXD}일 안 작업 비율": round(g.size / len(x), 3), "중앙값(일)": round(g.median(), 1),
                     "사분위 25%": round(g.quantile(.25), 1), "사분위 75%": round(g.quantile(.75), 1),
                     "3일 안": round((g <= 3).sum() / len(x), 3), "7일 안": round((g <= 7).sum() / len(x), 3), "14일 안": round((g <= 14).sum() / len(x), 3),
                     "기준선(하루)": round(base, 5), "기준선 2배를 넘는 마지막 날": int(over.max()) if len(over) else None})
    pd.DataFrame(dist).to_csv(ROOT / "output/tables/maint_gap_dist.csv", index=False)
    summ = pd.DataFrame(summ)
    summ.to_csv(ROOT / "output/tables/maint_gap_summary.csv", index=False)

    # 2) 유형 대응: 유형이 하나뿐인 신고 → 14일 안 첫 작업의 항목
    one = f.groupby(["bike_id", "reg_ts"]).kind.agg(lambda s: s.iloc[0] if s.nunique() == 1 else None).dropna().reset_index()
    one = one[one.kind.isin(TYPES)]
    lift = []
    for k in ("정비", "수리점"):
        j = jobs[jobs.kind == k]
        x = first_after(one, j, 14).dropna(subset=["gap"])
        items = rows[rows.kind == k][["bike_id", "done", "item"]].drop_duplicates()
        xi = x.merge(items, on=["bike_id", "done"])
        share = xi.groupby(["kind", "item"]).size().unstack(0).fillna(0) / x.groupby("kind").size()   # 그 유형 신고 뒤 작업 가운데 항목이 든 비율
        overall = xi.groupby("item").size() / len(x)
        for item in share.index:
            for t in TYPES:
                if t in share.columns:
                    lift.append({"구분": k, "항목": item, "신고 유형": t, "작업에 든 비율": round(share.loc[item, t], 3),
                                 "배수": round(share.loc[item, t] / overall[item], 2), "그 유형 신고 뒤 작업 수": int((x.kind == t).sum())})
    pd.DataFrame(lift).to_csv(ROOT / "output/tables/maint_map_lift.csv", index=False)

    # 3) 점검만 / 점검+정비 / 수리점: 앞선 7일 안에 신고가 있던 비율
    p = jobs[jobs.kind == "점검"][["bike_id", "done", "n_items"]]
    q = jobs[jobs.kind == "정비"][["bike_id", "done"]].assign(has=1)
    p = p.merge(q, how="left", on=["bike_id", "done"])
    groups = {"점검만(정비 없음)": p[p.has.isna()], "점검+정비": p[p.has.notna()], "수리점": jobs[jobs.kind == "수리점"][["bike_id", "done", "n_items"]]}
    kt = []
    for name, g in groups.items():
        y = pd.merge_asof(g.sort_values("done"), rep.sort_values("reg_ts"), left_on="done", right_on="reg_ts", by="bike_id", direction="backward")
        lag = (y.done - y.reg_ts).dt.total_seconds() / 86400
        kt.append({"작업": name, "작업 수": len(g), "앞선 7일 안 신고 비율": round((lag <= 7).mean(), 3),
                   "앞선 1일 안 신고 비율": round((lag <= 1).mean(), 3), "작업당 항목 수": round(g.n_items.mean(), 1)})
    kt = pd.DataFrame(kt)
    kt.to_csv(ROOT / "output/tables/maint_kind_trigger.csv", index=False)

    print(summ.to_string()); print(kt.to_string())
    lf = pd.DataFrame(lift)
    print(lf[lf["배수"] >= 1.5].sort_values(["구분", "신고 유형", "배수"], ascending=[True, True, False]).to_string())


if __name__ == "__main__":
    main()
