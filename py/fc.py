"""1C 예측 비교용 공용 함수: 과제 정의, 나이브, 날씨 시나리오, 노출 예측, 채점.

원점 t에서 부품별 앞으로 7일(t ~ t+6) 신고 건수 합계를 맞힌다. t−1일까지의 관측값, t ~ t+6의
달력(요일·공휴일), 시나리오별 날씨는 쓸 수 있고, t 이후 대여량·신고 건수는 못 쓴다. 그래서 대여량(노출)도
따로 예측한다.

원점 구간은 7일 합계가 구간 안에 들도록 끝을 6일 당긴다.
  학습 2021-04-08 ~ 2023-12-25 / 검증 2024-01-01 ~ 2024-12-25 / 테스트 2025-01-01 ~ 2026-06-24
테스트 구간은 최종 평가 때 한 번만 예측·채점한다.

날씨 시나리오(앞으로 7일 날씨를 얼마나 아는가):
  A 실측       실제 관측값. 완벽한 예보를 가정한 상한선(1차 분석 방식)
  B 기상청 예보 그 원점에서 볼 수 있던 단기예보(0~2일)·중기예보(3~6일). 실제 운영 조건(py/forecast/kma_forecast.py)
  C 오차 주입   실측에 예보 오차를 흉내 낸 잡음을 더한다. B 이전에 쓰던 가정이고 오차 크기는 B와 비슷하다
  D 날씨 없음   날씨 변수를 뺀 모형. 평년값을 넣으면 매일 평균만큼 비가 오는 셈이 되어 대여량을
               30% 가까이 낮게 예측해서 쓰지 않는다

지표:
  MAE    |예측 − 실제| 평균(건)
  MAE%   MAE / 실제 평균 × 100
  skill  1 − MAE / 나이브(B1) MAE. 0.10이면 나이브보다 오차 10% 작음, 음수면 나이브보다 못함
  bias   예측 − 실제 평균. 양수면 과대예측

사용: from fc import *
"""
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm
import statsmodels.formula.api as smf
from scipy.optimize import minimize_scalar

ROOT = Path(__file__).resolve().parent.parent

# 예측 대상 열 → 표시 이름. 부품별은 행 수, 전체는 신고 건수
PARTS = {"fail_tire": "타이어", "fail_chain": "체인", "fail_saddle": "안장", "fail_pedal": "페달", "total_faults": "전체"}

SPLITS = {"train": ("2021-04-08", "2023-12-25"), "valid": ("2024-01-01", "2024-12-25"), "test": ("2025-01-01", "2026-06-24")}
TRAIN_DAYS = ("2021-04-08", "2023-12-31")          # 일 단위 모형(노출·음이항) 학습 범위



# ---------------------------------------------------------------------------
# 자료 준비
# ---------------------------------------------------------------------------

def load_master():
    """일별 마스터를 읽어 예측에 필요한 열을 붙인다.

    추가하는 열
      bad        학습·수준 계산에서 빼는 날(운영 중단일, 신고 누락 기간)
      snow_any   눈 1cm 이상
      expo_lvl   34~7일 전(28일) 하루 평균 대여건수. expo_lvl7은 13~7일 전(7일) 평균.
                 7일 전에서 끊어야 t+6일 행에서도 t−1일까지의 정보만 쓴다
      lvl_<부품> 같은 28일의 신고 합계 / 대여 합계(bad인 날 제외)
      개입·환경 변수: covid, qr_done, kick_law, kick_tow, recycle1, recycle2, bus_fare, subway_fare, climate_card, toss
    시나리오 D에서 평균값(0.3 등)을 넣을 수 있게 bool 열은 1/0으로 바꾼다.
    """
    m = pd.read_parquet(ROOT / "data/processed/daily_master_v2.parquet").sort_values("date").reset_index(drop=True)
    m["bad"] = m.no_service | m.report_anomaly
    m["snow_any"] = m.snow_cm >= 1
    bool_cols = m.select_dtypes(bool).columns
    m[bool_cols] = m[bool_cols].astype(int)

    ok = m.bad == 0
    # 대여 수준은 운영 중단일만 뺀다. 신고 누락 기간(2023-09-07 ~ 10-10)은 신고만 덜 잡혔고 대여는 정상이었다
    m["expo_lvl"] = m.rentals.where(m.no_service == 0).rolling(28, min_periods=14).mean().shift(7)
    m["expo_lvl7"] = m.rentals.where(m.no_service == 0).rolling(7, min_periods=4).mean().shift(7)   # 13~7일 전 평균
    for p in PARTS:
        # 2021-04-08 이전은 신고 수준이 다른 레짐이라 뺀다
        num = m[p].where(ok & (m.date >= "2021-04-08"))
        d2 = m.rentals.where(ok & (m.date >= "2021-04-08")).rolling(28, min_periods=14).sum().shift(7)
        m["lvl_" + p] = num.rolling(28, min_periods=14).sum().shift(7) / d2

    # 개입·환경 변수: 그 날짜 이후면 1. 날짜·출처는 data/raw/external/events.csv
    steps = {"qr_done": "2022-03-01", "kick_law": "2021-05-13", "kick_tow": "2021-07-15", "recycle1": "2023-03-01",
             "recycle2": "2024-03-01", "bus_fare": "2023-08-12", "subway_fare": "2023-10-07",
             "climate_card": "2024-01-27", "toss": "2024-02-06"}
    for k, d in steps.items():
        m[k] = (m.date >= d).astype(int)
    m["covid"] = m.date.between("2020-03-22", "2022-04-17").astype(int)     # 사회적 거리두기 기간
    return m


