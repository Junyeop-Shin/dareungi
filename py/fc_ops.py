"""운영 주간 부품 예측: 최근 4주 신고율 × 대여량 예측(구조 나이브, 1C의 S1).

대여량 예측은 두 가지를 같이 낸다.
  kma  예보 대여량. 1C의 K2(기상청 예보로 학습한 음이항, 달력 + 날씨). 화면에 보여 주는 쪽
  cal  달력 대여량. 1C의 시나리오 D(날씨 없는 음이항, 달력만). 예보가 빠진 주에 대신 쓰고, 매주 같이 기록해 비교한다
둘 다 1C 테스트 때처럼 2021-04-08 ~ 2024-12-31로 맞춰 계수를 output/models/expo_ops.json에 고정한다(py/forecast/fc_ops_fit.py).
예측은 그 계수로 직접 계산한다(GLMModel.predict와 같은 값인지 fc_ops_fit.py가 확인한다).

신고율(lvl)과 대여 수준(expo_lvl)은 날짜마다 7일 전에서 끊은 28일 값이라 원점 t에서 t+6까지 모두 계산할 수 있다.
정의는 fc.load_master()와 같고, fc_ops_fit.py가 2024 검증 구간에서 1C 숫자를 그대로 재현하는지 확인한다.

실시간 예보(fcst_archive)는 kma_forecast.py가 과거 예보에 한 것과 같은 방식으로 날짜별로 요약·보정한다.
  - 날짜마다 원점 아침까지 나온 가장 최근 발표를 쓴다. 단기예보가 그날을 18시간 이상 덮으면 단기, 아니면 중기
  - 중기 평균기온, 중기 강수량(강수확률 구간별), lead별 기온 편향은 output/models/kma_calibration.json의 값
  - 하루라도 비면 예보 대여량은 내지 않는다(달력 대여량으로 대신)

주의: 단기예보 강수량·적설은 '강수없음', '1mm 미만', '30.0~50.0mm' 같은 문자열이다. 범위는 가운데 값,
      '미만'은 기준의 절반, '이상'은 기준값으로 바꾼다.

사용: from fc_ops import load_models, forecast, levels, kma_live
"""
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
PARTS = {"fail_tire": "타이어", "fail_chain": "체인", "fail_saddle": "안장", "fail_pedal": "페달", "total_faults": "전체"}
# 화면에 같이 보여 주는 나머지 신고 유형(사람 결정 2026-10-10). 1C 과제(fc.PARTS)에는 없던 유형이라 따로 둔다:
# 1C 숫자를 재현하는 확인(fc_ops_fit)은 PARTS로만 하고, 운영 예측·실적·화면은 ALL을 쓴다. 방법은 같다(최근 4주 신고율 × 대여량 예측)
EXTRA = {"fail_terminal": "단말기", "fail_etc": "기타"}
ALL = PARTS | EXTRA
MODEL_FILE = ROOT / "output/models/expo_ops.json"
SPEC_FILE = ROOT / "output/models/fc_ops_spec.json"
CALIB_FILE = ROOT / "output/models/kma_calibration.json"
HOLIDAYS = ROOT / "data/raw/calendar"        # holidays_2016_2026.csv(1A) + holidays_<연도>.csv(py/build/holidays.py)
LEVEL_FROM = "2021-04-08"        # 이전은 신고 수준이 다른 레짐이라 신고율 계산에서 뺀다(fc.load_master와 같음)
METHODS = {"kma": "예보 대여량", "cal": "달력 대여량"}
WX = ["tavg", "tmax", "tmin", "rain_mm", "rain_any", "snow_cm"]


# ---------------------------------------------------------------------------
# 달력과 평년기온
# ---------------------------------------------------------------------------

def normal_coef(m):
    """평년 평균기온 곡선 계수. 1A 마스터와 같게 2016 ~ 2023년 tavg를 상수 + 계절 사인·코사인 4개로 맞춘다"""
    X = np.column_stack([np.ones(len(m)), m.fs1, m.fc1, m.fs2, m.fc2])
    fit = (m.date.dt.year <= 2023).to_numpy()
    coef, *_ = np.linalg.lstsq(X[fit], m.tavg.to_numpy()[fit], rcond=None)
    return [float(c) for c in coef]


