"""공통 스타일(CSS, 색, plotly 템플릿).

Streamlit은 오른쪽 위 메뉴에서 테마를 바꿔도 화면을 다시 그리지 않는다. 파이썬에서 테마별로 다른 색을 넣으면 바꾸는 순간
어두운 카드 위에 어두운 글자가 남는다. 그래서 카드·그래프 색은 양쪽에서 읽히는 한 벌을 쓰고,
글자색(보조 글자·링크·등급 글자)만 CSS light-dark(밝은 테마 색, 어두운 테마 색)로 적는다. Streamlit이 .stApp에
color-scheme을 달아 주므로 테마를 바꾸면 브라우저가 바로 색을 바꾼다. 한 가지 회색으로는 양쪽 배경에서
글자 대비 4.5:1(WCAG AA)을 동시에 맞출 수 없어서다.
지도 바탕만 그릴 때의 테마(st.context.theme.type)를 따른다.
차트에는 도넛·게이지·3D·그라데이션을 쓰지 않는다.

테마 기본값은 .streamlit/config.toml에 있다.
"""
import plotly.graph_objects as go
import plotly.io as pio
import streamlit as st

COLORS = dict(
    card="rgba(127,127,127,0.06)", border="rgba(127,127,127,0.28)", muted="light-dark(#5F6368, #9AA0A6)", grid="rgba(127,127,127,0.18)", axis="#8B8F94",
    text="#8B8F94",                                  # 그래프 안 숫자(막대 끝 등): 양쪽 배경에서 읽히는 회색
    primary="#2FA77E", primary_dark="#1D7A5C", primary_mid="#5DCAA5", primary_light="#DDF3EA", secondary="#8B8F94", naive="#A3A3A0",
    link="light-dark(#1F4FB5, #6BA4F5)", ok="light-dark(#1D7A5C, #3CC08A)", danger="light-dark(#B4372D, #F0877E)", whisker="#8B8F94",
    dot=[224, 88, 77, 175], dot2=[74, 139, 234, 175],
)


def mode():
    try:
        return "light" if st.context.theme.type == "light" else "dark"
    except Exception:
        return "dark"


def P():
    """색 묶음(양쪽 테마 공통)에 지도 바탕 스타일(그릴 때의 테마)을 더해 돌려준다."""
    return COLORS | {"map_style": mode() + "_no_labels"}      # 바탕 지도의 영어 지명을 빼고 한글 구 이름을 따로 얹는다


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
.kpi-center {{ text-align: center; }}
.card-head {{ display: flex; justify-content: space-between; align-items: baseline; gap: 12px; }}
.card-title {{ font-size: 1.12rem; font-weight: 700; margin: 0; line-height: 1.5; }}
a.card-link {{ color: {p['link']} !important; font-size: 0.92rem; text-decoration: none; white-space: nowrap; font-weight: 600; }}
a.card-link:hover {{ text-decoration: underline; }}
a.card-link .plus {{ display: inline-flex; align-items: center; justify-content: center; width: 18px; height: 18px; margin-left: 5px;
                    border: 1.5px solid currentColor; border-radius: 50%; font-size: 14px; line-height: 1; vertical-align: -3px; }}