INTERVENTIONS = ["covid", "qr_done", "kick_law", "kick_tow", "recycle1", "recycle2", "bus_fare", "subway_fare",
                 "climate_card", "toss", "bikes_active_prev"]


def split_of(dates):
    """원점 날짜의 구간(train/valid/test). 어디에도 안 속하면 None"""
    d = pd.Series(pd.to_datetime(dates)).reset_index(drop=True)
    out = pd.Series([None] * len(d), dtype=object)
    for name, (a, b) in SPLITS.items():
        out[d.between(a, b)] = name
    return out.to_numpy()


def sum7_fwd(s):
    """원점 포함 앞으로 7일 합계(t ~ t+6). 예측 대상"""
    return s[::-1].rolling(7).sum()[::-1]


def sum_back(s, n):
    """원점 직전 n일 합계(t−n ~ t−1). 원점 당일은 포함하지 않는다"""
    return s.rolling(n).sum().shift(1)


def make_base(m, include_test=False):
    """원점 × 부품별 실제값(y)과 나이브 예측 4가지를 만든다.

    include_test=False(기본)면 테스트 구간 원점은 만들지 않는다. 실수로 테스트를 보지 않게 하려는 것.

      B1 지난주 합계               "다음 주 = 지난주". 기준 나이브(skill의 분모)
      B2 최근 4주 평균             4주 합계 / 4
      B3 작년 같은 주              364일 전부터 7일(요일을 맞추려고 364일)
      B4 작년 같은 주 × 최근 증감  B3 × (최근 4주 / 작년 같은 때 4주)
    clean: 원점 앞 28일 ~ 뒤 6일에 bad인 날이 없는 원점. 학습에는 이것만 쓴다
    """
    bad_near = (m.bad.rolling(35).sum().shift(-6) > 0).fillna(True)        # t−28 ~ t+6의 35일
    rows = []
    for p in PARTS:
        x = m[p]
        rows.append(pd.DataFrame({
            "origin": m.date, "i": np.arange(len(m)), "part": p, "y": sum7_fwd(x),
            "B1": sum_back(x, 7), "B2": sum_back(x, 28) / 4,
            "B3": sum7_fwd(x).shift(364),
            "B4": sum7_fwd(x).shift(364) * sum_back(x, 28) / sum_back(x, 28).shift(364),
            "clean": (~bad_near).to_numpy()}))
    base = pd.concat(rows, ignore_index=True)
    base["split"] = np.tile(split_of(m.date), len(PARTS))
    base = base.dropna(subset=["split", "y"])
    if not include_test:
        base = base[base.split != "test"]
    return base.reset_index(drop=True)


# ---------------------------------------------------------------------------
# 날씨 시나리오와 '미래 7일' 표
# ---------------------------------------------------------------------------

