import os
import pickle
import threading
from datetime import date, datetime, timedelta
from io import BytesIO

import dash
import pandas as pd
from dash import Input, Output, State, dcc, html, dash_table
from dash.exceptions import PreventUpdate
from pytz import timezone
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4, portrait
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


dash.register_page(
    __name__,
    path="/fracionamento_pdm",
    name="Fracionamento de Despesas PDM",
    title="Fracionamento de Despesas PDM",
)

URL_BI_ITABIRA = (
    "https://docs.google.com/spreadsheets/d/"
    "1Fwzgfc7o-R8Ly6rz2-V_KcvJjKpECrNexj2qPOvgLys/"
    "gviz/tq?tqx=out:csv&sheet=BI%20-%20Itabira"
)

COL_PDM_ORIG = "Cod PDM"
COL_DESC_ORIG = "Descrição.1"
COL_VALOR_ORIG = "Valor.1"
COL_PDM_OUT = "PDM"

VALOR_LIMITE_2026 = 65492.11
CACHE_TTL_MINUTOS = 60
PAGE_SIZE_PADRAO = 15
TZ_SP = timezone("America/Sao_Paulo")

COLS_TABELA_PDM = [
    COL_PDM_OUT,
    "Descrição",
    "Valor Empenhado",
    "Limite da Dispensa",
    "Saldo para contratação",
]

_CACHE_LOCK = threading.Lock()
_DF_CACHE = None
_DF_CACHE_AT = None
_CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", ".cache", "fracionamento_pdm")
_CACHE_FILE = os.path.join(_CACHE_DIR, "df.pkl")
_CACHE_META = os.path.join(_CACHE_DIR, "meta.pkl")
os.makedirs(_CACHE_DIR, exist_ok=True)


def now_sp():
    return datetime.now(TZ_SP)


def format_datetime_sp(dt):
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = TZ_SP.localize(dt)
    else:
        dt = dt.astimezone(TZ_SP)
    return dt.strftime("%d/%m/%Y %H:%M:%S")


def fmt_moeda(v):
    if pd.isna(v):
        return ""
    return "R$ " + f"{float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def carregar_dados_limite_pdm():
    df = pd.read_csv(URL_BI_ITABIRA)
    df.columns = [c.strip() for c in df.columns]

    if COL_PDM_ORIG not in df.columns:
        df[COL_PDM_ORIG] = ""
    if COL_DESC_ORIG not in df.columns:
        df[COL_DESC_ORIG] = ""
    if COL_VALOR_ORIG not in df.columns:
        df[COL_VALOR_ORIG] = 0.0

    df[COL_PDM_OUT] = (
        df[COL_PDM_ORIG]
        .astype(str)
        .str.strip()
        .str.replace(r"\.0$", "", regex=True)
        .str.replace(r"\D", "", regex=True)
        .str.zfill(5)
    )

    serie_valor = df[COL_VALOR_ORIG].astype(str).str.strip()
    serie_valor = (
        serie_valor.str.replace("R$", "", regex=False)
        .str.replace("\xa0", " ", regex=False)
        .str.replace(" ", "", regex=False)
        .str.replace(".", "", regex=False)
        .str.replace(",", ".", regex=False)
    )
    df["Valor Empenhado"] = pd.to_numeric(serie_valor, errors="coerce").fillna(0.0)
    df["Descrição"] = df[COL_DESC_ORIG].fillna("").astype(str)
    df["Limite da Dispensa"] = VALOR_LIMITE_2026
    df["Saldo para contratação"] = df["Limite da Dispensa"] - df["Valor Empenhado"]

    return df


def _load_disk_cache():
    try:
        if not (os.path.exists(_CACHE_FILE) and os.path.exists(_CACHE_META)):
            return None, None
        with open(_CACHE_META, "rb") as file:
            meta = pickle.load(file)
        cached_at = meta.get("cached_at")
        if not cached_at:
            return None, None
        cached_at_dt = datetime.fromisoformat(cached_at)
        age = datetime.now() - cached_at_dt
        if age > timedelta(minutes=CACHE_TTL_MINUTOS):
            return None, None
        return pd.read_pickle(_CACHE_FILE), cached_at_dt
    except Exception:
        return None, None


