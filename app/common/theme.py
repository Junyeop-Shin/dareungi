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
    border="rgba(127,127,127,0.28)", muted="light-dark(#5F6368, #9AA0A6)", grid="rgba(127,127,127,0.18)", axis="#8B8F94",
    text="#8B8F94",                                  # 그래프 안 숫자(막대 끝 등): 양쪽 배경에서 읽히는 회색
    primary="#2FA77E", primary_dark="#1D7A5C", primary_mid="#5DCAA5", primary_light="#DDF3EA", secondary="#8B8F94", naive="#A3A3A0",
    link="light-dark(#12604A, #3CC08A)", ok="light-dark(#1D7A5C, #3CC08A)", danger="light-dark(#B4372D, #F0877E)", whisker="#8B8F94",
    dot=[29, 122, 92, 185],                          # 지도의 원: 세 목록 모두 강조색과 같은 초록(목록은 한 번에 하나만 보이므로 색으로 나눌 필요가 없다)
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
    legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="right", x=1, font=dict(size=12)),      # 범례는 그래프 오른쪽 위에
    xaxis=dict(showgrid=False, zeroline=False, linecolor=COLORS["grid"]), yaxis=dict(gridcolor=COLORS["grid"], zeroline=False),      # 격자선은 가로만
    hoverlabel=dict(font=dict(size=12)),
    margin=dict(l=10, r=10, t=30, b=10), colorway=[COLORS["primary"], COLORS["secondary"], COLORS["primary_dark"], COLORS["primary_mid"]]))
pio.templates.default = "dareungi"


