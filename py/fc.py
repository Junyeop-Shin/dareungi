"""단계 1C 예측 비교용 공용 함수 — 과제 정의, 나이브, 날씨 시나리오, 노출 예측, 채점

[과제]
예측 시점(원점) t에서 **부품별 '앞으로 7일(t ~ t+6) 신고 건수 합계'**를 맞힌다.
  - 쓸 수 있는 정보: t−1일까지의 모든 관측값, t ~ t+6의 달력(요일·공휴일은 미리 안다), t ~ t+6의 날씨(시나리오에 따라)
  - 쓸 수 없는 정보: t 이후의 대여량·신고 건수. 대여량(노출)도 따로 예측해야 한다

[원점 구간] (7일 합계가 구간 안에 들어가도록 끝을 6일 당긴다)
  학습 2021-04-08 ~ 2023-12-25 / 검증 2024-01-01 ~ 2024-12-25 / 테스트 2025-01-01 ~ 2026-06-24
  테스트 구간은 사람이 승인하기 전에는 예측도 채점도 하지 않는다.

[날씨 시나리오] 미래 7일의 날씨를 얼마나 아는가
  A 실측     : 실제 관측값을 그대로 쓴다. 완벽한 예보가 있을 때의 상한선(1차 분석이 쓴 방식)
  C 오차 주입: 실측에 예보 오차를 흉내 낸 잡음을 더한다. 현실에 가까운 추정. 오차 크기는 가정이다
  D 날씨 없음: 날씨 변수를 아예 뺀 모형으로 예측한다. 날씨 정보가 전혀 없을 때
               (평년값을 대신 넣는 방법은 쓰지 않는다. "매일 평균 강수량만큼 비가 온다"가 되어 대여량을 30% 가까이 낮게 예측했다)

[지표]
  MAE     : 예측과 실제의 차이(절댓값) 평균. 단위는 건
  MAE%    : MAE ÷ 실제 평균 × 100
  skill   : 1 − MAE ÷ 나이브(B1)의 MAE. 0.10 = 나이브보다 오차가 10% 작다. 음수면 나이브보다 못하다
  bias    : 예측 − 실제의 평균. 양수면 과대예측

노트북에서:  from fc import *
"""
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm
import statsmodels.formula.api as smf
from scipy.optimize import minimize_scalar

ROOT = Path(__file__).resolve().parent.parent

# 맞힐 대상: 열 이름 → 표·그림에 쓸 이름. 부품별은 행 수, 전체는 신고 건수
PARTS = {"fail_tire": "타이어", "fail_chain": "체인", "fail_saddle": "안장", "fail_pedal": "페달", "total_faults": "전체"}

SPLITS = {"train": ("2021-04-08", "2023-12-25"), "valid": ("2024-01-01", "2024-12-25"), "test": ("2025-01-01", "2026-06-24")}
TRAIN_DAYS = ("2021-04-08", "2023-12-31")          # 일 단위 모형(노출·음이항)을 맞출 때 쓰는 날짜 범위

# 예보로 알 수 있는 날씨 원자료(이것만 시나리오로 바꾼다). 일사량·지면온도 등은 예보가 없어 쓰지 않는다
WX_RAW = ["tavg", "tmax", "tmin", "rain_mm", "snow_cm"]
# 원자료에서 계산하는 파생 날씨 변수(시나리오를 바꾸면 다시 계산한다)
WX_DERIVED = ["cold_wave", "winter_anom", "snow_any", "hot", "freeze_thaw", "rain_any"]


# ---------------------------------------------------------------------------
# 자료 준비
# ---------------------------------------------------------------------------

