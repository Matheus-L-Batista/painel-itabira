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
    path="/fracionamento_catser",
    name="Fracionamento de Despesas CATSER",
    title="Fracionamento de Despesas CATSER",
)

URL_BI_ITABIRA = (
    "https://docs.google.com/spreadsheets/d/"
    "1Fwzgfc7o-R8Ly6rz2-V_KcvJjKpECrNexj2qPOvgLys/"
    "gviz/tq?tqx=out:csv&sheet=BI%20-%20Itabira"
)

COL_COD_CAT_ORIG = "Cod CAT"
COL_DESC_ORIG = "Descrição"
COL_VALOR_ORIG = "Valor"
COL_CATSER_OUT = "CATSER"

VALOR_LIMITE_2026 = 65492.11
CACHE_TTL_MINUTOS = 60
PAGE_SIZE_PADRAO = 15
TZ_SP = timezone("America/Sao_Paulo")

COLS_TABELA_CATSER = [
    COL_CATSER_OUT,
    "Descrição",
    "Valor Empenhado",
    "Limite da Dispensa",
    "Saldo para contratação",
]

_CACHE_LOCK = threading.Lock()
_DF_CACHE = None
_DF_CACHE_AT = None
_CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", ".cache", "fracionamento_catser")
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


def carregar_dados_limite_catser():
    df = pd.read_csv(URL_BI_ITABIRA)
    df.columns = [c.strip() for c in df.columns]

    if COL_COD_CAT_ORIG not in df.columns:
        df[COL_COD_CAT_ORIG] = ""
    if COL_DESC_ORIG not in df.columns:
        df[COL_DESC_ORIG] = ""
    if COL_VALOR_ORIG not in df.columns:
        df[COL_VALOR_ORIG] = 0.0

    df[COL_CATSER_OUT] = (
        df[COL_COD_CAT_ORIG]
        .astype(str)
        .str.strip()
        .str.replace(r"\.0$", "", regex=True)
        .str.replace(r"\D", "", regex=True)
        .str.zfill(6)
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


def get_df_catser(force=False):
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
                df = carregar_dados_limite_catser()
                _DF_CACHE = df
                _DF_CACHE_AT = now_check
                _save_disk_cache(df, now_check)
                return _DF_CACHE, f"Dados recarregados da planilha ({format_datetime_sp(now_sp())})."

    return _DF_CACHE, f"Dados em cache (memória) - verificado em {format_datetime_sp(now_sp())}."


def catsers_unicos(df_base):
    if df_base is None or df_base.empty or COL_CATSER_OUT not in df_base.columns:
        return []
    return sorted(
        value
        for value in df_base[COL_CATSER_OUT].dropna().unique()
        if isinstance(value, str) and value.strip() not in ("", "000000")
    )


def filtrar_dados_catser(df_base, catser_lista=None):
    dff = df_base.copy() if df_base is not None else pd.DataFrame()
    if dff.empty:
        return dff

    dff = dff[dff[COL_CATSER_OUT] != "000000"]
    if catser_lista:
        dff = dff[dff[COL_CATSER_OUT].isin(catser_lista)]

    for col in COLS_TABELA_CATSER:
        if col not in dff.columns:
            dff[col] = pd.NA
    return dff


def preparar_payload_tabela_catser(dff):
    dff_display = dff[COLS_TABELA_CATSER].copy()
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
            id="coluna_esquerda_catser",
            style=texto_orientacao_style,
            children=[
                html.Div(
                    "Limite de Gasto - Itabira por CATSER",
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
                        "Para obtenção do CATSER: no catálogo de compras disponível em ",
                        html.A(
                            "https://catalogo.compras.gov.br/cnbs-web/busca",
                            href="https://catalogo.compras.gov.br/cnbs-web/busca",
                            target="_blank",
                            style={"color": "#1d4ed8", "textDecoration": "underline"},
                        ),
                        ", informar o número do CATSER.",
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
            id="coluna_direita_catser",
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
                                    "Fracionamento de Despesas CATSER",
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
                                            id="card_data_consulta_catser",
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
                    id="barra_filtros_limite_itabira_catser",
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
                                        html.Label("CATSER (digitação)"),
                                        dcc.Input(
                                            id="filtro_catser_texto_itabira",
                                            type="text",
                                            placeholder=(
                                                "Digite parte do CATSER, selecione na lista e, "
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
                                        html.Label("CATSER (lista)"),
                                        dcc.Checklist(
                                            id="filtro_catser_lista_itabira",
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
                                    id="btn_limpar_filtros_limite_itabira_catser",
                                    n_clicks=0,
                                    style=botao_limpar_style,
                                ),
                                html.Button(
                                    "Atualizar dados",
                                    id="btn_reload_catser",
                                    n_clicks=0,
                                    style=botao_atualizar_style,
                                ),
                                html.Button(
                                    "Baixar Relatório PDF",
                                    id="btn_download_relatorio_limite_itabira_catser",
                                    n_clicks=0,
                                    style=botao_pdf_style,
                                ),
                                dcc.Download(id="download_relatorio_limite_itabira_catser"),
                                html.Div(
                                    id="info-atualizacao-catser",
                                    style={"fontSize": "12px", "color": "#333"},
                                ),
                            ],
                        ),
                    ],
                ),
                dash_table.DataTable(
                    id="tabela_limite_itabira_catser",
                    columns=[
                        {"name": "CATSER", "id": COL_CATSER_OUT},
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
                        {"if": {"column_id": COL_CATSER_OUT}, "width": "10%"},
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
                dcc.Store(id="store-reload-catser"),
                dcc.Interval(id="interval-reload-catser", interval=60 * 60 * 1000, n_intervals=0),
            ],
        ),
    ],
)


