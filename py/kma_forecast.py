"""기상청의 과거 예보(단기예보 + 중기예보)를 "원점 t에서 볼 수 있던 앞으로 7일의 예보"로 정리한다 — 시나리오 B

[왜 필요한가]
1C의 예측은 미래 7일의 날씨를 예보로만 안다고 본다. 실제 운영에서 쓸 예보는 기상청 예보이므로,
학습·평가도 같은 출처의 과거 예보로 하는 것이 맞다(처음에는 예보 오차를 가정으로 흉내 냈다 = 시나리오 C).

[자료] 기상자료개방포털에서 사람이 내려받은 파일(2026-10-06 ~ 07)
  data/raw/forecast_short/old/*.zip  (구)단기예보 2021-03-01 ~ 06-30. 종로구 교남동 격자(60, 127) = 서울 관측소 근처
                                     3시간 기온, 일 최고·최저기온, 강수형태, 강수확률, 6시간 강수량, 6시간 적설
  data/raw/forecast_short/new/*.zip  단기예보 2021-06-30 ~ 2026-10-06. 같은 격자
                                     1시간 기온, 일 최고·최저기온, 강수형태, 강수확률, 1시간 강수량, 1시간 적설
  data/raw/forecast_mid/temp/*.csv   중기기온예보 2021-01-01 ~ 2026-10-06, 서울. 일 최저·최고기온(3 ~ 10일 뒤)
  data/raw/forecast_mid/land/*.csv   중기육상예보 같은 기간, 서울·인천·경기. 오전·오후 날씨와 강수확률(3 ~ 10일 뒤)

[파일 형식(단기예보)]
  "Start : YYYYMMDD" 줄 뒤에 "day,hour,forecast,value" 줄이 이어진다. day = 그 달의 며칠, hour = 발표 시각,
  forecast = 발표 시각부터 몇 시간 뒤의 값인지, value = 예보값(-999.9는 결측).
  2021-06-30 이후 파일의 day·hour는 UTC다(파일 머리글에 적혀 있다) → 9시간을 더해 한국시각으로 바꾼다. 구 형식은 한국시각.
  하루 8번 발표(한국시각 02, 05, 08, 11, 14, 17, 20, 23시). 예보 범위는 2023년까지 +79시간, 2024년부터 +103시간.
  포털에서 내보낼 때 깨진 줄(날짜 자리에 이상한 수)이 2 ~ 4% 있다 → 건너뛴다.

[원점 t에서 쓸 수 있는 예보]
  원점 t(그날 아침)에는 t−1일 23시(한국시각) 발표 단기예보와 t−1일 18시 발표 중기예보까지 볼 수 있다.
  - t ~ t+2일: 단기예보의 시간별 값을 날짜별로 요약(평균·최고·최저기온, 강수량 합, 적설 합, 비 유무)
  - t+3일: 단기예보에 그날 24시간이 다 있으면(2024년 이후) 단기, 아니면 중기
  - t+4 ~ t+6일: 중기예보. 최고·최저기온은 그대로, 평균기온은 (최고 + 최저) ÷ 2를 학습 구간의 관측으로 보정,
                강수량은 없으므로 "강수확률이 그 정도일 때 학습 구간에서 실제로 온 비의 평균"으로 대신하고,
                비 유무는 강수확률(0 ~ 1)로, 눈은 날씨 문구에 '눈'이 있으면 1
  - 기온의 계통 오차(격자와 관측소의 차이)는 예측 거리별 평균 편향을 학습 구간에서 구해 뺀다

[출력]
  data/interim/weather_forecast/kma_by_origin.parquet   원점 × 앞으로 7일(lead 0 ~ 6): tavg, tmax, tmin, rain_mm, rain_any, snow_cm, src(short/mid)
  output/tables/fc_forecast_error_kma.csv               예측 거리별 예보 오차(관측 대비, 학습·검증 구간만)

[실행]  터미널(프로젝트 폴더):  python py/kma_forecast.py
"""
import glob
import re
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
SHORT = ROOT / "data/raw/forecast_short"
MID = ROOT / "data/raw/forecast_mid"
OUT = ROOT / "data/interim/weather_forecast/kma_by_origin.parquet"
TRAIN = ("2021-04-08", "2023-12-31")        # 보정값을 구할 때 쓰는 기간(검증·테스트 구간은 쓰지 않는다)


# ---------------------------------------------------------------------------
# 단기예보 읽기
# ---------------------------------------------------------------------------

def _zip_name(i):
    return i.filename if i.flag_bits & 0x800 else i.filename.encode("cp437").decode("cp949", "replace")