def load_master():
    """일별 마스터를 읽어 예측에 필요한 열을 붙인다.

    추가하는 열
      bad        : 학습·수준 계산에서 빼는 날(운영 중단일, 신고 누락 기간)
      snow_any, rain_any : 눈(1cm 이상)·비(1mm 이상) 온 날
      expo_lvl   : 노출의 최근 수준 = 34일 전 ~ 7일 전(28일) 하루 평균 대여건수.
                   '7일 전까지'만 쓰는 이유: 원점 t에서 t+6일을 예측할 때도 t−1일까지의 정보만 쓰게 하려고(미래 정보 차단)
      lvl_<부품> : 그 부품 신고율의 최근 수준 = 같은 28일의 신고 합계 ÷ 대여 합계(bad인 날은 빼고 계산)
      개입·환경 변수(1C에서 넣은 것·뺀 것을 비교): covid, qr_done, kick_law, kick_tow, recycle1, recycle2, bus_fare, subway_fare, climate_card, toss
    참/거짓 열은 1/0으로 바꾼다(시나리오 D에서 0.3 같은 평균값을 넣어야 하므로).
    """
    m = pd.read_parquet(ROOT / "data/processed/daily_master_v2.parquet").sort_values("date").reset_index(drop=True)
    m["bad"] = m.no_service | m.report_anomaly
    m["snow_any"] = m.snow_cm >= 1
    bool_cols = m.select_dtypes(bool).columns
    m[bool_cols] = m[bool_cols].astype(int)

    ok = m.bad == 0
    m["expo_lvl"] = m.rentals.where(ok).rolling(28, min_periods=14).mean().shift(7)
    den = m.rentals.where(ok).rolling(28, min_periods=14).sum().shift(7)
    for p in PARTS:
        # 2021-04-08 이전은 신고가 잡히는 정도가 달랐던 기간이라 수준 계산에 넣지 않는다
        num = m[p].where(ok & (m.date >= "2021-04-08"))
        d2 = m.rentals.where(ok & (m.date >= "2021-04-08")).rolling(28, min_periods=14).sum().shift(7)
        m["lvl_" + p] = num.rolling(28, min_periods=14).sum().shift(7) / d2

    # 개입·환경 변수: 그 날짜 이후면 1 (날짜·출처는 data/raw/external/events.csv)
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
    """원점 날짜가 어느 구간(train/valid/test)에 속하는지. 어디에도 안 속하면 None"""
    d = pd.Series(pd.to_datetime(dates)).reset_index(drop=True)
    out = pd.Series([None] * len(d), dtype=object)
    for name, (a, b) in SPLITS.items():
        out[d.between(a, b)] = name
    return out.to_numpy()


def sum7_fwd(s):
    """원점 포함 앞으로 7일 합계(t ~ t+6). 이것이 맞힐 값이다"""
    return s[::-1].rolling(7).sum()[::-1]


def sum_back(s, n):
    """원점 직전 n일 합계(t−n ~ t−1). 원점 당일은 포함하지 않는다"""
    return s.rolling(n).sum().shift(1)