@dash.callback(
    Output("store-reload-catser", "data"),
    Output("info-atualizacao-catser", "children"),
    Output("filtro_catser_lista_itabira", "options"),
    Output("card_data_consulta_catser", "children"),
    Input("url", "pathname"),
    Input("interval-reload-catser", "n_intervals"),
    Input("btn_reload_catser", "n_clicks"),
    State("filtro_catser_lista_itabira", "value"),
)
def carregar_ao_abrir_interval_ou_recarregar_catser(pathname, _n_intervals, _n_clicks, selecionados):
    if pathname != "/fracionamento_catser":
        raise PreventUpdate

    force = dash.ctx.triggered_id == "btn_reload_catser"
    df, status = get_df_catser(force=force)

    base = catsers_unicos(df)
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
    Output("filtro_catser_lista_itabira", "options", allow_duplicate=True),
    Input("filtro_catser_texto_itabira", "value"),
    Input("store-reload-catser", "data"),
    State("filtro_catser_lista_itabira", "value"),
    prevent_initial_call=True,
)
def atualizar_opcoes_catser(catser_texto, _reload, valores_selecionados):
    df, _ = get_df_catser(force=False)
    base = catsers_unicos(df)

    if not catser_texto or not str(catser_texto).strip():
        filtradas = base
    else:
        termo = str(catser_texto).strip().lower()
        filtradas = [value for value in base if termo in str(value).lower()]

    valores_selecionados = valores_selecionados or []
    for value in valores_selecionados:
        if value in base and value not in filtradas:
            filtradas.append(value)

    return [{"label": value, "value": value} for value in sorted(filtradas)]