.card-sub {{ color: {p['muted']}; font-size: 0.88rem; margin: 2px 0 8px; line-height: 1.45; }}
.card-caption {{ color: {p['muted']}; font-size: 0.85rem; line-height: 1.45; }}
.empty {{ color: {p['muted']}; font-size: 0.95rem; padding: 24px 0; text-align: center; }}
.loc {{ color: {p['muted']}; font-size: 0.78rem; margin-top: 2px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.98rem; }}
.bike-row {{ display: grid; grid-template-columns: 1.2rem minmax(0, 1fr) auto; gap: 10px; align-items: center; padding: 10px 2px; border-bottom: 1px solid {p['border']}; }}
.bike-row:last-child {{ border-bottom: none; }}
.reason {{ font-size: 0.85rem; line-height: 1.35; margin-top: 3px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.score {{ font-size: 0.9rem; text-align: right; white-space: nowrap; }}
.rank {{ font-weight: 700; color: {p['muted']}; }}
.tiles {{ display: grid; grid-template-columns: repeat(6, 1fr); gap: 6px; }}
.tile {{ border-radius: 8px; padding: 6px 2px; text-align: center; line-height: 1.15; font-size: 0.9rem; min-height: 50px; white-space: nowrap; }}
.tile b {{ font-size: 1.05rem; }}
.tile.dim {{ opacity: 0.3; }}
.big-num {{ font-size: 2.3rem; font-weight: 800; }}
.gu-picked {{ color: {p['muted']}; font-size: 0.85rem; text-align: right; margin-top: 2px; }}
/* Streamlit 기본 회색 글자(st.caption)와 그래프의 축·범례 글자도 같은 보조 글자색으로(밝은 테마에서 대비 부족) */
div[data-testid="stCaptionContainer"], div[data-testid="stCaptionContainer"] p {{ color: {p['muted']} !important; opacity: 1 !important; }}
.js-plotly-plot .xtick text, .js-plotly-plot .ytick text, .js-plotly-plot .legendtext, .js-plotly-plot .annotation-text {{ fill: {p['muted']} !important; }}
div[data-testid="stAlertContainer"] p {{ color: light-dark(#1F2328, #ECECEC); }}
/* 인용 상자(예상 방법의 식)는 Streamlit 기본이 흐린 회색이라 본문 글자색으로 */
div[data-testid="stMarkdownContainer"] blockquote, div[data-testid="stMarkdownContainer"] blockquote p {{ color: light-dark(#1F2328, #ECECEC); opacity: 1; }}
/* 부품별 화면 맨 아래 '예상 방법' 설명: 글자 크기는 카드 설명(.card-sub)과 같게, 문단·목록은 보조 글자색, 식과 표는 본문 글자색 */
.st-key-method_box div[data-testid="stMarkdownContainer"] :is(p, li, td, th) {{ font-size: 0.88rem; line-height: 1.5; }}
.st-key-method_box div[data-testid="stMarkdownContainer"] > p, .st-key-method_box div[data-testid="stMarkdownContainer"] li {{ color: {p['muted']}; }}
.st-key-method_box div[data-testid="stMarkdownContainer"] strong {{ color: light-dark(#1F2328, #ECECEC); }}
/* 접는 상자: 펼쳤을 때 머리줄에 깔리는 바탕색을 없애고 글자는 본문색으로(카드 안에서 따로 놀지 않게) */
div[data-testid="stExpander"] summary, div[data-testid="stExpander"] summary:hover {{ background: transparent !important; color: inherit !important; }}
div[data-testid="stExpander"] details {{ border-color: {p['border']} !important; }}
/* 표 위에 뜨는 영어 도구(Search, Fullscreen 등)는 숨긴다. 내려받기는 표 아래 버튼으로 한다 */
div[data-testid="stElementToolbar"] {{ display: none; }}
/* 좁은 화면(태블릿, 200% 확대): 옆으로 나란한 칸을 아래로 쌓고, 고정 높이 카드는 내용만큼 늘린다 */
@media (max-width: 1000px) {{
  div[data-testid="stHorizontalBlock"] {{ flex-wrap: wrap; }}
  .kpi-sub {{ margin-bottom: 14px; }}
  div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"] {{ min-width: min(100%, 300px); }}
  div[data-testid="stVerticalBlockBorderWrapper"], div[data-testid="stVerticalBlockBorderWrapper"] > div {{ height: auto !important; max-height: none !important; }}
}}
</style>"""


def apply(top_pad="4.6rem"):
    """페이지마다 맨 처음 불러 CSS를 넣고 색 묶음을 돌려준다."""
    p = P()
    st.markdown(css(p, top_pad), unsafe_allow_html=True)
    # Streamlit은 문서 언어를 영어(en)로 내보낸다. 화면낭독기가 한글을 한국어 음성으로 읽도록 ko로 바꾼다
    st.html("<script>document.documentElement.lang = 'ko'</script>", unsafe_allow_javascript=True)
    return p