def calendar(start, end, coef, holidays=None):
    """start ~ end 날짜별 달력 변수(1A 마스터와 같은 정의)와 평년기온. 공휴일 표가 덮지 않는 해가 있으면 hol_known=False"""
    if holidays is None:
        hol = pd.concat([pd.read_csv(f, parse_dates=["date"]) for f in sorted(HOLIDAYS.glob("holidays_*.csv"))]).date
    else:
        hol = pd.to_datetime(pd.Series(holidays))
    dates = pd.date_range(pd.Timestamp(start) - pd.Timedelta(days=1), pd.Timestamp(end) + pd.Timedelta(days=1))
    c = pd.DataFrame({"date": dates})
    c["doy"], c["month"] = c.date.dt.dayofyear, c.date.dt.month
    c["dow"] = c.date.dt.dayofweek + 1
    c["is_holiday"] = c.date.isin(hol).astype(int)
    c["is_off"] = ((c.dow >= 6) | (c.is_holiday == 1)).astype(int)
    prev_off, next_off = c.is_off.shift(1, fill_value=0), c.is_off.shift(-1, fill_value=0)
    c["after_off"] = ((c.is_off == 0) & (prev_off == 1)).astype(int)
    c["bridge"] = ((c.is_off == 0) & (prev_off == 1) & (next_off == 1)).astype(int)
    for k in (1, 2):
        c[f"fs{k}"] = np.sin(2 * np.pi * k * c.doy / 365.25)
        c[f"fc{k}"] = np.cos(2 * np.pi * k * c.doy / 365.25)
    c["tavg_normal"] = np.column_stack([np.ones(len(c)), c.fs1, c.fc1, c.fs2, c.fc2]) @ np.asarray(coef)
    c["hol_known"] = c.date.dt.year <= hol.dt.year.max()
    return c.iloc[1:-1].reset_index(drop=True)


# ---------------------------------------------------------------------------
# 신고율·대여 수준
# ---------------------------------------------------------------------------