def _parse_short_file(lines, old_format):
    """단기예보 파일 하나 → (issue 발표시각 KST, target 대상시각 KST, value). 깨진 줄은 건너뛴다"""
    recs, start = [], None
    for l in lines:
        m = re.search(r"Start\s*:\s*(\d{8})", l)
        if m:
            start = pd.Timestamp(m.group(1)); continue
        p = [x.strip() for x in l.split(",")]
        if len(p) < 4 or not re.fullmatch(r"\d{1,2}", p[0]) or not re.fullmatch(r"\d{4}", p[1]):
            continue
        day, hour, lead = int(p[0]), int(p[1][:2]), int(p[2])
        try:
            val = float(p[3])
        except ValueError:
            continue
        if val <= -999 or day < 1 or day > 31:
            continue
        month_start = start.replace(day=1)
        if day < start.day:                               # 블록이 월말에서 시작해 다음 달로 넘어간 경우
            month_start = month_start + pd.DateOffset(months=1)
        try:
            issue = month_start + pd.Timedelta(days=day - 1, hours=hour)
        except Exception:
            continue
        if not old_format:
            issue = issue + pd.Timedelta(hours=9)        # UTC → KST
        recs.append((issue, issue + pd.Timedelta(hours=lead), val))
    return pd.DataFrame(recs, columns=["issue", "target", "value"])


def read_short():
    """단기예보 zip 전부 → 변수별 표. 같은 (발표시각, 대상시각)이 여러 파일에 있으면 하나만 남긴다"""
    parts = {}
    for z in sorted(glob.glob(str(SHORT / "*/*.zip"))):
        zf = zipfile.ZipFile(z)
        for i in zf.infolist():
            name = _zip_name(i)
            var = name.split("_")[1]                                # 예: 1시간기온, 강수확률
            lines = zf.open(i).read().decode("utf-8-sig").splitlines()
            df = _parse_short_file(lines, old_format="202103_202106" in name)
            parts.setdefault(var, []).append(df)
    return {v: pd.concat(d, ignore_index=True).drop_duplicates(["issue", "target"], keep="last") for v, d in parts.items()}


def short_by_origin(short):
    """원점마다 't−1일 23시 발표' 단기예보를 골라 앞으로 7일을 날짜별로 요약한다.
    돌려주는 것: origin, lead, date, tavg, tmax, tmin, rain_mm, snow_cm, rain_any, n_hours(그날 기온 예보가 덮는 시간 수)
    원점 당일(lead 0)은 전날 23시 발표의 첫 값이 05시라 19시간만 덮인다 → 18시간 이상이면 쓴다"""
    temp = pd.concat([short.get("1시간기온", pd.DataFrame()).assign(step=1), short.get("3시간기온", pd.DataFrame()).assign(step=3)])
    rain = pd.concat([short.get("1시간강수량", pd.DataFrame()), short.get("6시간강수량", pd.DataFrame())])
    snow = pd.concat([short.get("1시간적설", pd.DataFrame()), short.get("6시간적설", pd.DataFrame())])
    ptype = short.get("강수형태", pd.DataFrame())                   # 0 없음, 1 비, 2 비/눈, 3 눈, 4 소나기
    rows = []
    for name, df in (("t", temp), ("r", rain), ("s", snow), ("p", ptype)):
        d = df[df.issue.dt.hour == 23].copy()                         # 전날 23시 발표분만
        d["origin"] = d.issue.dt.normalize() + pd.Timedelta(days=1)
        d["date"] = d.target.dt.normalize()
        d["kind"] = name
        rows.append(d)
    a = pd.concat(rows, ignore_index=True)
    a = a[(a.date >= a.origin) & (a.date <= a.origin + pd.Timedelta(days=6))]
    g = a.groupby(["origin", "date", "kind"]).value
    agg = pd.DataFrame({"mean": g.mean(), "max": g.max(), "min": g.min(), "sum": g.sum(), "n": g.size()}).unstack("kind")
    step = a[a.kind == "t"].groupby(["origin", "date"]).step.max()
    out = pd.DataFrame({
        "tavg": agg[("mean", "t")], "tmax": agg[("max", "t")], "tmin": agg[("min", "t")],
        "n_hours": agg[("n", "t")] * step.reindex(agg.index),        # 기온 값이 덮는 시간 수(3시간 간격 자료는 값 하나가 3시간)
        "rain_mm": agg[("sum", "r")], "snow_cm": agg[("sum", "s")],
        "rain_any": (agg[("max", "p")] > 0).astype(float)}).reset_index()
    out["lead"] = (out.date - out.origin).dt.days
    # 일 최고·최저기온 예보가 따로 있으면 그것을 쓴다(시간별 값의 최대·최소보다 정확)
    for var, col, fn in (("일최고기온", "tmax", "max"), ("일최저기온", "tmin", "min")):
        if var in short:
            d = short[var][short[var].issue.dt.hour == 23].copy()
            d["origin"] = d.issue.dt.normalize() + pd.Timedelta(days=1); d["date"] = d.target.dt.normalize()
            v = d.groupby(["origin", "date"]).value.agg(fn).rename(col + "_d").reset_index()
            out = out.merge(v, on=["origin", "date"], how="left")
            out[col] = out[col + "_d"].fillna(out[col]); out = out.drop(columns=col + "_d")
    return out


