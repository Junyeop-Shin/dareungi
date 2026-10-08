"""스냅샷 읽기와 신선도 — 08a 설계서 2절. 앱은 serving/*.parquet만 읽는다(DB·인증키 없음). 10분 캐시"""
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
SERVING = ROOT / "serving"
STALE_H = 48


@st.cache_data(ttl=600)
def load(name):
    p = SERVING / f"{name}.parquet"
    return pd.read_parquet(p) if p.exists() else None


def status():
    """data_status → {데이터: Timestamp}. 없으면 빈 dict"""
    s = load("data_status")
    if s is None:
        return {}
    out = {}
    for k, v in zip(s.data, s.latest):
        try:
            out[k] = pd.to_datetime(v).tz_localize(None) if pd.to_datetime(v).tzinfo else pd.to_datetime(v)
        except Exception:
            pass
    return out


def stale_items(st_):
    """48시간 넘게 오래된 데이터 목록 [(이름, 며칠 전)]"""
    now = pd.Timestamp.now()
    out = []
    for k in ("대여이력", "고장신고", "날씨 관측", "발행 시각"):
        if k in st_:
            age_h = (now.value - pd.Timestamp(st_[k]).value) / 3.6e12          # ns → 시간 (Timedelta 생성 경고 회피)
            if age_h > STALE_H:
                out.append((k, int(age_h // 24)))
    return out


def get_gu():
    """자치구 필터(여러 개). 주소(query_params)와 session_state를 맞춘다. 빈 목록 = 서울 전체"""
    if "gu" not in st.session_state:
        q = st.query_params.get_all("gu") if hasattr(st.query_params, "get_all") else []
        st.session_state["gu"] = [g for g in q if g]
    return st.session_state["gu"]


def set_gu(gus):
    st.session_state["gu"] = list(gus)
    if gus:
        st.query_params["gu"] = list(gus)
    elif "gu" in st.query_params:
        del st.query_params["gu"]


def apply_gu(df, col="district"):
    gus = get_gu()
    return df if (df is None or not gus or col not in df) else df[df[col].isin(gus)]


def since(hours):
    """지금부터 hours시간 전 시각. 스냅샷의 datetime64[us]와 단위를 맞추고, pd.Timedelta(현재 pandas에서 numpy 경고를 냄)를 쓰지 않는다"""
    return np.datetime64(pd.Timestamp.now().to_datetime64(), "us") - np.timedelta64(int(hours), "h")


def shift_days(x, n):
    """날짜(스칼라 또는 열)에 n일을 더한다. pandas의 DateOffset·Timedelta 연산은 현재 버전에서 numpy 경고를 내서 numpy로 계산한다"""
    if isinstance(x, (pd.Series, pd.Index)):
        return pd.to_datetime(x.to_numpy().astype("datetime64[us]") + np.timedelta64(int(n), "D"))
    return pd.Timestamp(np.datetime64(pd.Timestamp(x).to_datetime64(), "us") + np.timedelta64(int(n), "D"))