def _save_disk_cache(df, cached_at):
    try:
        df.to_pickle(_CACHE_FILE)
        with open(_CACHE_META, "wb") as file:
            pickle.dump({"cached_at": cached_at.isoformat()}, file)
    except Exception:
        pass


def get_df_pdm(force=False):
    global _DF_CACHE, _DF_CACHE_AT

    now_naive = datetime.now()
    stale = (
        _DF_CACHE is None
        or _DF_CACHE_AT is None
        or (now_naive - _DF_CACHE_AT > timedelta(minutes=CACHE_TTL_MINUTOS))
    )

    if force or stale:
        with _CACHE_LOCK:
            now_check = datetime.now()
            stale_check = (
                _DF_CACHE is None
                or _DF_CACHE_AT is None
                or (now_check - _DF_CACHE_AT > timedelta(minutes=CACHE_TTL_MINUTOS))
            )

            if (not force) and stale_check:
                df_disk, at_disk = _load_disk_cache()
                if df_disk is not None and at_disk is not None:
                    _DF_CACHE = df_disk
                    _DF_CACHE_AT = now_check
                    return _DF_CACHE, f"Dados carregados do cache em disco ({format_datetime_sp(at_disk)})."

            if force or stale_check:
                df = carregar_dados_limite_pdm()
                _DF_CACHE = df
                _DF_CACHE_AT = now_check
                _save_disk_cache(df, now_check)
                return _DF_CACHE, f"Dados recarregados da planilha ({format_datetime_sp(now_sp())})."

    return _DF_CACHE, f"Dados em cache (memória) - verificado em {format_datetime_sp(now_sp())}."


def pdms_unicos(df_base):
    if df_base is None or df_base.empty or COL_PDM_OUT not in df_base.columns:
        return []
    return sorted(
        value
        for value in df_base[COL_PDM_OUT].dropna().unique()
        if isinstance(value, str) and value.strip() not in ("", "00000")
    )


def filtrar_dados_pdm(df_base, pdm_lista=None):
    dff = df_base.copy() if df_base is not None else pd.DataFrame()
    if dff.empty:
        return dff

    dff = dff[dff[COL_PDM_OUT] != "00000"]
    if pdm_lista:
        dff = dff[dff[COL_PDM_OUT].isin(pdm_lista)]

    for col in COLS_TABELA_PDM:
        if col not in dff.columns:
            dff[col] = pd.NA
    return dff


def preparar_payload_tabela_pdm(dff):
    dff_display = dff[COLS_TABELA_PDM].copy()
    dff_display["Valor Empenhado_fmt"] = dff_display["Valor Empenhado"].apply(fmt_moeda)
    dff_display["Limite da Dispensa_fmt"] = dff_display["Limite da Dispensa"].apply(fmt_moeda)
    dff_display["Saldo para contratação_fmt"] = dff_display["Saldo para contratação"].apply(fmt_moeda)
    return dff_display


botao_limpar_style = {
    "backgroundColor": "#9ca3af",
    "color": "white",
    "border": "1px solid #9ca3af",
    "borderRadius": "4px",
    "padding": "6px 12px",
    "cursor": "pointer",
    "fontWeight": "bold",
}

botao_atualizar_style = {
    "backgroundColor": "#0b2b57",
    "color": "white",
    "border": "1px solid #0b2b57",
    "borderRadius": "4px",
    "padding": "6px 12px",
    "cursor": "pointer",
    "fontWeight": "bold",
}

botao_pdf_style = {
    "backgroundColor": "#d92d20",
    "color": "white",
    "border": "1px solid #d92d20",
    "borderRadius": "4px",
    "padding": "6px 12px",
    "cursor": "pointer",
    "fontWeight": "bold",
}