# ---------------------------------------------------------------------------
# 중기예보 읽기
# ---------------------------------------------------------------------------

def read_mid():
    """중기기온(서울)과 중기육상(서울.인천.경기) → 원점 × 대상일: tmax, tmin, rain_prob(0 ~ 1), snow(0/1)"""
    t = pd.concat([pd.read_csv(f, encoding="cp949") for f in sorted(glob.glob(str(MID / "temp/*.csv")))], ignore_index=True)
    t = t[t["도시"] == "서울"]
    t = t[t["발표시각"].str.endswith("18시")].copy()                  # 전날 18시 발표분
    t["origin"] = pd.to_datetime(t["발표시각"].str[:10]) + pd.Timedelta(days=1)
    t["date"] = pd.to_datetime(t["예보시각"])
    t = t.rename(columns={"최저기온": "tmin", "최고기온": "tmax"})[["origin", "date", "tmin", "tmax"]]

    l = pd.concat([pd.read_csv(f, encoding="cp949") for f in sorted(glob.glob(str(MID / "land/*.csv")))], ignore_index=True)
    l = l[(l["지역"] == "서울.인천.경기") & l["발표시각"].str.endswith("18시")].copy()
    l["origin"] = pd.to_datetime(l["발표시각"].str[:10]) + pd.Timedelta(days=1)
    l["date"] = pd.to_datetime(l["예보시각"].str[:10])
    l["snow"] = l["예보"].astype(str).str.contains("눈").astype(float)
    l["rainy"] = l["예보"].astype(str).str.contains("비|소나기|눈").astype(float)
    g = l.groupby(["origin", "date"]).agg(rain_prob=("강수확률(%)", "max"), snow=("snow", "max"), rainy=("rainy", "max")).reset_index()
    g["rain_prob"] = g.rain_prob / 100
    out = t.merge(g, on=["origin", "date"], how="outer")
    out["lead"] = (out.date - out.origin).dt.days
    return out[(out.lead >= 0) & (out.lead <= 6)]


# ---------------------------------------------------------------------------
# 합치기와 보정
# ---------------------------------------------------------------------------

