"""B: 2025년에 신고가 준 만큼 실제 정비도 줄었는가. 신고와 정비·점검 작업 수를 반기별로 견준다.

왜: 레짐 원인 분석(docs/notes/04)은 헛대여를 고장 신호로 삼아 "2025년의 하락은 고장보다 신고 쪽 변화"로 보았다.
헛대여는 고장을 대신하는 가정이었는데, 정비 기록은 실제로 손본 기록이라 더 직접적인 견줄 거리다.
신고율이 0.83배로 내렸을 때 정비가 같이 줄었으면 고장이 준 것, 덜 줄었거나 늘었으면 신고 쪽 변화를 지지한다.
다만 정비 건수는 고장만이 아니라 인력·정기 점검 방식·수리점 사업 규모에도 따른다(해석할 때 단서).

기준은 기존 분석과 같다: 반기 단위 전년 같은 반기 비교(결정 로그 2026-10-07), 운영 중단일·신고 누락 기간은
신고와 정비 모두에서 뺀다(rc_sham.py와 같음). 비율은 대여 1만 건당.
2025년은 점검 기록이 없어 점검은 2023·2024·2026 상반기만 견준다.

유형 대응(사람이 정함 2026-10-23. 근거는 maint_link.py의 배수, 유형이 하나뿐인 신고 뒤 14일 안 첫 작업):
  공단(점검·정비 내역 10분류): 타이어 = 타이어.튜브 / 체인 = 체인. 안장·페달은 분류가 없어 견주지 않는다
  수리점(부품 41개): 타이어 = 전륜·후륜 타이어, 전륜·후륜 튜브 / 체인 = 체인 / 안장 = 안장, 싯포스트, 싯클램프, QR레버 /
  페달 = 페달, 크랭크암, 바텀브래킷. 휠셋(타이어 신고 뒤 0.82 ~ 0.88배), 체인커버(드물고 유형마다 엇갈림), 체인가드(6건)는 어느 유형에도 넣지 않는다
신고 뒤 작업으로 볼 기간: 14일(사람이 정함 2026-10-23. 원인과 상관없이 14일 안에 손봤으면 신고 뒤 작업으로 본다)

사후 분석이라 예측 모형 선택에는 쓰지 않는다.

입력: data/interim/maint/maint_rows.parquet, maint_jobs.parquet (py/maint/maint_prep.py), data/processed/daily_master_v2.parquet
출력: output/tables/maint_b_halfyear.csv  반기별 대여·신고·작업 수와 대여 1만 건당 비율
      output/tables/maint_b_ratios.csv    비교 기간별 비(신고, 공단 정비, 공단 점검, 수리점, 대여)
      output/tables/maint_b_parts.csv     유형별 신고 행 수와 그 유형이 든 작업 수의 비교 기간별 비
      output/tables/maint_b_trigger.csv   반기별 작업 가운데 앞선 N일(14일이 기준, 3·7일은 민감도) 안에 같은 자전거 신고가 있던 작업과 없던 작업
      output/figures/maint_b_monthly.png  월별 신고율과 작업률(대여 1만 건당)
실행: .venv/bin/python py/maint/maint_vs_reports.py
"""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

plt.rcParams["font.family"] = "AppleGothic"
plt.rcParams["axes.unicode_minus"] = False
ROOT = Path(__file__).resolve().parents[2]
KINDS = ["정비", "점검", "수리점"]
COMPARE = {"2024 상반기 vs 2023 상반기(하락 전)": ("2023상", "2024상"), "2024 하반기 vs 2023 하반기(하락 전)": ("2023하", "2024하"),
           "2025 상반기 vs 2024 상반기": ("2024상", "2025상"), "2025 하반기 vs 2024 하반기": ("2024하", "2025하"),
           "2026 상반기 vs 2025 상반기": ("2025상", "2026상"), "2026 상반기 vs 2024 상반기(2년)": ("2024상", "2026상")}