card_padrao_style = {
    "border": "1px solid #e5e7eb",
    "borderRadius": "8px",
    "padding": "8px 12px",
    "backgroundColor": "#ffffff",
    "minWidth": "140px",
    "width": "140px",
    "height": "54px",
    "boxShadow": "0 6px 18px rgba(15, 23, 42, 0.10)",
    "fontSize": "11px",
    "display": "flex",
    "flexDirection": "column",
    "justifyContent": "center",
}

texto_orientacao_style = {
    "flex": "0 0 36%",
    "borderRight": "1px solid #e5e7eb",
    "padding": "12px 18px",
    "minWidth": "380px",
    "maxWidth": "560px",
    "fontSize": "13px",
    "lineHeight": "1.25",
    "textAlign": "left",
    "backgroundColor": "#ffffff",
    "color": "#111827",
    "overflowY": "auto",
    "height": "100vh",
    "boxSizing": "border-box",
}

painel_dados_style = {
    "flex": "1 1 64%",
    "padding": "14px 16px",
    "minWidth": "0",
    "backgroundColor": "#f3f4f6",
    "boxSizing": "border-box",
}

cabecalho_painel_style = {
    "backgroundColor": "#0b2b57",
    "borderRadius": "8px",
    "padding": "16px",
    "marginBottom": "12px",
    "display": "flex",
    "alignItems": "center",
    "justifyContent": "space-between",
    "gap": "16px",
    "flexWrap": "wrap",
    "boxShadow": "0 8px 22px rgba(15, 23, 42, 0.16)",
}

filtros_fracionamento_style = {
    "backgroundColor": "#ffffff",
    "border": "1px solid #e5e7eb",
    "borderRadius": "8px",
    "padding": "10px 12px",
    "marginBottom": "10px",
    "boxShadow": "0 2px 8px rgba(15, 23, 42, 0.06)",
}

alerta_orientacao_style = {
    "backgroundColor": "#d92d20",
    "color": "#ffffff",
    "borderRadius": "8px",
    "padding": "10px 12px",
    "margin": "10px 0 0 0",
    "fontWeight": "600",
    "lineHeight": "1.35",
    "boxShadow": "0 4px 12px rgba(217, 45, 32, 0.25)",
}


