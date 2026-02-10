import dash
from dash import Dash, html, dcc, callback, Input, Output


app = Dash(
    __name__,
    use_pages=True,
    suppress_callback_exceptions=True,
    meta_tags=[{"name": "viewport", "content": "width=device-width, initial-scale=1"}],
)
server = app.server


# LINKS NORMAIS (sem Contratos e Extrato do Contrato, pois ficam dentro da caixinha "Contratos")
menu_links = [

  #  {"label": "tabela", "href": "/consultartabelas"},
]


app.layout = html.Div(
    className="app-root",
    children=[
        dcc.Location(id="url"),

        dcc.Interval(
            id="interval-atualizacao",
            interval=60 * 60 * 1000,
            n_intervals=0,
        ),

        html.Div(
            className="app-container",
            children=[
                # SIDEBAR
                html.Div(
                    className="sidebar",
                    children=[
                        html.Div(
                            className="sidebar-header",
                            children=[
                                html.Img(
                                    src="/assets/logo_unifei.png",
                                    className="sidebar-logo",
                                ),
                                html.H2(
                                    "Painéis",
                                    className="sidebar-title",
                                ),
                            ],
                        ),
                        html.Div(
                            id="sidebar-menu",
                            className="sidebar-menu",
                        ),
                    ],
                ),

                # CONTEÚDO PRINCIPAL
                html.Div(
                    className="main-content",
                    children=html.Div(
                        className="page-wrapper",
                        children=dash.page_container,
                    ),
                ),
            ],
        ),
    ],
)


@callback(
    Output("sidebar-menu", "children"),
    Input("url", "pathname"),
)
def atualizar_menu(pathname):
    itens = []

   

    # =========================
    # 2) Caixa Fracionamento
    # =========================
    fracionamento_ativo = pathname in ["/fracionamento_pdm", "/fracionamento_catser"]

    fr_btn_classes = "fracionamento-toggle"
    fr_content_classes = "fracionamento-content"
    if fracionamento_ativo:
        fr_btn_classes += " active"
        fr_content_classes += " expanded"

    fracionamento_box = html.Div(
        className="fracionamento-container",
        children=[
            html.Div(
                "Fracionamento de Despesas",
                id="btn-fracionamento",
                className=fr_btn_classes,
            ),
            html.Div(
                id="box-fracionamento",
                className=fr_content_classes,
                children=[
                    dcc.Link(
                        "Fracionamento de Despesas PDM (Material)",
                        href="/fracionamento_pdm",
                        className=(
                            "fracionamento-subbutton fracionamento-subbutton-active"
                            if pathname == "/fracionamento_pdm"
                            else "fracionamento-subbutton"
                        ),
                    ),
                    dcc.Link(
                        "Fracionamento de Despesas CATSER (Serviço)",
                        href="/fracionamento_catser",
                        className=(
                            "fracionamento-subbutton fracionamento-subbutton-active"
                            if pathname == "/fracionamento_catser"
                            else "fracionamento-subbutton"
                        ),
                    ),
                ],
            ),
        ],
    )
    itens.append(fracionamento_box)

   

    # =========================
    # 4) Demais itens normais
    # =========================
    for m in menu_links:
        class_name = (
            "sidebar-button sidebar-button-active"
            if pathname == m["href"]
            else "sidebar-button"
        )
        itens.append(
            dcc.Link(
                m["label"],
                href=m["href"],
                className=class_name,
            )
        )

    return itens





# Abre/fecha Fracionamento
@callback(
    Output("btn-fracionamento", "className"),
    Output("box-fracionamento", "className"),
    Input("btn-fracionamento", "n_clicks"),
    prevent_initial_call=True,
)
def toggle_fracionamento(n):
    base_btn = "fracionamento-toggle"
    base_box = "fracionamento-content"
    if n and n % 2 == 1:
        return base_btn + " active", base_box + " expanded"
    return base_btn, base_box





if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8053, debug=False)