PARTS_GONG = {"타이어": {"타이어.튜브"}, "체인": {"체인"}}
PARTS_SHOP = {"타이어": {"전륜 타이어", "후륜 타이어", "전륜 튜브", "후륜 튜브"}, "체인": {"체인"},
              "안장": {"안장", "싯포스트", "싯클램프", "QR레버"}, "페달": {"페달", "크랭크암", "바텀브래킷"}}
REPORT_COL = {"타이어": "fail_tire", "체인": "fail_chain", "안장": "fail_saddle", "페달": "fail_pedal"}


def half(dt):
    """날짜 → '2025상' 같은 반기 이름"""
    return dt.dt.year.astype(str) + dt.dt.month.le(6).map({True: "상", False: "하"})


def main():
    m = pd.read_parquet(ROOT / "data/processed/daily_master_v2.parquet")
    m["date"] = pd.to_datetime(m.date)
    m = m[(m.date >= "2023-01-01") & (m.date <= "2026-06-30")]
    ok = m[(m.no_service == 0) & (m.report_anomaly == 0)]                  # 기존 분석과 같이 두 기간을 뺀다
    okdays = set(ok.date.dt.normalize())
    jobs = pd.read_parquet(ROOT / "data/interim/maint/maint_jobs.parquet")
    jobs = jobs[jobs.done.dt.normalize().isin(okdays)]
    rows = pd.read_parquet(ROOT / "data/interim/maint/maint_rows.parquet")
    rows = rows[rows.done.dt.normalize().isin(okdays)]

    # 반기별 표
    h = ok.assign(h=half(ok.date)).groupby("h")[["rentals", "total_faults", *REPORT_COL.values()]].sum()
    for k in KINDS:
        h[k] = jobs[jobs.kind == k].groupby(half(jobs[jobs.kind == k].done)).size()
    h.loc["2025상":"2025하", "점검"] = float("nan")                          # 2025년 점검 기록은 받지 못했다
    h["정비+수리점 자전거"] = (jobs[jobs.kind != "점검"].assign(h=lambda d: half(d.done))
                               .groupby("h").bike_id.nunique())
    for c in ["total_faults", *KINDS]:
        h[f"{c}_per10k"] = h[c] / h.rentals * 1e4
    h.to_csv(ROOT / "output/tables/maint_b_halfyear.csv")

    # 비교 기간별 비(P1 ÷ P0). 건수 비와 대여 1만 건당 비
    rr = []
    for name, (p0, p1) in COMPARE.items():
        r = {"비교": name, "대여 비": h.rentals[p1] / h.rentals[p0]}
        for c, lab in (("total_faults", "신고"), ("정비", "공단 정비"), ("점검", "공단 점검"), ("수리점", "수리점")):
            r[f"{lab} 건수 비"] = h[c][p1] / h[c][p0]
            r[f"{lab} 대여당 비"] = h[f"{c}_per10k"][p1] / h[f"{c}_per10k"][p0]
        rr.append(r)
    ratios = pd.DataFrame(rr).round(3)
    ratios.to_csv(ROOT / "output/tables/maint_b_ratios.csv", index=False)

    # 유형별: 신고 행 수(유형별은 행 수로 센다) 대 그 유형 항목이 든 작업 수
    rows["h"] = half(rows.done)
    pr = []
    for part, col in REPORT_COL.items():
        rep = ok.assign(h=half(ok.date)).groupby("h")[col].sum()
        series = {"신고": rep}
        if part in PARTS_GONG:
            g = rows[(rows.kind == "정비") & rows["item"].isin(PARTS_GONG[part])].drop_duplicates(["bike_id", "done"])
            series["공단 정비"] = g.groupby("h").size()
        s = rows[(rows.kind == "수리점") & rows["item"].isin(PARTS_SHOP[part])].drop_duplicates(["bike_id", "done"])
        series["수리점"] = s.groupby("h").size()
        for name, (p0, p1) in COMPARE.items():
            r = {"유형": part, "비교": name}
            for lab, ser in series.items():
                r[f"{lab} 비"] = round(ser.get(p1, float("nan")) / ser.get(p0, float("nan")), 3)
            pr.append(r)
    pd.DataFrame(pr).to_csv(ROOT / "output/tables/maint_b_parts.csv", index=False)

    # 작업이 신고를 따라온 것인지: 같은 자전거의 신고(48시간 병합 뒤)가 앞선 N일 안에 있었나.
    # 신고 없이 한 작업(직원 순회·선제 정비 등)이 늘었다면 '선제 수거가 신고를 줄였다'(H6)는 설명도 살아난다
    f = pd.read_parquet(ROOT / "data/processed/fail_std.parquet", columns=["bike", "reg_ts", "keep"])
    f = f[f.keep].assign(bike_id=lambda d: d.bike.astype("int64"), reg_ts=lambda d: d.reg_ts.astype("datetime64[ns]"))
    rep_ = f[["bike_id", "reg_ts"]].drop_duplicates().sort_values("reg_ts")
    tr = []
    for k in ("정비", "수리점"):
        j = jobs[jobs.kind == k][["bike_id", "done"]].sort_values("done")
        x = pd.merge_asof(j, rep_, left_on="done", right_on="reg_ts", by="bike_id", direction="backward")
        gap = (x.done - x.reg_ts).dt.total_seconds() / 86400
        x["h"] = half(x.done)
        for n in (3, 7, 14):
            g = x.assign(linked=gap.le(n)).groupby("h").linked.agg(["sum", "size"])
            for hh, r in g.iterrows():
                tr.append({"구분": k, "기준(일)": n, "반기": hh, "작업": int(r["size"]), "신고 뒤 작업": int(r["sum"]),
                           "신고 없던 작업": int(r["size"] - r["sum"]), "신고 뒤 비율": round(r["sum"] / r["size"], 3)})
    trig = pd.DataFrame(tr)
    trig.to_csv(ROOT / "output/tables/maint_b_trigger.csv", index=False)

    # 월별 그림: 대여 1만 건당 신고와 작업
    mo = ok.groupby(ok.date.dt.to_period("M"))[["rentals", "total_faults"]].sum()
    for k in KINDS:
        mo[k] = jobs[jobs.kind == k].groupby(jobs[jobs.kind == k].done.dt.to_period("M")).size()
    mo.loc["2025-01":"2025-12", "점검"] = float("nan")
    fig, ax = plt.subplots(figsize=(11, 4.5))
    for c, lab, col in (("total_faults", "신고(건수)", "black"), ("정비", "공단 정비 작업", "tab:blue"),
                        ("점검", "공단 점검 작업", "tab:green"), ("수리점", "수리점 작업", "tab:orange")):
        ax.plot(mo.index.to_timestamp(), mo[c] / mo.rentals * 1e4, label=lab, color=col, lw=2 if c == "total_faults" else 1.4)
    ax.axvline(pd.Timestamp("2025-03-01"), color="red", ls=":", lw=1)
    ax.set_ylabel("대여 1만 건당"); ax.set_title("신고와 정비·점검 작업(대여 1만 건당, 월별). 2025년 점검 기록 없음, 수리점은 3 ~ 12월만")
    ax.legend(ncol=4, fontsize=9); ax.grid(alpha=.3)
    fig.tight_layout(); fig.savefig(ROOT / "output/figures/maint_b_monthly.png", dpi=130)

    print(h[["rentals", "total_faults", *KINDS, "total_faults_per10k", "정비_per10k", "점검_per10k", "수리점_per10k"]].round(2).to_string())
    print(ratios.to_string())
    print(pd.DataFrame(pr).to_string())
    print(trig[trig['기준(일)'] == 14].to_string())


if __name__ == "__main__":
    main()