layout = html.Div(
    style={
        "display": "flex",
        "flexDirection": "row",
        "width": "100%",
        "minHeight": "100vh",
        "gap": "0",
        "backgroundColor": "#f3f4f6",
    },
    children=[
        html.Div(
            id="coluna_esquerda_pdm",
            style=texto_orientacao_style,
            children=[
                html.Div(
                    "Limite de Gasto - Itabira por PDM",
                    style={
                        "fontSize": "20px",
                        "fontWeight": "800",
                        "lineHeight": "1.2",
                        "color": "#0b2b57",
                        "marginBottom": "10px",
                    },
                ),
                html.P("Prezado requisitante,"),
                html.P(
                    "Em atenção ao acórdão nº 324/2009 Plenário TCU, "
                    "“Planeje adequadamente as compras e a contratação de "
                    "serviços durante o exercício financeiro, de forma a "
                    "evitar a prática de fracionamento de despesas”."
                ),
                html.P("Assim dispõe a IN SEGES/ME nº 67/2021:"),
                html.P(
                    "Art. 4º Os órgãos e entidades adotarão a dispensa de "
                    "licitação, na forma eletrônica, nas seguintes hipóteses:"
                ),
                html.P(
                    "[...] § 2º Considera-se ramo de atividade a linha de "
                    "fornecimento registrada pelo fornecedor quando do seu "
                    "cadastramento no Sistema de Cadastramento Unificado de "
                    "Fornecedores (Sicaf), vinculada:"
                ),
                html.P(
                    "I - à classe de materiais, utilizando o Padrão "
                    "Descritivo de Materiais (PDM) do Sistema de Catalogação "
                    "de Material do Governo federal; ou"
                ),
                html.P(
                    "II - à descrição dos serviços ou das obras, constante do "
                    "Sistema de Catalogação de Serviços ou de Obras do "
                    "Governo federal. (NR)"
                ),
                html.P("Em resumo: Para materiais - PDM; para serviços - CATSER."),
                html.P(
                    [
                        "Para obtenção do PDM: no catálogo de compras disponível em ",
                        html.A(
                            "https://catalogo.compras.gov.br/cnbs-web/busca",
                            href="https://catalogo.compras.gov.br/cnbs-web/busca",
                            target="_blank",
                            style={"color": "#1d4ed8", "textDecoration": "underline"},
                        ),
                        ", informar o número do CATMAT. Exemplo para o CATMAT 605322: a consulta "
                        "retornará PDM: 8320. Esse é o número que deverá ser considerado.",
                    ]
                ),
                html.P("Exemplo para a necessidade de contratação de três itens:"),
                html.P(
                    "1) o somatório do valor obtido na pesquisa de mercado para "
                    "cada um dos itens multiplicado por seu quantitativo não "
                    "poderá exceder o limite da dispensa."
                ),
                html.P(
                    "2) O valor por item deverá obrigatoriamente ser igual ou "
                    "inferior ao saldo para contratação (PDM ou CATSER) desse item."
                ),
                html.P(
                    "Os valores informados na tabela são os já empenhados no "
                    "exercício por PDM ou CATSER."
                ),
                html.Div(
                    "O processo de compra deverá vir instruído já na modalidade "
                    "DISPENSA DE LICITAÇÃO. A tela de consulta (Relatório PDF) "
                    "deverá estar apensado ao processo, que será conferido pelo "
                    "Setor de Compras e, somente a partir do resultado dessa "
                    "conferência, o processo prosseguirá.",
                    style=alerta_orientacao_style,
                ),
            ],
        ),
        html.Div(
            id="coluna_direita_pdm",
            style=painel_dados_style,
            children=[
                html.Div(
                    style=cabecalho_painel_style,
                    children=[
                        html.Div(
                            style={
                                "display": "flex",
                                "flexDirection": "column",
                                "gap": "6px",
                                "flex": "1 1 420px",
                                "minWidth": "320px",
                                "maxWidth": "680px",
                            },
                            children=[
                                html.Div(
                                    "Fracionamento de Despesas PDM",
                                    style={
                                        "color": "#ffffff",
                                        "fontSize": "22px",
                                        "fontWeight": "800",
                                        "lineHeight": "1.1",
                                    },
                                ),
                                html.Div(
                                    children=[
                                        html.Span(
                                            "O valor global do processo de compra não poderá exceder esse limite."
                                        ),
                                        html.Br(),
                                        html.Span(
                                            "O valor de cada item não poderá exceder o Saldo para Contratação."
                                        ),
                                    ],
                                    style={
                                        "color": "#ffffff",
                                        "fontSize": "12px",
                                        "lineHeight": "1.25",
                                    },
                                ),
                            ],
                        ),
                        html.Div(
                            style={
                                "display": "flex",
                                "alignItems": "center",
                                "gap": "12px",
                                "flexWrap": "wrap",
                            },
                            children=[
                                html.Div(
                                    style=card_padrao_style,
                                    children=[
                                        html.Div(
                                            "Limite da dispensa (2026)",
                                            style={
                                                "fontWeight": "bold",
                                                "color": "#374151",
                                                "marginBottom": "1px",
                                                "textAlign": "center",
                                                "lineHeight": "1.1",
                                            },
                                        ),
                                        html.Div(
                                            fmt_moeda(VALOR_LIMITE_2026),
                                            style={
                                                "fontSize": "16px",
                                                "fontWeight": "bold",
                                                "color": "#166534",
                                                "textAlign": "center",
                                            },
                                        ),
                                    ],
                                ),
                                html.Div(
                                    style=card_padrao_style,
                                    children=[
                                        html.Div(
                                            "Data da consulta",
                                            style={
                                                "fontWeight": "bold",
                                                "color": "#374151",
                                                "marginBottom": "1px",
                                                "textAlign": "center",
                                                "lineHeight": "1.1",
                                            },
                                        ),
                                        html.Div(
                                            id="card_data_consulta_pdm",
                                            children=date.today().strftime("%d/%m/%Y"),
                                            style={
                                                "fontSize": "16px",
                                                "fontWeight": "bold",
                                                "color": "#111827",
                                                "textAlign": "center",
                                            },
                                        ),
                                    ],
                                ),
                            ],
                        ),
                    ],
                ),
                html.Div(
                    id="barra_filtros_limite_itabira_pdm",
                    className="filtros-sticky",
                    style=filtros_fracionamento_style,
                    children=[
                        html.Div(
                            style={
                                "display": "flex",
                                "flexWrap": "wrap",
                                "gap": "10px",
                                "alignItems": "flex-start",
                            },
                            children=[
                                html.Div(
                                    style={
                                        "minWidth": "220px",
                                        "flex": "1 1 260px",
                                        "maxHeight": "60px",
                                    },
                                    children=[
                                        html.Label("PDM (digitação)"),
                                        dcc.Input(
                                            id="filtro_pdm_texto_itabira",
                                            type="text",
                                            placeholder=(
                                                "Digite parte do PDM, selecione na lista e, "
                                                "após a seleção, apague o texto digitado."
                                            ),
                                            style={
                                                "width": "100%",
                                                "marginBottom": "8px",
                                                "height": "30px",
                                                "border": "1px solid #cbd5e1",
                                                "borderRadius": "4px",
                                                "padding": "4px 8px",
                                                "boxSizing": "border-box",
                                            },
                                        ),
                                    ],
                                ),
                            ],
                        ),
                        html.Div(
                            style={
                                "marginTop": "4px",
                                "display": "flex",
                                "flexWrap": "wrap",
                                "gap": "10px",
                                "alignItems": "flex-start",
                            },
                            children=[
                                html.Div(
                                    style={
                                        "minWidth": "220px",
                                        "flex": "1 1 100%",
                                        "maxHeight": "104px",
                                        "overflowY": "auto",
                                        "border": "1px solid #cbd5e1",
                                        "borderRadius": "4px",
                                        "padding": "6px 8px",
                                        "fontSize": "11px",
                                        "backgroundColor": "#f8fafc",
                                    },
                                    children=[
                                        html.Label("PDM (lista)"),
                                        dcc.Checklist(
                                            id="filtro_pdm_lista_itabira",
                                            options=[],
                                            value=[],
                                            style={
                                                "display": "flex",
                                                "flexWrap": "wrap",
                                                "justifyContent": "center",
                                                "columnGap": "10px",
                                                "rowGap": "2px",
                                            },
                                            inputStyle={"marginRight": "4px"},
                                            labelStyle={
                                                "display": "inline-block",
                                                "width": "14%",
                                                "fontSize": "11px",
                                                "lineHeight": "1.5",
                                            },
                                        ),
                                    ],
                                ),
                            ],
                        ),
                        html.Div(
                            style={
                                "marginTop": "10px",
                                "display": "flex",
                                "alignItems": "center",
                                "gap": "10px",
                                "flexWrap": "wrap",
                            },
                            children=[
                                html.Button(
                                    "Limpar filtros",
                                    id="btn_limpar_filtros_limite_itabira_pdm",
                                    n_clicks=0,
                                    style=botao_limpar_style,
                                ),
                                html.Button(
                                    "Atualizar dados",
                                    id="btn_reload_pdm",
                                    n_clicks=0,
                                    style=botao_atualizar_style,
                                ),
                                html.Button(
                                    "Baixar Relatório PDF",
                                    id="btn_download_relatorio_limite_itabira_pdm",
                                    n_clicks=0,
                                    style=botao_pdf_style,
                                ),
                                dcc.Download(id="download_relatorio_limite_itabira_pdm"),
                                html.Div(
                                    id="info-atualizacao-pdm",
                                    style={"fontSize": "12px", "color": "#333"},
                                ),
                            ],
                        ),
                    ],
                ),
                dash_table.DataTable(
                    id="tabela_limite_itabira_pdm",
                    columns=[
                        {"name": "PDM", "id": COL_PDM_OUT},
                        {"name": "Descrição", "id": "Descrição"},
                        {"name": "Valor Empenhado (R$)", "id": "Valor Empenhado_fmt"},
                        {"name": "Limite da Dispensa (R$)", "id": "Limite da Dispensa_fmt"},
                        {"name": "Saldo para contratação (R$)", "id": "Saldo para contratação_fmt"},
                    ],
                    data=[],
                    page_action="custom",
                    page_current=0,
                    page_size=PAGE_SIZE_PADRAO,
                    row_selectable=False,
                    cell_selectable=False,
                    style_table={
                        "overflowX": "hidden",
                        "overflowY": "auto",
                        "width": "100%",
                        "height": "calc(100vh - 405px)",
                        "minHeight": "260px",
                        "position": "relative",
                        "border": "1px solid #e5e7eb",
                        "borderRadius": "8px",
                        "backgroundColor": "#ffffff",
                    },
                    style_cell={
                        "textAlign": "center",
                        "padding": "7px 8px",
                        "fontSize": "12px",
                        "fontFamily": "Arial, sans-serif",
                        "minWidth": "0",
                        "maxWidth": "none",
                        "whiteSpace": "normal",
                        "height": "auto",
                        "lineHeight": "1.35",
                    },
                    style_cell_conditional=[
                        {"if": {"column_id": COL_PDM_OUT}, "width": "10%"},
                        {"if": {"column_id": "Descrição"}, "width": "34%", "textAlign": "left"},
                        {"if": {"column_id": "Valor Empenhado_fmt"}, "width": "18%"},
                        {"if": {"column_id": "Limite da Dispensa_fmt"}, "width": "18%"},
                        {"if": {"column_id": "Saldo para contratação_fmt"}, "width": "20%"},
                    ],
                    css=[
                        {
                            "selector": ".dash-spreadsheet-container .dash-spreadsheet-inner table",
                            "rule": "table-layout: fixed; width: 100%;",
                        },
                    ],
                    style_header={
                        "fontWeight": "bold",
                        "backgroundColor": "#0b2b57",
                        "color": "white",
                        "textAlign": "center",
                        "position": "sticky",
                        "top": 0,
                        "zIndex": 5,
                    },
                    style_data_conditional=[
                        {"if": {"row_index": "odd"}, "backgroundColor": "#f5f5f5"},
                        {
                            "if": {"filter_query": "{Saldo para contratação} < 0"},
                            "backgroundColor": "#ffcccc",
                            "color": "#b42318",
                        },
                        {
                            "if": {
                                "filter_query": "{Saldo para contratação} > 0 && {Saldo para contratação} != {Limite da Dispensa}"
                            },
                            "backgroundColor": "#dcfce7",
                            "color": "#166534",
                        },
                    ],
                ),
                dcc.Store(id="store-reload-pdm"),
                dcc.Interval(id="interval-reload-pdm", interval=60 * 60 * 1000, n_intervals=0),
            ],
        ),
    ],
)