def build(obs):
    """단기·중기예보를 원점 × 앞으로 7일 표로 합치고, 학습 구간의 관측으로 보정값을 구해 적용한다.
    obs: 일별 마스터(date, tavg, tmax, tmin, rain_mm)"""
    s = short_by_origin(read_short())
    m = read_mid()
    s_ok = s[s.n_hours >= 18].copy()                                   # 그날을 18시간 이상 덮는 날만 단기예보를 쓴다(당일은 05시부터라 19시간)
    s_ok["src"] = "short"
    m = m.assign(src="mid")
    keys = ["origin", "date", "lead"]
    f = s_ok.merge(m[keys + ["tmin", "tmax", "rain_prob", "snow", "rainy"]], on=keys, how="outer", suffixes=("", "_mid"))
    f["src"] = np.where(f.n_hours.notna() & (f.n_hours >= 18), "short", "mid")
    mid = f.src == "mid"
    f.loc[mid, "tmax"] = f.loc[mid, "tmax_mid"]; f.loc[mid, "tmin"] = f.loc[mid, "tmin_mid"]

    tr = f.merge(obs, on="date", suffixes=("", "_obs"))
    tr = tr[tr.date.between(*TRAIN)]
    # (1) 중기예보의 평균기온: 관측 tavg ≈ a + b × (tmax + tmin) / 2  (학습 구간, 중기 행에서 추정)
    x = tr[tr.src == "mid"]; mid_mean = (x.tmax + x.tmin) / 2
    b, a = np.polyfit(mid_mean, x.tavg_obs, 1)
    f.loc[mid, "tavg"] = a + b * (f.loc[mid, "tmax"] + f.loc[mid, "tmin"]) / 2
    # (2) 중기예보의 강수량: 강수확률 구간별 '실제로 온 비의 평균'(학습 구간)
    bins = [-0.01, 0.15, 0.35, 0.55, 0.75, 1.0]
    x = tr[tr.src == "mid"].assign(pb=lambda t: pd.cut(t.rain_prob, bins))
    rain_by_prob = x.groupby("pb", observed=True).rain_mm_obs.mean()
    f.loc[mid, "rain_mm"] = pd.cut(f.loc[mid, "rain_prob"], bins).map(rain_by_prob).astype(float).to_numpy()
    f.loc[mid, "rain_any"] = f.loc[mid, "rain_prob"]
    f.loc[mid, "snow_cm"] = f.loc[mid, "snow"] * 1.0
    # (3) 기온의 계통 오차: 예측 거리별 평균 편향(학습 구간)을 뺀다
    tr = f.merge(obs, on="date", suffixes=("", "_obs")); tr = tr[tr.date.between(*TRAIN)]
    for col in ("tavg", "tmax", "tmin"):
        bias = tr.groupby("lead").apply(lambda t: (t[col] - t[col + "_obs"]).mean(), include_groups=False)
        f[col] = f[col] - f.lead.map(bias)
    f["rain_mm"] = f.rain_mm.clip(lower=0).fillna(0); f["snow_cm"] = f.snow_cm.fillna(0)
    # 비 유무가 비어 있는 날(강수형태·강수확률 발표 누락): 강수량 예보로 대신 정한다
    f["rain_any"] = f.rain_any.fillna((f.rain_mm >= 1).astype(float))
    f["rain_prob"] = f.rain_prob.fillna(f.rain_any)
    cols = ["origin", "lead", "date", "src", "tavg", "tmax", "tmin", "rain_mm", "rain_any", "snow_cm", "rain_prob"]
    out = f[cols].sort_values(["origin", "lead"]).reset_index(drop=True)
    # 빠진 칸 채우기: 어떤 원점에 어느 날의 예보가 없으면(발표 누락), 하루 전 원점이 본 같은 날 예보로 대신한다(src = "prev")
    grid = pd.MultiIndex.from_product([pd.date_range(out.origin.min(), out.origin.max()), range(7)], names=["origin", "lead"]).to_frame(index=False)
    grid["date"] = grid.origin + pd.to_timedelta(grid.lead, unit="D")
    out = grid.merge(out.drop(columns="date"), on=["origin", "lead"], how="left")
    for _ in range(3):
        miss = out.tavg.isna()
        if not miss.any():
            break
        prev = out.assign(origin=out.origin + pd.Timedelta(days=1), lead=out.lead - 1, src="prev")
        prev = prev[prev.lead >= 0].set_index(["origin", "lead"])
        idx = pd.MultiIndex.from_frame(out.loc[miss, ["origin", "lead"]])
        for c in ["src", "tavg", "tmax", "tmin", "rain_mm", "rain_any", "snow_cm", "rain_prob"]:
            out.loc[miss, c] = prev[c].reindex(idx).to_numpy()
    out = out.dropna(subset=["tavg"])
    return out, {"mid_tavg_fit": (a, b), "rain_by_prob": rain_by_prob.to_dict(), "temp_bias_removed": True}


def error_table(f, obs):
    """예측 거리별 예보 오차(학습·검증 구간). 시나리오 C의 가정과 나란히 둔다"""
    d = f.merge(obs, on="date", suffixes=("", "_obs"))
    rows = []
    for k in range(7):
        for name, (a, b) in (("학습(2021-04 ~ 2023)", TRAIN), ("검증(2024)", ("2024-01-01", "2024-12-31"))):
            x = d[(d.lead == k) & d.date.between(a, b)]
            e = x.tavg - x.tavg_obs
            wet_f = x.rain_any >= 0.5; wet_o = x.rain_mm_obs >= 1
            rows.append({"며칠 뒤": k, "기간": name, "일수": len(x), "출처": x.src.value_counts().to_dict(),
                         "평균기온 오차 표준편차(℃)": e.std(), "평균기온 편향(보정 뒤, ℃)": e.mean(),
                         "비 유무를 틀린 비율": (wet_f != wet_o).mean(), "비 온 날을 놓친 비율": ((~wet_f) & wet_o).sum() / max(wet_o.sum(), 1),
                         "강수량 상관(log)": np.corrcoef(np.log1p(x.rain_mm), np.log1p(x.rain_mm_obs))[0, 1] if len(x) > 2 else np.nan,
                         "시나리오 C의 가정: 기온 오차": 1.0 + 0.25 * k, "시나리오 C의 가정: 비 유무 오판": 0.10 + 0.03 * k})
    return pd.DataFrame(rows).set_index(["기간", "며칠 뒤"])


if __name__ == "__main__":
    obs = pd.read_parquet(ROOT / "data/processed/daily_master_v2.parquet")[["date", "tavg", "tmax", "tmin", "rain_mm"]]
    f, info = build(obs)
    OUT.parent.mkdir(parents=True, exist_ok=True); f.to_parquet(OUT, index=False)
    print("원점", f.origin.nunique(), "개 (", f.origin.min().date(), "~", f.origin.max().date(), ") | 행", len(f), "| 출처:", f.src.value_counts().to_dict())
    print("보정값:", info)
    tab = error_table(f, obs); tab.to_csv(ROOT / "output/tables/fc_forecast_error_kma.csv")
    pd.set_option("display.width", 250); print(tab.round(2).to_string())