def _derive(f):
    """기온·강수·눈에서 파생 변수를 다시 계산한다. 정의는 마스터와 같다"""
    f["cold_wave"] = (f.tavg <= -4).astype(float)
    f["winter_anom"] = np.where(f.month.isin([12, 1, 2]), f.tavg - f.tavg_normal, 0.0)
    f["snow_any"] = (f.snow_cm >= 1).astype(float)
    f["hot"] = (f.tmax >= 30).astype(float)
    f["freeze_thaw"] = ((f.tmin < 0) & (f.tmax > 0)).astype(float)
    f["rain_any"] = (f.rain_mm >= 1).astype(float)
    return f


_KMA = None


def _kma_forecast():
    """기상청 과거 예보 표(한 번만 읽어 캐시)"""
    global _KMA
    if _KMA is None:
        _KMA = pd.read_parquet(ROOT / "data/interim/weather_forecast/kma_by_origin.parquet")
    return _KMA


def future_frame(m, origins_i, scn):
    """원점마다 앞으로 7일 행을 만든다(원점 수 × 7행). 날씨만 시나리오 값으로 바꾼다.

    m         : load_master() 결과
    origins_i : 원점 행 번호 배열
    scn       : "A"(실측) / "B"(기상청 예보) / "C"(오차 주입). D는 날씨를 뺀 모형을 쓰므로 표는 A와 같다
    반환: 마스터의 모든 열 + oi(원점 행 번호), lead(0~6).
      달력·수준 변수는 미리 알거나 과거 값이라 그대로 둔다.
      rain_lag1은 lead 0이면 실제 어제 값, 그 뒤는 시나리오의 전날 값.
    """
    origins_i = np.asarray(origins_i)
    idx = origins_i[:, None] + np.arange(7)[None, :]                     # (원점 수, 7) 날짜 행 번호
    f = m.iloc[idx.ravel()].copy().reset_index(drop=True)
    f["oi"] = np.repeat(origins_i, 7); f["lead"] = np.tile(np.arange(7), len(origins_i))
    n = len(origins_i)

    if scn == "C":
        # 가정한 예보 오차: 기온 표준편차 당일 1.0℃ → 6일 뒤 2.5℃, 비 유무 오답률 10% → 28%
        lead = np.arange(7)
        e = np.empty((n, 7)); flip = np.empty((n, 7), bool); noise = np.empty((n, 7))
        for k, oi in enumerate(origins_i):                               # 원점 번호를 시드로 써서 같은 원점은 늘 같은 오차
            rng = np.random.default_rng(int(oi))
            e[k] = rng.normal(0, 1.0 + 0.25 * lead)
            flip[k] = rng.random(7) < (0.10 + 0.03 * lead)
            noise[k] = np.exp(rng.normal(0, 0.5, 7))
        e, flip, noise = e.ravel(), flip.ravel(), noise.ravel()
        for c in ("tavg", "tmax", "tmin"):
            f[c] = f[c].to_numpy() + e
        rain = f.rain_mm.to_numpy(); wet = rain >= 1
        wet2 = np.where(flip, ~wet, wet)                                 # 비 유무를 뒤집은 날
        f["rain_mm"] = np.where(flip & wet2, 5.0, np.where(~wet2, 0.0, rain * noise))   # 맑음→비로 틀리면 5mm로 가정
        f["snow_cm"] = np.where(f.tavg.to_numpy() > 3, 0.0, f.snow_cm.to_numpy())      # 3℃ 넘게 예보된 날은 눈 없음
        f = _derive(f)
    elif scn == "B":
        # py/kma_forecast.py가 만든 과거 예보를 (원점, lead)로 붙인다
        kma = _kma_forecast()
        key = pd.DataFrame({"origin": m.date.to_numpy()[f.oi.to_numpy()], "lead": f.lead.to_numpy()})
        w = key.merge(kma, on=["origin", "lead"], how="left")
        if w.tavg.isna().any():
            miss = key[w.tavg.isna().to_numpy()].origin.dt.date.unique()
            raise ValueError(f"기상청 예보가 없는 원점이 있다: {miss[:5]} … ({len(miss)}일)")
        for c in ("tavg", "tmax", "tmin", "rain_mm", "snow_cm"):
            f[c] = w[c].to_numpy()
        f = _derive(f)
        f["rain_any"] = w.rain_any.to_numpy()          # 중기예보 구간은 강수확률(0~1), 단기예보 구간은 0/1
    elif scn not in ("A", "D"):
        raise ValueError("시나리오는 A, B, C, D 중 하나")

    # 전날 강수: lead 0은 실제 어제 값, 그 뒤는 시나리오의 전날 값
    r = f.rain_mm.to_numpy().reshape(n, 7)
    lag = np.empty_like(r)
    lag[:, 0] = m.rain_mm.to_numpy()[origins_i - 1]
    lag[:, 1:] = r[:, :-1]
    f["rain_lag1"] = lag.ravel()
    return f