@dash.callback(
    Output("store-reload-pdm", "data"),
    Output("info-atualizacao-pdm", "children"),
    Output("filtro_pdm_lista_itabira", "options"),
    Output("card_data_consulta_pdm", "children"),
    Input("url", "pathname"),
    Input("interval-reload-pdm", "n_intervals"),
    Input("btn_reload_pdm", "n_clicks"),
    State("filtro_pdm_lista_itabira", "value"),
)
def carregar_ao_abrir_interval_ou_recarregar_pdm(pathname, _n_intervals, _n_clicks, selecionados):
    if pathname != "/fracionamento_pdm":
        raise PreventUpdate

    force = dash.ctx.triggered_id == "btn_reload_pdm"
    df, status = get_df_pdm(force=force)

    base = pdms_unicos(df)
    selecionados = selecionados or []
    selecionados_validos = [value for value in selecionados if value in base]

    opcoes = [{"label": value, "value": value} for value in base]
    msg = html.Div([html.B("Dados disponíveis. "), html.Span(status)])
    return (
        {"ts": datetime.now().isoformat(), "sel": selecionados_validos},
        msg,
        opcoes,
        now_sp().strftime("%d/%m/%Y"),
    )


@dash.callback(
    Output("filtro_pdm_lista_itabira", "options", allow_duplicate=True),
    Input("filtro_pdm_texto_itabira", "value"),
    Input("store-reload-pdm", "data"),
    State("filtro_pdm_lista_itabira", "value"),
    prevent_initial_call=True,
)
def atualizar_opcoes_pdm(pdm_texto, _reload, valores_selecionados):
    df, _ = get_df_pdm(force=False)
    base = pdms_unicos(df)

    if not pdm_texto or not str(pdm_texto).strip():
        filtradas = base
    else:
        termo = str(pdm_texto).strip().lower()
        filtradas = [value for value in base if termo in str(value).lower()]

    valores_selecionados = valores_selecionados or []
    for value in valores_selecionados:
        if value in base and value not in filtradas:
            filtradas.append(value)

    return [{"label": value, "value": value} for value in sorted(filtradas)]


