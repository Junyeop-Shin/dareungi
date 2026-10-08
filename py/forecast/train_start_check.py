"""대여량 모형 학습 시작 변경(2021-04-08 → 2022-05-01)을 테스트 뒤 자료로 한 번 확인한다.

변경은 2024 검증 구간 근거로 정했다(rb_6_train_start_valid.csv, 결정 로그 2026-10-08). 이 스크립트는 결정 뒤에
테스트 구간 뒤 주(2026-07-06 ~ 09-28 월요일, 13주)에서 두 설정을 한 번 비교해 결과를 그대로 기록한다.
두 설정 모두 2024-12-31까지로 맞추고, 신고율(최근 4주)과 실적은 운영 DB daily_district, 날씨는 1C 과거 예보 파일을 쓴다.

입력: data/processed/daily_master_v2.parquet, data/interim/weather_forecast/kma_by_origin.parquet, ops.duckdb daily_district
출력: output/tables/rb_17_train_start_live.csv (부품별 MAE%, 치우침%)
실행: .venv/bin/python py/forecast/train_start_check.py
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parents[1]))   # py/ 공용 모듈
import fc                                       # noqa: E402
import fc_ops                                   # noqa: E402
from common import connect                      # noqa: E402
from fc_ops_fit import TEST_REFIT_END, fit_models   # noqa: E402

STARTS = {"2021-04-08(그전)": "2021-04-08", "2022-05-01(변경)": "2022-05-01"}


def main():
    m = fc.load_master()
    con = connect()
    d = fc_ops.daily_from_district(con.execute("SELECT * FROM daily_district").df())
    con.close()
    kma = pd.read_parquet(fc.ROOT / "data/interim/weather_forecast/kma_by_origin.parquet")
    bad, ns = fc_ops.master_flags()
    spec = {"normal_coef": fc_ops.normal_coef(m)}
    full = d.asfreq("D", fill_value=0)
    mondays = pd.date_range("2026-07-06", "2026-09-28", freq="7D")
    rows = []
    for label, start in STARTS.items():
        models = fit_models(m, TEST_REFIT_END, start=start)
        for o in mondays:
            r = fc_ops.forecast(d, o, kma[kma.origin == o].sort_values("lead"), models, spec, bad=bad, no_service=ns)
            act = full[(full.index >= o) & (full.index < o + pd.Timedelta(days=7))]
            for p, name in fc_ops.PARTS.items():
                for k in ("kma", "cal"):
                    rows.append({"학습 시작": label, "모형": fc_ops.METHODS[k], "부품": name, "week": o, "pred": r[k][p], "actual": act[p].sum()})
    t = pd.DataFrame(rows)
    out = t.groupby(["모형", "학습 시작", "부품"]).apply(lambda g: (g.pred - g.actual).abs().mean() / g.actual.mean() * 100, include_groups=False) \
           .unstack("부품")[list(fc_ops.PARTS.values())]
    bias = t.groupby(["모형", "학습 시작"]).apply(lambda g: (g.pred - g.actual).sum() / g.actual.sum() * 100, include_groups=False)
    out["치우침%(전 부품, +는 과대)"] = bias
    out["주 수"] = t.week.nunique()
    out.round(2).to_csv(fc.ROOT / "output/tables/rb_17_train_start_live.csv")
    pd.set_option("display.width", 200)
    print("2026-07-06 ~ 09-28 MAE%"); print(out.round(2).to_string())


if __name__ == "__main__":
    main()