def make_base(m, include_test=False):
    """원점 × 부품별로 실제값(y)과 나이브 예측 4가지를 만든다.
    include_test=False(기본)면 테스트 구간 원점은 아예 만들지 않는다(사람이 승인하기 전에 실수로 보지 않도록).

      B1 지난주 합계                 : "다음 주 = 지난주". 기준 나이브(skill의 분모)
      B2 최근 4주 평균               : 4주 합계 ÷ 4
      B3 작년 같은 주                : 364일 전부터 7일(요일이 맞도록 364일)
      B4 작년 같은 주 × 최근 증감률  : B3에 '최근 4주 ÷ 작년 같은 때 4주'를 곱한다
    clean = 원점 앞 28일 ~ 뒤 6일에 bad인 날이 하나도 없는 원점(학습에는 이것만 쓴다)
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
    """날씨 원자료(기온·강수·눈)에서 파생 변수를 다시 계산한다(마스터를 만들 때와 같은 정의)"""
    f["cold_wave"] = (f.tavg <= -4).astype(float)
    f["winter_anom"] = np.where(f.month.isin([12, 1, 2]), f.tavg - f.tavg_normal, 0.0)
    f["snow_any"] = (f.snow_cm >= 1).astype(float)
    f["hot"] = (f.tmax >= 30).astype(float)
    f["freeze_thaw"] = ((f.tmin < 0) & (f.tmax > 0)).astype(float)
    f["rain_any"] = (f.rain_mm >= 1).astype(float)
    return f


def future_frame(m, origins_i, scn):
    """원점마다 '앞으로 7일'의 행을 만든다(원점 수 × 7행). 날씨는 시나리오에 따라 바꾼다.

    m         : load_master()의 결과
    origins_i : 원점의 행 번호 배열
    scn       : "A"(실측) / "C"(오차 주입). 시나리오 D(날씨 없음)는 날씨를 뺀 모형을 쓰는 것이라 표는 A와 같다
    돌려주는 표의 열: 마스터의 모든 열 + oi(원점 행 번호), lead(0 ~ 6일 뒤).
      달력·수준 변수는 그 날짜의 값 그대로(미리 알거나 과거 값이므로), 날씨만 시나리오 값.
      rain_lag1(전날 강수)은 첫날은 실제 어제 값, 둘째 날부터는 시나리오의 전날 값.
    """
    origins_i = np.asarray(origins_i)
    idx = origins_i[:, None] + np.arange(7)[None, :]                     # (원점 수, 7) 날짜 행 번호
    f = m.iloc[idx.ravel()].copy().reset_index(drop=True)
    f["oi"] = np.repeat(origins_i, 7); f["lead"] = np.tile(np.arange(7), len(origins_i))
    n = len(origins_i)

    if scn == "C":
        # 예보 오차 흉내(가정): 기온 오차의 표준편차는 당일 1.0℃ → 6일 뒤 2.5℃, 비 유무를 틀릴 확률은 10% → 28%
        lead = np.arange(7)
        e = np.empty((n, 7)); flip = np.empty((n, 7), bool); noise = np.empty((n, 7))
        for k, oi in enumerate(origins_i):                               # 같은 원점은 언제나 같은 오차를 받도록 원점 번호를 난수 씨앗으로
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
        f["snow_cm"] = np.where(f.tavg.to_numpy() > 3, 0.0, f.snow_cm.to_numpy())      # 따뜻하다고 예보된 날은 눈 없음
        f = _derive(f)
    elif scn not in ("A", "D"):
        raise ValueError("시나리오는 A, C, D 중 하나")

    # 전날 강수: 첫날(lead 0)은 실제 어제 값, 그 뒤는 시나리오의 전날 값
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
    """음이항 회귀를 맞춘다(py/nbreg.py의 fit_nb와 같은 방식이되 표준오차는 계산하지 않아 빠르다). 돌려주는 것: (적합 결과, alpha)"""
    def negll(log_alpha):
        fam = sm.families.NegativeBinomial(alpha=float(np.exp(log_alpha)))
        return -smf.glm(formula, data=data, family=fam, offset=offset).fit().llf
    alpha = float(np.exp(minimize_scalar(negll, bounds=(-9, 2), method="bounded", options={"xatol": 1e-3}).x))
    res = smf.glm(formula, data=data, family=sm.families.NegativeBinomial(alpha=alpha), offset=offset).fit()
    return res, alpha


# 대여량(노출) 모형의 식: 최근 수준 + 요일·쉬는 날 + 계절 + 날씨. 기온은 2차식(너무 춥거나 더우면 덜 탄다)
EXPO_CAL = "np.log(expo_lvl) + C(dow) + is_holiday + after_off + bridge + fs1 + fc1 + fs2 + fc2"
EXPO_WX = " + tavg + I(tavg**2) + cold_wave + rain_any + np.log1p(rain_mm) + snow_any"


def fit_expo(m, weather=True):
    """대여량 예측 모형을 학습 구간의 날들로 맞춘다(운영 중단일은 빼고). weather=False면 날씨 없이(시나리오 D용)"""
    tr = m[m.date.between(*TRAIN_DAYS) & (m.no_service == 0)].dropna(subset=["expo_lvl"])
    return fit_nb_fc("rentals ~ " + EXPO_CAL + (EXPO_WX if weather else ""), tr)[0]


# ---------------------------------------------------------------------------
# 채점
# ---------------------------------------------------------------------------

def score(preds, base, by=("split", "part", "model")):
    """예측을 채점한다.

    preds : 열 origin, part, model, pred (있으면 q10, q90 = 80% 예측구간의 아래·위)
    부품·구간마다 '모든 모형이 예측을 낸 원점'만 채점한다(모형마다 원점이 다르면 공정한 비교가 아니다).
    돌려주는 것: 모형별 n(원점 수), MAE, MAE%, skill, bias, (구간이 있으면) cover80 = 실제값이 구간 안에 든 비율
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