def levels(d, bad=None, no_service=None, until=None, window=28):
    """날짜별 expo_lvl(34 ~ 7일 전 28일 평균 대여), expo_lvl7(13 ~ 7일 전 평균), lvl_<부품>(같은 28일 신고 / 대여).

    d          : 날짜 인덱스, 열 rentals와 PARTS의 열(fail_tire … total_faults)
    bad        : 신고율 계산에서 뺄 날(운영 중단, 신고 누락). no_service: 대여 수준에서 뺄 날
    until      : 이 날짜까지 인덱스를 늘린다(미래 7일 행). 값이 7일 전에서 끊기므로 t+6까지 계산된다
    window     : 신고율 창(일). 운영은 28. 다른 값은 강건성 점검용(py/forecast/robustness.py)
    """
    d = d.sort_index()
    if until is not None and pd.Timestamp(until) > d.index.max():
        d = d.reindex(pd.date_range(d.index.min(), until))
    bad = pd.Series(0, index=d.index) if bad is None else pd.Series(bad).reindex(d.index).fillna(0)
    ns = pd.Series(0, index=d.index) if no_service is None else pd.Series(no_service).reindex(d.index).fillna(0)
    out = pd.DataFrame(index=d.index)
    r = d.rentals.where(ns == 0)
    out["expo_lvl"] = r.rolling(28, min_periods=14).mean().shift(7)
    out["expo_lvl7"] = r.rolling(7, min_periods=4).mean().shift(7)
    ok = (bad == 0) & (d.index >= LEVEL_FROM)
    den = d.rentals.where(ok).rolling(window, min_periods=window // 2).sum().shift(7)
    for p in ALL:
        if p in d:                                   # 단말기·기타 열이 없는 옛 표로 불러도 돌게
            out["lvl_" + p] = d[p].where(ok).rolling(window, min_periods=window // 2).sum().shift(7) / den
    return out


# ---------------------------------------------------------------------------
# 실시간 예보 → 날짜별 날씨
# ---------------------------------------------------------------------------

def _amount(v):
    """단기예보 강수량·적설 문자열을 숫자로. '강수없음' 0, '1mm 미만' 0.5, '30.0~50.0mm' 40, '50.0mm 이상' 50"""
    v = str(v).strip()
    if v in ("", "강수없음", "적설없음") or v == "nan":
        return 0.0
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", v)]
    if not nums:
        return 0.0
    if "미만" in v:
        return nums[0] / 2
    return sum(nums) / len(nums)


def _short_daily(a):
    """단기예보 행(issue_ts, fcst_ts, category, value)을 (issue, date)별 요약으로"""
    a = a.assign(date=a.fcst_ts.dt.normalize())
    t = a[a.category == "TMP"].assign(v=lambda x: pd.to_numeric(x.value, errors="coerce")).sort_values(["issue_ts", "fcst_ts"])
    # 3일째 이후는 3시간 간격이라 값 하나가 3시간을 덮는다(과거 파일의 3시간기온과 같은 규칙)
    t["step"] = (t.groupby("issue_ts").fcst_ts.shift(-1) - t.fcst_ts).dt.total_seconds().div(3600).fillna(1).clip(1, 3)
    g = t.groupby(["issue_ts", "date"])
    out = pd.DataFrame({"tavg": g.v.mean(), "tmax": g.v.max(), "tmin": g.v.min(), "n_hours": g.step.sum()})
    for cat, col, fn in (("TMX", "tmax", "max"), ("TMN", "tmin", "min")):        # 일 최고·최저 예보가 있으면 그것을 쓴다
        x = a[a.category == cat].assign(v=lambda y: pd.to_numeric(y.value, errors="coerce")).groupby(["issue_ts", "date"]).v.agg(fn)
        out[col] = x.reindex(out.index).fillna(out[col])
    for cat, col in (("PCP", "rain_mm"), ("SNO", "snow_cm")):
        x = a[a.category == cat].assign(v=lambda y: y.value.map(_amount)).groupby(["issue_ts", "date"]).v.sum()
        out[col] = x.reindex(out.index)
    pty = a[a.category == "PTY"].assign(v=lambda y: pd.to_numeric(y.value, errors="coerce")).groupby(["issue_ts", "date"]).v.max()
    out["rain_any"] = (pty.reindex(out.index) > 0).astype(float)
    return out.reset_index()


def _mid_daily(a, calib):
    """중기예보 행을 (issue, date)별로. 평균기온·강수량은 학습 구간에서 구한 식으로 만든다"""
    a = a.assign(date=a.fcst_ts.dt.normalize())
    num = a[a.category.isin(["TMX", "TMN"])].assign(v=lambda x: pd.to_numeric(x.value, errors="coerce"))
    out = num.pivot_table(index=["issue_ts", "date"], columns="category", values="v").rename(columns={"TMX": "tmax", "TMN": "tmin"})
    pop = a[a.category.str.startswith("POP")].assign(v=lambda x: pd.to_numeric(x.value, errors="coerce") / 100).groupby(["issue_ts", "date"]).v.max()
    snow = a[a.category.str.startswith("WF")].assign(v=lambda x: x.value.astype(str).str.contains("눈").astype(float)).groupby(["issue_ts", "date"]).v.max()
    out["rain_prob"], out["snow"] = pop.reindex(out.index), snow.reindex(out.index).fillna(0)
    a0, b0 = calib["mid_tavg_fit"]
    out["tavg"] = a0 + b0 * (out.tmax + out.tmin) / 2
    out["rain_mm"] = pd.cut(out.rain_prob, calib["rain_bins"], labels=False).map(dict(enumerate(calib["rain_by_prob"]))).astype(float)
    out["rain_any"] = out.rain_prob
    out["snow_cm"] = out.snow
    return out.reset_index()


def kma_live(archive, origin, calib=None, stale_days=3):
    """fcst_archive → 원점 origin의 앞으로 7일 날씨(lead 0 ~ 6). 하루라도 비면 None.

    날짜마다 원점 07시까지 나온 발표 중 가장 최근 것을 쓰되, 단기예보는 그날을 18시간 이상 덮을 때만 쓴다.
    stale_days일보다 오래된 발표는 쓰지 않는다.
    """
    calib = calib or json.load(open(CALIB_FILE))
    o = pd.Timestamp(origin).normalize()
    a = archive[(archive.issue_ts <= o + pd.Timedelta(hours=7)) & (archive.issue_ts >= o - pd.Timedelta(days=stale_days))].copy()
    a["fcst_ts"] = pd.to_datetime(a.fcst_ts)
    dates = pd.date_range(o, periods=7)
    s = _short_daily(a[a.src == "short"]) if (a.src == "short").any() else pd.DataFrame(columns=["issue_ts", "date", "n_hours"])
    m = _mid_daily(a[a.src == "mid"], calib) if (a.src == "mid").any() else pd.DataFrame(columns=["issue_ts", "date"])
    rows = []
    for k, d in enumerate(dates):
        ss = s[(s.date == d) & (s.n_hours >= 18)].sort_values("issue_ts")
        mm = m[(m.date == d)].dropna(subset=["tmax", "tmin"]).sort_values("issue_ts") if len(m) else m
        if len(ss):
            r = ss.iloc[-1]; src = "short"
        elif len(mm):
            r = mm.iloc[-1]; src = "mid"
        else:
            return None
        rows.append({"lead": k, "date": d, "src": src, "issue_ts": r.issue_ts, **{c: r.get(c, np.nan) for c in WX}})
    f = pd.DataFrame(rows)
    for col in ("tavg", "tmax", "tmin"):                      # lead별 평균 편향(학습 구간)을 뺀다
        f[col] = f[col].astype(float) - f.lead.map({int(k): v for k, v in calib["temp_bias_by_lead"][col].items()})
    f["rain_mm"] = f.rain_mm.astype(float).clip(lower=0).fillna(0)
    f["snow_cm"] = f.snow_cm.astype(float).fillna(0)
    f["rain_any"] = f.rain_any.astype(float).fillna((f.rain_mm >= 1).astype(float))
    return f


# ---------------------------------------------------------------------------
# 예측
# ---------------------------------------------------------------------------

def frame(lv, cal, origin, wx=None):
    """원점 origin의 미래 7일 행: 신고율·대여 수준, 달력, (있으면) 날씨와 파생 변수"""
    dates = pd.date_range(pd.Timestamp(origin).normalize(), periods=7)
    f = cal.set_index("date").reindex(dates).join(lv.reindex(dates)).rename_axis("date").reset_index()
    if wx is not None:
        w = wx.set_index("lead") if "lead" in wx else wx.reset_index(drop=True)
        for c in WX:
            f[c] = np.asarray(w[c], dtype=float)
        f["cold_wave"] = (f.tavg <= -4).astype(float)
        f["snow_any"] = (f.snow_cm >= 1).astype(float)
        f["tavg_anom"] = f.tavg - f.tavg_normal
    return f


DISTRICT_COLS = {"reports": "total_faults", "n_tire": "fail_tire", "n_chain": "fail_chain", "n_saddle": "fail_saddle", "n_pedal": "fail_pedal",
                 "n_terminal": "fail_terminal", "n_etc": "fail_etc"}


def daily_from_district(df):
    """운영 DB daily_district(날짜 × 자치구)를 서울 전체 날짜별 표(rentals + PARTS 열)로"""
    d = df.groupby("date")[["rentals"] + [c for c in DISTRICT_COLS if c in df]].sum().rename(columns=DISTRICT_COLS)
    d.index = pd.to_datetime(d.index)
    return d.sort_index()


def master_flags():
    """마스터의 bad(운영 중단·신고 누락)와 no_service. 마스터 기간 밖은 0으로 본다"""
    m = pd.read_parquet(ROOT / "data/processed/daily_master_v2.parquet", columns=["date", "no_service", "report_anomaly"])
    m = m.set_index(pd.to_datetime(m.date))
    return (m.no_service | m.report_anomaly).astype(int), m.no_service.astype(int)


def s1(f, expo_hat):
    """부품별 7일 합계 = Σ(그날 신고율 × 그날 대여량 예측)"""
    e = np.asarray(expo_hat, dtype=float)
    return {p: float((f["lvl_" + p].to_numpy() * e).sum()) for p in ALL if "lvl_" + p in f}


class ExpoModel:
    """저장한 음이항 계수로 대여량을 예측한다. 항 이름은 statsmodels 식 그대로(C(dow)[T.2], I(tavg ** 2), tavg_anom:fs1 …)"""

    def __init__(self, params):
        self.params = params

    @staticmethod
    def _term(name, f):
        if name == "Intercept":
            return np.ones(len(f))
        m = re.fullmatch(r"C\((\w+)\)\[T\.(.+)\]", name)               # 범주 더미
        if m:
            return (f[m.group(1)].astype(int).to_numpy() == int(m.group(2))).astype(float)
        if ":" in name and "(" not in name:                                # 상호작용
            a, b = name.split(":")
            return f[a].to_numpy(float) * f[b].to_numpy(float)
        return np.asarray(eval(name, {"np": np, "I": lambda x: x}, {c: f[c].to_numpy(float) for c in f.columns
                                                                 if c.isidentifier() and (pd.api.types.is_numeric_dtype(f[c]) or pd.api.types.is_bool_dtype(f[c]))}), dtype=float)

    def predict(self, f):
        return np.exp(sum(coef * self._term(name, f) for name, coef in self.params.items()))


def load_models():
    """고정한 대여량 모형 두 개와 설정(평년기온 계수, 80% 구간 보정값)"""
    saved = json.load(open(MODEL_FILE))
    return {k: ExpoModel(v["params"]) for k, v in saved.items()}, json.load(open(SPEC_FILE))


def forecast(d, origin, wx, models, spec, bad=None, no_service=None):
    """원점 origin의 부품별 7일 예측. 반환: {"cal": {부품: 값}, "kma": {부품: 값} 또는 None, "frame": 미래 7일 표}"""
    o = pd.Timestamp(origin).normalize()
    lv = levels(d[d.index < o], bad=bad, no_service=no_service, until=o + pd.Timedelta(days=6))
    cal = calendar(o, o + pd.Timedelta(days=6), spec["normal_coef"])
    f = frame(lv, cal, o, wx)
    out = {"frame": f, "cal": s1(f, models["cal"].predict(f)), "kma": None, "hol_known": bool(cal.hol_known.all())}
    if wx is not None:
        out["kma"] = s1(f, models["kma"].predict(f))
        out["expo_kma"] = float(np.asarray(models["kma"].predict(f)).sum())
    out["expo_cal"] = float(np.asarray(models["cal"].predict(f)).sum())
    return out