@dash.callback(
    Output("tabela_limite_itabira_pdm", "data"),
    Output("tabela_limite_itabira_pdm", "page_count"),
    Input("store-reload-pdm", "data"),
    Input("filtro_pdm_lista_itabira", "value"),
    Input("tabela_limite_itabira_pdm", "page_current"),
    Input("tabela_limite_itabira_pdm", "page_size"),
)
def atualizar_tabela_limite_itabira_pdm(_reload, pdm_lista, page_current, page_size):
    df_base, _ = get_df_pdm(force=False)
    dff = filtrar_dados_pdm(df_base, pdm_lista)

    if dff.empty:
        return [], 0

    page_current = page_current or 0
    page_size = page_size or PAGE_SIZE_PADRAO
    page_count = max(1, (len(dff) + page_size - 1) // page_size)
    page_current = min(page_current, page_count - 1)
    start = page_current * page_size
    end = start + page_size
    dff_payload = preparar_payload_tabela_pdm(dff.iloc[start:end])

    return dff_payload.to_dict("records"), page_count


@dash.callback(
    Output("filtro_pdm_texto_itabira", "value"),
    Output("filtro_pdm_lista_itabira", "value"),
    Input("btn_limpar_filtros_limite_itabira_pdm", "n_clicks"),
    prevent_initial_call=True,
)
def limpar_filtros_limite_itabira_pdm(_n):
    return None, []


wrap_style_data_pdm = ParagraphStyle(
    name="wrap_limite_itabira_pdm_data",
    fontSize=7,
    leading=9,
    alignment=TA_CENTER,
    textColor=colors.black,
)

wrap_style_header_pdm = ParagraphStyle(
    name="wrap_limite_itabira_pdm_header",
    fontSize=7,
    leading=9,
    alignment=TA_CENTER,
    textColor=colors.white,
)

wrap_style_desc_pdm = ParagraphStyle(
    name="wrap_limite_itabira_pdm_desc",
    fontSize=7,
    leading=9,
    alignment=TA_LEFT,
    textColor=colors.black,
)


def wrap_data_pdm(text):
    return Paragraph(str(text), wrap_style_data_pdm)


def wrap_header_pdm(text):
    return Paragraph(str(text), wrap_style_header_pdm)


def wrap_desc_pdm(text):
    return Paragraph(str(text), wrap_style_desc_pdm)


@dash.callback(
    Output("download_relatorio_limite_itabira_pdm", "data"),
    Input("btn_download_relatorio_limite_itabira_pdm", "n_clicks"),
    State("filtro_pdm_lista_itabira", "value"),
    prevent_initial_call=True,
)
def gerar_pdf_limite_itabira_pdm(n, pdm_lista):
    if not n:
        return None

    df_base, _ = get_df_pdm(force=False)
    df = filtrar_dados_pdm(df_base, pdm_lista)
    if df.empty:
        return None

    buffer = BytesIO()
    pagesize = portrait(A4)
    doc = SimpleDocTemplate(
        buffer,
        pagesize=pagesize,
        rightMargin=0.3 * inch,
        leftMargin=0.3 * inch,
        topMargin=0.2 * inch,
        bottomMargin=0.4 * inch,
    )

    styles = getSampleStyleSheet()
    story = []

    data_hora_brasilia = now_sp().strftime("%d/%m/%Y %H:%M:%S")
    data_top_table = Table(
        [[
            Paragraph(
                data_hora_brasilia,
                ParagraphStyle("data_topo_pdm", fontSize=9, alignment=TA_RIGHT, textColor="#333333"),
            )
        ]],
        colWidths=[pagesize[0] - 0.6 * inch],
    )
    data_top_table.setStyle(TableStyle([("ALIGN", (0, 0), (-1, -1), "RIGHT"), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(data_top_table)
    story.append(Spacer(1, 0.15 * inch))

    logo_esq = Image("assets/brasaobrasil.png", 1.0 * inch, 1.0 * inch) if os.path.exists("assets/brasaobrasil.png") else ""
    logo_dir = Image("assets/simbolo_RGB.png", 1.0 * inch, 1.0 * inch) if os.path.exists("assets/simbolo_RGB.png") else ""

    texto_instituicao = (
        "<b><font color='#0b2b57' size=12>Ministério da Educação</font></b><br/>"
        "<b><font color='#0b2b57' size=12>Universidade Federal de Itabira</font></b><br/>"
        "<font color='#0b2b57' size=10>"
        "Coordenação de Compras e Contratos<br/>"
        "Campus de Itabira"
        "</font>"
    )

    instituicao = Paragraph(
        texto_instituicao,
        ParagraphStyle("instituicao_pdm", alignment=TA_CENTER, leading=14),
    )

    cabecalho = Table([[logo_esq, instituicao, logo_dir]], colWidths=[1.2 * inch, 3.5 * inch, 1.2 * inch])
    cabecalho.setStyle(
        TableStyle(
            [
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.append(cabecalho)
    story.append(Spacer(1, 0.25 * inch))

    titulo_paragraph = Paragraph(
        "Consulta ao Fracionamento de Despesa 2026 - PDM (Material): UASG: 158161 - Campus Itabira",
        ParagraphStyle("titulo_pdm", alignment=TA_CENTER, fontSize=10, leading=14, textColor=colors.black),
    )
    story.append(titulo_paragraph)
    story.append(Spacer(1, 0.15 * inch))
    story.append(Paragraph(f"Total de registros: {len(df)}", styles["Normal"]))
    story.append(Spacer(1, 0.1 * inch))

    cols = COLS_TABELA_PDM
    df_pdf = df.copy()
    for col in cols:
        if col not in df_pdf.columns:
            df_pdf[col] = ""
    for col in cols[2:]:
        df_pdf[col] = df_pdf[col].apply(fmt_moeda)

    header = [wrap_header_pdm(col) for col in cols]
    table_data = [header]
    saldo_values = pd.to_numeric(df["Saldo para contratação"], errors="coerce").fillna(0).tolist()

    for _, row in df_pdf[cols].iterrows():
        row_data = []
        for index, col in enumerate(cols):
            row_data.append(wrap_desc_pdm(row[col]) if index == 1 else wrap_data_pdm(row[col]))
        table_data.append(row_data)

    tbl = Table(
        table_data,
        colWidths=[0.8 * inch, 2.5 * inch, 1.0 * inch, 1.0 * inch, 1.0 * inch],
        repeatRows=1,
    )

    table_styles = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0b2b57")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("ALIGN", (1, 1), (1, -1), "LEFT"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.whitesmoke]),
    ]

    for row_idx, saldo in enumerate(saldo_values, 1):
        if saldo <= 0:
            table_styles.append(("BACKGROUND", (0, row_idx), (-1, row_idx), colors.HexColor("#ffcccc")))
            table_styles.append(("TEXTCOLOR", (0, row_idx), (-1, row_idx), colors.HexColor("#cc0000")))

    tbl.setStyle(TableStyle(table_styles))
    story.append(tbl)

    doc.build(story)
    buffer.seek(0)

    return dcc.send_bytes(
        buffer.getvalue(),
        f"limite_gasto_itabira_pdm_{datetime.now().strftime('%Y%m%d%H%M%S')}.pdf",
    )