def css(p):
    return f"""
<style>
.block-container {{ padding-top: 5.4rem; max-width: 1320px; }}
.page-title {{ font-size: 1.9rem; font-weight: 800; margin: 0; letter-spacing: -0.01em; }}
/* 위쪽 메뉴: 알약 모양 버튼 대신 글자 탭. 지금 화면은 굵은 글자와 아래 줄로 표시하고, 왼쪽 끝을 본문과 맞춘다 */
header[data-testid="stHeader"] {{ background: light-dark(#F5F6F5, #141414); border-bottom: 1px solid {p['border']}; }}
div[data-testid="stToolbar"] {{ padding-left: calc(max((100vw - 1320px) / 2, 0px) + 0.05rem); padding-right: calc(max((100vw - 1320px) / 2, 0px) + 0.2rem); }}
@media (min-width: 864px) {{ div[data-testid="stToolbar"] {{ padding-left: calc(max((100vw - 1320px) / 2, 0px) + 4.05rem); padding-right: calc(max((100vw - 1320px) / 2, 0px) + 3.9rem); }} }}
a[data-testid="stTopNavLink"] {{ height: 3.75rem; margin-right: 1.6rem; padding: 0 !important; border-radius: 0 !important; background: transparent !important;
          border-bottom: 2px solid transparent; }}
a[data-testid="stTopNavLink"] p {{ color: {p['muted']} !important; font-size: 0.95rem; font-weight: 500; }}
a[data-testid="stTopNavLink"]:hover p {{ color: light-dark(#1F2328, #ECECEC) !important; }}
a[data-testid="stTopNavLink"][aria-current="page"] {{ border-bottom-color: light-dark(#1F2328, #ECECEC); }}
a[data-testid="stTopNavLink"][aria-current="page"] p {{ color: light-dark(#1F2328, #ECECEC) !important; font-weight: 700; }}
a[data-testid="stTopNavLink"]:focus-visible {{ outline: 2px solid {p['link']}; outline-offset: -2px; }}
/* 머리 영역의 '자료 기준' 띠: 위아래 가는 선 사이에 자료별 칸을 나란히. 점은 정상(초록)·밀림(주황), 밀린 칸은 글자로도 며칠째인지 적는다 */
.fresh {{ display: flex; flex-wrap: wrap; align-items: center; row-gap: 6px; margin: 2px 0 6px; padding: 10px 0;
          border-top: 1px solid {p['border']}; border-bottom: 1px solid {p['border']}; font-size: 0.88rem; line-height: 1.4; }}
.fresh .fc {{ display: flex; align-items: center; gap: 8px; padding: 0 20px; border-left: 1px solid {p['border']}; }}
.fresh .fc:first-child {{ padding-left: 2px; border-left: none; }}
/* 제목 줄 오른쪽의 예측 정확도(유형별 화면). 지역 선택 버튼과 같은 자리·같은 높이 */
.conf-top {{ display: flex; justify-content: flex-end; align-items: center; gap: 8px; min-height: 2rem; font-size: 0.95rem; }}
.conf-top .fl {{ color: {p['muted']}; }}
.conf-top .fv {{ font-weight: 700; }}
.fresh .fl {{ color: {p['muted']}; }}
.fresh .fv {{ font-weight: 600; }}
.fresh .dot {{ flex: none; width: 8px; height: 8px; border-radius: 50%; background: light-dark(#1D7A5C, #3CC08A); }}
.fresh .late .dot {{ background: light-dark(#B37A00, #E0A526); }}
.fresh .late .fv {{ color: light-dark(#7A5400, #E0A526); }}
.kpi-label {{ font-size: 0.95rem; margin-bottom: 4px; font-weight: 600; }}
.kpi-value {{ font-size: 2.4rem; font-weight: 800; line-height: 1.1; }}
.kpi-sub {{ color: {p['muted']}; font-size: 0.88rem; margin-top: 6px; line-height: 1.4; }}
.kpi-value {{ font-variant-numeric: tabular-nums; }}
.kpi-value.txt {{ font-size: 1.25rem; line-height: 2.1; }}          /* 숫자가 아닌 값은 보통 크기로(줄 높이는 큰 숫자 칸과 맞춘다) */
.kpi-value .unit {{ font-size: 0.5em; font-weight: 700; margin-left: 3px; }}          /* 단위(건·대)는 숫자보다 작게 */
/* KPI 띠: 상자 없이 가는 세로선으로만 나눈 한 줄(머리 영역의 자료 기준 띠와 같은 문법). 칸 수는 style의 --n */
.kstrip {{ display: grid; grid-template-columns: repeat(var(--n), minmax(0, 1fr)); margin: 14px 0 18px; }}
.kstrip .kc {{ padding: 2px 22px; border-left: 1px solid {p['border']}; }}
.kstrip .kc:first-child {{ padding-left: 2px; border-left: none; }}
.kstrip.narrow .kc {{ padding: 2px 16px; }}
.kstrip.narrow .kpi-value {{ font-size: 1.9rem; }}          /* 유형별 화면의 7칸이 한 줄에 들어가는 크기 */
.kstrip.narrow .kpi-sub {{ font-size: 0.8rem; margin-top: 4px; }}
.card-head {{ display: flex; justify-content: space-between; align-items: baseline; gap: 12px; }}
.card-title {{ font-size: 1.12rem; font-weight: 700; margin: 0; line-height: 1.5; }}
.card-title-wrap {{ display: inline-flex; align-items: center; gap: 7px; }}
/* 제목 옆 물음표: 마우스를 올리거나 탭으로 가면 아래에 설명 상자가 뜬다 */
.qhelp {{ position: relative; display: inline-flex; align-items: center; justify-content: center; flex: none; width: 17px; height: 17px; border: 1.5px solid {p['muted']};
          border-radius: 50%; color: {p['muted']}; font-size: 11px; font-weight: 700; line-height: 1; cursor: help; }}
.qhelp .qtip {{ display: none; position: absolute; left: -140px; top: 25px; z-index: 1000; width: min(290px, 70vw); padding: 12px 14px;      /* 좁은 카드 안에서도 잘리지 않게 왼쪽으로 당겨 띄운다 */
          border-radius: 10px; background: light-dark(#1F2328, #ECECEC); color: light-dark(#FFFFFF, #141414); font-size: 0.85rem; font-weight: 400; line-height: 1.5;
          white-space: normal; text-align: left; cursor: default; box-shadow: 0 4px 14px rgba(0,0,0,0.25); }}
.qhelp:hover .qtip, .qhelp:focus .qtip {{ display: block; }}
.qhelp.to-left .qtip {{ left: auto; right: -8px; width: min(330px, 80vw); }}          /* 화면 오른쪽 끝에 있는 물음표는 왼쪽으로 펼친다 */
.qtip .qt-num {{ font-size: 1.7rem; font-weight: 800; line-height: 1.2; color: light-dark(#5DCAA5, #12604A); }}
.qtip .qt-num.dim {{ color: inherit; opacity: 0.75; }}
.qtip .qt-head {{ font-weight: 700; margin-bottom: 6px; }}
.qtip .qt-pair {{ display: grid; grid-template-columns: 1fr 1.4fr; gap: 14px; margin-bottom: 8px; }}
.qtip .qt-rule {{ margin-top: 8px; padding-top: 8px; border-top: 1px solid rgba(127,127,127,0.45); }}
.qtip .qt-rule div {{ opacity: 0.6; }}
.qtip .qt-rule div.on {{ opacity: 1; font-weight: 700; }}
a.card-link {{ color: {p['link']} !important; font-size: 0.92rem; text-decoration: none; white-space: nowrap; font-weight: 600;
               display: inline-flex; align-items: center; gap: 5px; }}
a.card-link:hover {{ text-decoration: underline; }}
a.card-link .plus {{ position: relative; flex: none; width: 17px; height: 17px; border: 1.5px solid currentColor; border-radius: 50%; font-size: 0; }}
/* + 글자는 글꼴마다 원 안에서 위아래로 치우쳐서, 가로·세로 막대를 직접 그려 한가운데에 둔다 */
a.card-link .plus::before, a.card-link .plus::after {{ content: ""; position: absolute; left: 50%; top: 50%; background: currentColor; transform: translate(-50%, -50%); }}
a.card-link .plus::before {{ width: 8px; height: 1.5px; }}
a.card-link .plus::after {{ width: 1.5px; height: 8px; }}
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
a.bike-link {{ color: inherit !important; text-decoration: underline; text-decoration-color: {p['border']}; text-underline-offset: 3px; }}
a.bike-link:hover {{ color: {p['link']} !important; text-decoration-color: currentColor; }}
.tiles {{ display: grid; grid-template-columns: repeat(6, 1fr); gap: 6px; }}
.tile {{ border-radius: 8px; padding: 6px 2px; text-align: center; line-height: 1.15; font-size: 0.9rem; min-height: 59px; white-space: nowrap;
         display: flex; flex-direction: column; justify-content: center; }}      /* 6줄이 옆 카드의 아래 끝과 맞는 높이 */
.tile b {{ font-size: 1.05rem; }}
.tile.dim {{ opacity: 0.3; }}
.big-num {{ font-size: 2.3rem; font-weight: 800; }}
.gu-picked {{ color: {p['muted']}; font-size: 0.85rem; text-align: right; margin-top: 2px; }}
/* 강조색 버튼(조회)의 글자: 어두운 테마에서는 밝은 초록 바탕이라 글자를 어둡게 */
button[kind^="primary"] p {{ color: light-dark(#FFFFFF, #0B1F17) !important; }}
/* 내려받기 버튼: 카드의 '자세히 보기' 링크와 같은 글자 모양 */
div[data-testid="stDownloadButton"] button {{ min-height: 0; padding: 0; }}
div[data-testid="stDownloadButton"] button p {{ color: {p['link']}; font-size: 0.92rem; font-weight: 600; }}
div[data-testid="stDownloadButton"] button:hover p {{ text-decoration: underline; }}
/* 구 선택 버튼: 옆의 권역 버튼과 같은 높이로 */
div[data-testid="stPopover"] button {{ min-height: 2rem; padding: 0 12px; font-size: 0.875rem; }}
/* 작은 범례(타일맵의 색 단계, 지도의 원 크기) */
.legend {{ display: flex; flex-wrap: wrap; align-items: center; gap: 6px 16px; margin-top: 10px; color: {p['muted']}; font-size: 0.82rem; }}
.legend span {{ display: inline-flex; align-items: center; gap: 6px; }}
.legend i {{ display: inline-block; flex: none; }}
/* 화면 아래: 가는 선 위에 작은 글씨 한 줄 */
.foot {{ margin-top: 28px; padding-top: 12px; border-top: 1px solid {p['border']}; color: {p['muted']}; font-size: 0.82rem; line-height: 1.5; }}
/* 자전거 조회 화면: 번호 · 결론 한 줄 · 있는 곳, 바로 조회 번호, 90일 신고 띠 */
.lk-head {{ display: flex; flex-wrap: wrap; align-items: baseline; gap: 6px 14px; margin: 10px 0 0; }}
.lk-head .mono {{ font-size: 1.25rem; font-weight: 700; }}
.lk-verdict {{ font-size: 1.25rem; font-weight: 800; color: light-dark(#7A5400, #E0A526); }}
.lk-verdict.none {{ color: inherit; font-weight: 700; }}
.lk-why div {{ padding: 8px 0; border-bottom: 1px solid {p['border']}; font-size: 0.92rem; }}          /* 선정 사유: 한 줄에 하나씩 */
.lk-msg {{ margin: 4px 0 2px; padding: 8px 12px; border-left: 3px solid light-dark(#B37A00, #E0A526); font-size: 0.92rem; line-height: 1.55; }}
.lk-where {{ color: {p['muted']}; font-size: 0.92rem; }}
a.pick {{ color: {p['link']} !important; text-decoration: none; padding: 3px 9px; border: 1px solid {p['border']}; border-radius: 999px; }}
a.pick:hover {{ border-color: currentColor; }}
a.pick span {{ color: {p['muted']}; }}
.tl-axis {{ stroke: {p['muted']}; stroke-width: 1; }}
.tl-dot {{ fill: light-dark(#1D7A5C, #3CC08A); stroke: light-dark(#F5F6F5, #141414); stroke-width: 1.5; }}
.tl-ends {{ display: flex; justify-content: space-between; margin: -4px 2% 6px; color: {p['muted']}; font-size: 0.78rem; }}
/* 이름(왼쪽, 회색) · 값(오른쪽) 줄: 자전거 조회 화면의 카드 안 */
.drow {{ display: flex; justify-content: space-between; gap: 16px; padding: 9px 0; border-bottom: 1px solid {p['border']}; font-size: 0.92rem; line-height: 1.5; }}
.drow:last-child {{ border-bottom: none; }}
.drow span:first-child {{ color: {p['muted']}; white-space: nowrap; }}
.drow span:last-child {{ text-align: right; }}
/* Streamlit 기본 회색 글자(st.caption)와 그래프의 축·범례 글자도 같은 보조 글자색으로(밝은 테마에서 대비 부족) */
div[data-testid="stCaptionContainer"], div[data-testid="stCaptionContainer"] p {{ color: {p['muted']} !important; opacity: 1 !important; }}
.js-plotly-plot .xtick text, .js-plotly-plot .ytick text, .js-plotly-plot .legendtext, .js-plotly-plot .annotation-text {{ fill: {p['muted']} !important; }}
div[data-testid="stAlertContainer"] p {{ color: light-dark(#1F2328, #ECECEC); }}
/* 인용 상자(예측 방법의 식)는 Streamlit 기본이 흐린 회색이라 본문 글자색으로 */
div[data-testid="stMarkdownContainer"] blockquote, div[data-testid="stMarkdownContainer"] blockquote p {{ color: light-dark(#1F2328, #ECECEC); opacity: 1; }}
/* 유형별 화면 맨 아래 '예측 방법' 설명: 글자 크기는 카드 설명(.card-sub)과 같게, 문단·목록은 보조 글자색, 식과 표는 본문 글자색 */
.st-key-method_box div[data-testid="stMarkdownContainer"] :is(p, li, td, th) {{ font-size: 0.88rem; line-height: 1.5; }}
.st-key-method_box div[data-testid="stMarkdownContainer"] > p, .st-key-method_box div[data-testid="stMarkdownContainer"] li {{ color: {p['muted']}; }}
.st-key-method_box div[data-testid="stMarkdownContainer"] strong {{ color: light-dark(#1F2328, #ECECEC); }}
/* 접는 상자: 펼쳤을 때 머리줄에 깔리는 바탕색을 없애고 글자는 본문색으로(카드 안에서 따로 놀지 않게) */
div[data-testid="stExpander"] summary, div[data-testid="stExpander"] summary:hover {{ background: transparent !important; color: inherit !important; }}
div[data-testid="stExpander"] details {{ border-color: {p['border']} !important; }}
/* 스크립트만 넣은 칸(문서 언어, 탭 이름)이 화면에 빈 줄을 만들지 않게 */
div[data-testid="stElementContainer"]:has(> div[data-testid="stHtml"]) {{ display: none; }}
/* 입력칸 안의 영어 안내("Press Enter to submit form · 5/12")는 숨긴다 */
div[data-testid="InputInstructions"] {{ display: none; }}
/* 자전거 조회의 입력칸·조회 버튼·바로 조회: 손가락으로 누르는 기기와 좁은 화면에서는 높이 44px 이상 */
@media (pointer: coarse), (max-width: 640px) {{
  .st-key-lookup_box input, .st-key-lookup_box div[data-baseweb="input"], .st-key-lookup_box button {{ min-height: 44px; }}
  .st-key-lookup_box a.pick {{ display: inline-flex; align-items: center; min-height: 44px; padding: 0 12px; }}
}}
/* 표 위에 뜨는 영어 도구(Search, Fullscreen 등)는 숨긴다. 내려받기는 표 아래 버튼으로 한다 */
div[data-testid="stElementToolbar"] {{ display: none; }}
/* 좁은 화면(태블릿, 200% 확대): 옆으로 나란한 칸을 아래로 쌓고, 고정 높이 카드는 내용만큼 늘린다 */
@media (max-width: 1000px) {{
  div[data-testid="stHorizontalBlock"] {{ flex-wrap: wrap; }}
  .fresh .fc {{ flex: 1 1 100%; padding: 0 2px; border-left: none; }}          /* 자료 기준 띠는 한 줄에 한 칸씩 */
  .kstrip {{ grid-template-columns: repeat(2, minmax(0, 1fr)); row-gap: 16px; }}
  .kstrip .kc, .kstrip.narrow .kc {{ padding: 2px 12px 2px 2px; border-left: none; }}
  div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"] {{ min-width: min(100%, 300px); }}
  div[data-testid="stLayoutWrapper"], div[data-testid="stVerticalBlock"] {{ height: auto !important; max-height: none !important; }}          /* 높이를 고정한 카드도 내용만큼 */
}}
</style>"""


def apply():
    """페이지마다 맨 처음 불러 CSS를 넣고 색 묶음을 돌려준다."""
    p = P()
    st.markdown(css(p), unsafe_allow_html=True)
    # Streamlit은 문서 언어를 영어(en)로 내보낸다. 화면낭독기가 한글을 한국어 음성으로 읽도록 ko로 바꾼다
    st.html("<script>document.documentElement.lang = 'ko'</script>", unsafe_allow_javascript=True)
    return p