@dash.callback(
    Output("tabela_limite_itabira_catser", "data"),
    Output("tabela_limite_itabira_catser", "page_count"),
    Input("store-reload-catser", "data"),
    Input("filtro_catser_lista_itabira", "value"),
    Input("tabela_limite_itabira_catser", "page_current"),
    Input("tabela_limite_itabira_catser", "page_size"),
)
def atualizar_tabela_limite_itabira_catser(_reload, catser_lista, page_current, page_size):
    df_base, _ = get_df_catser(force=False)
    dff = filtrar_dados_catser(df_base, catser_lista)

    if dff.empty:
        return [], 0

    page_current = page_current or 0
    page_size = page_size or PAGE_SIZE_PADRAO
    page_count = max(1, (len(dff) + page_size - 1) // page_size)
    page_current = min(page_current, page_count - 1)
    start = page_current * page_size
    end = start + page_size
    dff_payload = preparar_payload_tabela_catser(dff.iloc[start:end])

    return dff_payload.to_dict("records"), page_count


@dash.callback(
    Output("filtro_catser_texto_itabira", "value"),
    Output("filtro_catser_lista_itabira", "value"),
    Input("btn_limpar_filtros_limite_itabira_catser", "n_clicks"),
    prevent_initial_call=True,
)
def limpar_filtros_limite_itabira_catser(_n):
    return None, []


wrap_style_data_catser = ParagraphStyle(
    name="wrap_limite_itabira_catser_data",
    fontSize=7,
    leading=9,
    alignment=TA_CENTER,
    textColor=colors.black,
)

wrap_style_header_catser = ParagraphStyle(
    name="wrap_limite_itabira_catser_header",
    fontSize=7,
    leading=9,
    alignment=TA_CENTER,
    textColor=colors.white,
)

wrap_style_desc_catser = ParagraphStyle(
    name="wrap_limite_itabira_catser_desc",
    fontSize=7,
    leading=9,
    alignment=TA_LEFT,
    textColor=colors.black,
)


def wrap_data_catser(text):
    return Paragraph(str(text), wrap_style_data_catser)


def wrap_header_catser(text):
    return Paragraph(str(text), wrap_style_header_catser)


def wrap_desc_catser(text):
    return Paragraph(str(text), wrap_style_desc_catser)


@dash.callback(
    Output("download_relatorio_limite_itabira_catser", "data"),
    Input("btn_download_relatorio_limite_itabira_catser", "n_clicks"),
    State("filtro_catser_lista_itabira", "value"),
    prevent_initial_call=True,
)
def gerar_pdf_limite_itabira_catser(n, catser_lista):
    if not n:
        return None

    df_base, _ = get_df_catser(force=False)
    df = filtrar_dados_catser(df_base, catser_lista)
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
                ParagraphStyle("data_topo_catser", fontSize=9, alignment=TA_RIGHT, textColor="#333333"),
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
        ParagraphStyle("instituicao_catser", alignment=TA_CENTER, leading=14),
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
        "Consulta ao Fracionamento de Despesa 2026 - CATSER (Serviço): UASG: 158161 - Campus Itabira",
        ParagraphStyle("titulo_catser", alignment=TA_CENTER, fontSize=10, leading=14, textColor=colors.black),
    )
    story.append(titulo_paragraph)
    story.append(Spacer(1, 0.15 * inch))
    story.append(Paragraph(f"Total de registros: {len(df)}", styles["Normal"]))
    story.append(Spacer(1, 0.1 * inch))

    cols = COLS_TABELA_CATSER
    df_pdf = df.copy()
    for col in cols:
        if col not in df_pdf.columns:
            df_pdf[col] = ""
    for col in cols[2:]:
        df_pdf[col] = df_pdf[col].apply(fmt_moeda)

    header = [wrap_header_catser(col) for col in cols]
    table_data = [header]
    saldo_values = pd.to_numeric(df["Saldo para contratação"], errors="coerce").fillna(0).tolist()

    for _, row in df_pdf[cols].iterrows():
        row_data = []
        for index, col in enumerate(cols):
            row_data.append(wrap_desc_catser(row[col]) if index == 1 else wrap_data_catser(row[col]))
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
        f"limite_gasto_itabira_catser_{datetime.now().strftime('%Y%m%d%H%M%S')}.pdf",
    )