# ---------------------------------------------------------------------------
# 음이항 회귀(예측용: HAC 없이 빠르게)와 노출 예측
# ---------------------------------------------------------------------------

def fit_nb_fc(formula, data, offset=None):
    """nbreg.fit_nb와 같은 음이항 적합. HAC 표준오차를 건너뛰어 빠르다. 반환: (적합 결과, alpha)"""
    def negll(log_alpha):
        fam = sm.families.NegativeBinomial(alpha=float(np.exp(log_alpha)))
        return -smf.glm(formula, data=data, family=fam, offset=offset).fit().llf
    alpha = float(np.exp(minimize_scalar(negll, bounds=(-9, 2), method="bounded", options={"xatol": 1e-3}).x))
    res = smf.glm(formula, data=data, family=sm.families.NegativeBinomial(alpha=alpha), offset=offset).fit()
    return res, alpha


# 대여량(노출) 모형: 최근 수준 + 요일·휴일 + 계절 + 날씨. 너무 춥거나 더우면 덜 타서 기온은 2차항까지
EXPO_CAL = "np.log(expo_lvl) + C(dow) + is_holiday + after_off + bridge + fs1 + fc1 + fs2 + fc2"
EXPO_WX = " + tavg + I(tavg**2) + cold_wave + rain_any + np.log1p(rain_mm) + snow_any"


def fit_expo(m, weather=True, end=TRAIN_DAYS[1]):
    """대여량 모형을 학습 구간(운영 중단일 제외)으로 맞춘다. weather=False면 날씨 없이(시나리오 D).

    end: 학습 마지막 날. 테스트 때는 2024-12-31까지 넣어 다시 맞춘다.
    """
    tr = m[m.date.between(TRAIN_DAYS[0], end) & (m.no_service == 0)].dropna(subset=["expo_lvl"])
    return fit_nb_fc("rentals ~ " + EXPO_CAL + (EXPO_WX if weather else ""), tr)[0]


# ---------------------------------------------------------------------------
# 채점
# ---------------------------------------------------------------------------

def score(preds, base, by=("split", "part", "model")):
    """예측을 채점한다.

    preds : 열 origin, part, model, pred (선택: q10, q90 = 80% 예측구간)
    모형끼리 같은 원점으로 비교하도록, 부품·구간마다 모든 모형이 예측을 낸 원점만 채점한다.
    반환: 모형별 n(원점 수), MAE, MAEpct, skill, bias, cover80(구간이 있을 때, 실제값이 구간 안에 든 비율)
    """
    df = preds.merge(base[["origin", "part", "split", "y", "B1"]], on=["origin", "part"])
    n_all = df.groupby(["split", "part"]).model.transform("nunique")
    n_here = df.groupby(["split", "origin", "part"]).model.transform("nunique")
    df = df[n_here == n_all].copy()
    df["ae"] = (df.pred - df.y).abs(); df["ae_b1"] = (df.B1 - df.y).abs(); df["err"] = df.pred - df.y
    if "q10" in df:
        df["inside"] = ((df.y >= df.q10) & (df.y <= df.q90)).where(df.q10.notna())
    g = df.groupby(list(by))
    out = g.agg(n=("y", "size"), MAE=("ae", "mean"), y_mean=("y", "mean"), MAE_B1=("ae_b1", "mean"), bias=("err", "mean"))
    out["MAEpct"] = 100 * out.MAE / out.y_mean
    out["skill"] = 1 - out.MAE / out.MAE_B1
    if "inside" in df:
        out["cover80"] = g.inside.mean()
    return out.drop(columns=["y_mean", "MAE_B1"]).reset_index()
