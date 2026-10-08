"""공통 스타일 — 라이트·다크 어느 쪽에서도 읽히는 한 벌(2026-10-08).
오른쪽 위 메뉴에서 테마를 바꿔도 화면이 다시 그려지지 않으므로(스트림릿 동작), 테마별로 다른 색을 넣으면 바꾸는 순간
'어두운 카드 위 어두운 글자'가 생긴다. 그래서 카드는 반투명 배경, 보조 글자·그래프 축은 중간 밝기 회색,
강조색은 양쪽 배경에서 대비가 나는 중간 톤만 쓴다. 지도 바탕만 그릴 때의 테마(st.context.theme.type)를 따른다.
도넛·게이지·3D·그라데이션은 쓰지 않는다. 테마 기본값은 .streamlit/config.toml"""
import plotly.graph_objects as go
import plotly.io as pio
import streamlit as st

COLORS = dict(
    card="rgba(127,127,127,0.06)", border="rgba(127,127,127,0.28)", muted="#8B8F94", grid="rgba(127,127,127,0.18)", axis="#8B8F94",
    text="#8B8F94",                                  # 그래프 안 숫자(막대 끝 등): 양쪽 배경에서 읽히는 회색
    primary="#2FA77E", primary_dark="#1D7A5C", primary_mid="#5DCAA5", primary_light="#DDF3EA", secondary="#8B8F94", naive="#A3A3A0",
    link="#4A8BEA", ok="#2FA875", warn="#C98A00", danger="#E0584D", whisker="#8B8F94",
    insp=("rgba(224,88,77,0.16)", "#E0584D"), caution=("rgba(201,138,0,0.16)", "#C98A00"),
    normal=("rgba(150,140,50,0.16)", "#9A8F2E"), low=("rgba(127,127,127,0.16)", "#8B8F94"), sel="#4A8BEA",
    dot=[224, 88, 77, 175], dot2=[74, 139, 234, 175],
)


def mode():
    try:
        return "light" if st.context.theme.type == "light" else "dark"
    except Exception:
        return "dark"


def P():
    """색 묶음(양쪽 테마 공통) + 지도 바탕(그릴 때의 테마)"""
    return COLORS | {"map_style": mode()}


pio.templates["dareungi"] = go.layout.Template(layout=dict(
    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font=dict(size=12, color=COLORS["axis"]),
    legend=dict(orientation="h", yanchor="top", y=-0.15, xanchor="left", x=0, font=dict(size=12)),
    xaxis=dict(gridcolor=COLORS["grid"], zeroline=False, linecolor=COLORS["grid"]), yaxis=dict(gridcolor=COLORS["grid"], zeroline=False),
    hoverlabel=dict(font=dict(size=12)),
    margin=dict(l=10, r=10, t=10, b=10), colorway=[COLORS["primary"], COLORS["secondary"], COLORS["primary_dark"], COLORS["primary_mid"]]))
pio.templates.default = "dareungi"


def css(p, top_pad="4.6rem"):
    return f"""
<style>
.block-container {{ padding-top: {top_pad}; max-width: 1320px; }}
div[data-testid="stVerticalBlockBorderWrapper"] {{ background: {p['card']}; border-color: {p['border']} !important; border-radius: 18px !important; }}
.page-title {{ font-size: 1.9rem; font-weight: 800; margin: 0; }}
.status-line {{ color: {p['muted']}; font-size: 0.92rem; margin-top: 4px; }}
.kpi-label {{ font-size: 0.95rem; margin-bottom: 4px; font-weight: 600; }}
.kpi-value {{ font-size: 2.4rem; font-weight: 800; line-height: 1.1; }}
.kpi-sub {{ color: {p['muted']}; font-size: 0.88rem; margin-top: 6px; line-height: 1.4; }}
.card-head {{ display: flex; justify-content: space-between; align-items: baseline; gap: 12px; }}
.card-title {{ font-size: 1.12rem; font-weight: 700; margin: 0; line-height: 1.5; }}
a.card-link {{ color: {p['link']} !important; font-size: 0.92rem; text-decoration: none; white-space: nowrap; font-weight: 600; }}
a.card-link:hover {{ text-decoration: underline; }}
.card-sub {{ color: {p['muted']}; font-size: 0.88rem; margin: 2px 0 8px; line-height: 1.45; }}
.card-caption {{ color: {p['muted']}; font-size: 0.85rem; line-height: 1.45; }}
.empty {{ color: {p['muted']}; font-size: 0.95rem; padding: 24px 0; text-align: center; }}
.loc {{ color: {p['muted']}; font-size: 0.78rem; margin-top: 2px; }}
.mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.98rem; }}
.bike-row {{ display: grid; grid-template-columns: 1.4fr 0.85fr 1.15fr; gap: 10px; align-items: center; padding: 10px 2px; border-bottom: 1px solid {p['border']}; }}
.bike-row:last-child {{ border-bottom: none; }}
.reason {{ font-size: 0.9rem; line-height: 1.35; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }}
.gbadge {{ display: inline-block; padding: 3px 8px; border-radius: 8px; font-size: 0.85rem; font-weight: 700; white-space: nowrap; }}
.g-insp {{ background: {p['insp'][0]}; color: {p['insp'][1]}; }}
.g-warn {{ background: {p['caution'][0]}; color: {p['caution'][1]}; }}
.g-norm {{ background: {p['normal'][0]}; color: {p['normal'][1]}; }}
.g-low  {{ background: {p['low'][0]}; color: {p['low'][1]}; }}
.tiles {{ display: grid; grid-template-columns: repeat(6, 1fr); gap: 6px; }}
.tile {{ border-radius: 8px; padding: 6px 2px; text-align: center; line-height: 1.15; font-size: 0.9rem; min-height: 50px; }}
.tile b {{ font-size: 1.05rem; }}
.tile.sel {{ outline: 3px solid {p['sel']}; }}
.big-num {{ font-size: 2.3rem; font-weight: 800; }}
.gu-picked {{ color: {p['muted']}; font-size: 0.85rem; text-align: right; margin-top: 2px; }}
</style>"""


def apply(top_pad="4.6rem"):
    """페이지마다 맨 처음 부른다. 돌려주는 것: 색 묶음"""
    p = P()
    st.markdown(css(p, top_pad), unsafe_allow_html=True)
    return p
