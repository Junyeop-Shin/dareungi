"""주간 예측 방식 비교: 테스트 구간(~2026-06) 뒤에 실제 결과가 나온 주(2026-07 ~).

forecast_track(actuals.py)의 예보판·달력판·나이브에, 2026-10-08까지 쓰던 '최근 4주 신고율 × 지난주 대여량'을
같은 daily_district로 다시 계산해 붙인다. 주가 적어(13주 안팎) 참고용이다. 교체 판단은 2026-10-12 이후 운영 주로 한다.

입력: ops.duckdb forecast_track, daily_district
출력: output/tables/fc_ops_compare_live.csv
실행: .venv/bin/python py/fc_ops_compare.py
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parent))
import fc_ops                      # noqa: E402
from common import ROOT, connect   # noqa: E402

FROM = "2026-07-06"                # 테스트 구간 뒤 첫 월요일


def main():
    con = connect()
    t = con.execute("SELECT * FROM forecast_track WHERE actual IS NOT NULL AND week >= ?", [FROM]).df()
    t["week"] = pd.to_datetime(t.week)
    d = fc_ops.daily_from_district(con.execute("SELECT * FROM daily_district").df()).asfreq("D", fill_value=0)
    con.close()
    old = []
    for w in pd.to_datetime(t.week.unique()):
        lvl = d[(d.index >= w - pd.Timedelta(days=35)) & (d.index < w - pd.Timedelta(days=7))]
        last = d[(d.index >= w - pd.Timedelta(days=7)) & (d.index < w)]
        old += [{"week": w, "part": n, "pred_old": lvl[p].sum() / lvl.rentals.sum() * last.rentals.sum()} for p, n in fc_ops.PARTS.items()]
    t = t.merge(pd.DataFrame(old), on=["week", "part"])
    cols = {"naive": "나이브(지난주 그대로)", "pred_old": "그전 방식(× 지난주 대여량)", "pred_cal": "달력판", "pred_kma": "예보판"}
    tab = pd.DataFrame({lab: t.groupby("part").apply(lambda g: (g[c] - g.actual).abs().mean() / g.actual.mean() * 100, include_groups=False)
                        for c, lab in cols.items()}).T[list(fc_ops.PARTS.values())].round(2)
    tab["주 수"] = t.week.nunique()
    tab.to_csv(ROOT / "output/tables/fc_ops_compare_live.csv")
    print(f"{t.week.min()} ~ {t.week.max()} ({t.week.nunique()}주) MAE%"); print(tab.to_string())


if __name__ == "__main__":
    main()
