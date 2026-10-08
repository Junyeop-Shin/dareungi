"""대여소를 이용 시간대 패턴으로 군집한다(런북 05, 2A-6).

대여소마다 요일×시간 168칸의 대여 비율과 반납 비율(합쳐 336차원)을 만들어 KMeans로 묶는다.
검증·테스트 기간 정보가 섞이지 않도록 학습 기간(2025-06-30까지, 런북 06 2B-1) 통행만 쓴다.
군집별 평균 프로파일(평일 출퇴근 시간 대여·반납 비중, 주말 낮 비중, 왕복 비중, 거리 중앙값)을 표로 내서
이름을 붙일 때 근거로 쓴다. 대략 평일 출근 시간 반납이 많으면 업무지구, 대여가 많으면 주거지,
주말 낮·왕복이 많으면 여가, 거리가 짧으면 환승으로 읽는다.

군집 수는 4~6 중에서 프로파일을 보고 고른다(지금은 5, 29_panel.py의 CLUSTER_K). 여기서는 셋 다 만든다.

입력: DuckDB trips, station
출력: data/interim/station_cluster.csv (st_no, cluster_k4~k6), output/tables/panel_2a6_cluster_profile.csv
실행: .venv/bin/python py/26_station_clusters.py  (1~2분)
"""
import sys
from pathlib import Path

import pandas as pd
from sklearn.cluster import KMeans

sys.path.append(str(Path(__file__).resolve().parent))
from db import connect   # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
TRAIN_END = "2025-07-01"


def main():
    con = connect()
    prof = con.sql(f"""
    SELECT st, dir, isodow(ts) * 100 + hour(ts) AS slot, count(*) AS n FROM (
      SELECT rent_st AS st, 'out' AS dir, rent_dt AS ts FROM trips WHERE rent_dt < TIMESTAMP '{TRAIN_END}'
      UNION ALL
      SELECT rtn_st, 'in', rtn_dt FROM trips WHERE rtn_dt < TIMESTAMP '{TRAIN_END}' AND rtn_dt IS NOT NULL
    ) GROUP BY ALL""").df()
    wide = prof.pivot_table(index="st", columns=["dir", "slot"], values="n", fill_value=0)
    tot = wide.sum(axis=1)
    wide = wide[tot >= 1000]                                  # 건수가 적으면 프로파일이 잡음이라 뺀다
    share = wide.div(wide.sum(axis=1), axis=0)
    # 군집 해석용 대여소별 요약 지표
    cols = share.columns
    def pick(dir_, dows, hours):
        c = [x for x in cols if x[0] == dir_ and (x[1] // 100) in dows and (x[1] % 100) in hours]
        return share[c].sum(axis=1)
    wk, we = range(1, 6), range(6, 8)
    feat = pd.DataFrame({
        "am_out": pick("out", wk, range(7, 10)), "am_in": pick("in", wk, range(7, 10)),
        "pm_out": pick("out", wk, range(17, 20)), "pm_in": pick("in", wk, range(17, 20)),
        "weekend": pick("out", we, range(0, 24)) + pick("in", we, range(0, 24)),
        "weekend_day": pick("out", we, range(10, 18)) + pick("in", we, range(10, 18)),
    })
    rt = con.sql(f"SELECT rent_st AS st, avg(round_trip::INT) AS round_share, median(use_m) AS m_med FROM trips WHERE rent_dt < TIMESTAMP '{TRAIN_END}' GROUP BY 1").df().set_index("st")
    feat = feat.join(rt)
    out = pd.DataFrame({"st_no": share.index})
    profiles = []
    for k in (4, 5, 6):
        km = KMeans(n_clusters=k, random_state=42, n_init=10).fit(share.to_numpy())
        out[f"cluster_k{k}"] = km.labels_
        g = feat.assign(cluster=km.labels_).groupby("cluster").agg(n_st=("am_out", "size"), am_out=("am_out", "mean"), am_in=("am_in", "mean"),
                                                                  pm_out=("pm_out", "mean"), pm_in=("pm_in", "mean"), weekend=("weekend", "mean"),
                                                                  weekend_day=("weekend_day", "mean"), round_share=("round_share", "mean"), m_med=("m_med", "median"))
        g["am_in_minus_out"] = g.am_in - g.am_out
        profiles.append(g.reset_index().assign(k=k))
    out.to_csv(ROOT / "data/interim/station_cluster.csv", index=False)
    prof_tab = pd.concat(profiles, ignore_index=True)
    prof_tab.round(4).to_csv(ROOT / "output/tables/panel_2a6_cluster_profile.csv", index=False)
    pd.set_option("display.width", 250)
    print("대여소", len(share), "곳 (학습 기간 대여+반납 1,000건 이상)")
    print(prof_tab.round(3).to_string(index=False))


if __name__ == "__main__":
    main()
