import streamlit as st
import pandas as pd
import plotly.express as px
import math
from io import BytesIO

st.set_page_config(page_title="Planejador Inteligente de Turmas", layout="wide")

st.title("📊 Planejador Inteligente de Turmas")

# =========================
# PARÂMETROS
# =========================

st.sidebar.header("⚙️ Parâmetros")

min_alunos = st.sidebar.number_input(
    "Mínimo de alunos por turma",
    min_value=1,
    max_value=100,
    value=30
)

max_alunos = st.sidebar.number_input(
    "Máximo de alunos por turma",
    min_value=1,
    max_value=100,
    value=45
)

# =========================
# MODELO DE PLANILHA
# =========================

modelo = pd.DataFrame({
    "Curso": ["Administração", "Administração", "Logística"],
    "UF": ["PR", "PR", "SP"],
    "CNPJ": ["11111111000100", "22222222000100", "33333333000100"],
    "Qtde": [30, 18, 25]
})


def gerar_modelo_excel():

    output = BytesIO()

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        modelo.to_excel(writer, index=False, sheet_name="Dados")

    return output.getvalue()


st.download_button(
    label="📥 Baixar modelo de planilha",
    data=gerar_modelo_excel(),
    file_name="modelo_planejamento_turmas.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)

# =========================
# FUNÇÃO GERAR TURMAS
# =========================

def gerar_turmas(df, min_alunos, max_alunos):

    turmas = []

    for curso in df["Curso"].unique():

        dados_curso = df[df["Curso"] == curso]

        lista = []

        for _, row in dados_curso.iterrows():

            lista.extend([{
                "UF": row["UF"],
                "CNPJ": row["CNPJ"]
            }] * int(row["Qtde"]))

        total = len(lista)

        turmas_necessarias = math.ceil(total / max_alunos)

        while total / turmas_necessarias < min_alunos and turmas_necessarias > 1:
            turmas_necessarias -= 1

        tamanho_base = total // turmas_necessarias
        sobra = total % turmas_necessarias

        inicio = 0

        for i in range(turmas_necessarias):

            tamanho = tamanho_base + (1 if i < sobra else 0)

            grupo = lista[inicio:inicio+tamanho]

            inicio += tamanho

            ufs = sorted(set([a["UF"] for a in grupo]))
            cnpjs = sorted(set([a["CNPJ"] for a in grupo]))

            turmas.append({
                "Curso": curso,
                "Turma": f"{curso[:3].upper()}-{i+1:02d}",
                "Alunos": len(grupo),
                "UFs": ", ".join(ufs),
                "CNPJs": ", ".join(cnpjs)
            })

    return pd.DataFrame(turmas)

# =========================
# UPLOAD
# =========================

arquivo = st.file_uploader("📤 Envie sua planilha", type=["xlsx"])

if arquivo:

    df = pd.read_excel(arquivo)

    # limpeza

    df.columns = df.columns.str.strip()

    df["Curso"] = df["Curso"].astype(str).str.strip()
    df["UF"] = df["UF"].astype(str).str.strip()
    df["CNPJ"] = df["CNPJ"].astype(str).str.strip()

    df["Qtde"] = pd.to_numeric(df["Qtde"], errors="coerce").fillna(0).astype(int)

    df = df[df["Qtde"] > 0]

    df = df.groupby(["Curso", "UF", "CNPJ"], as_index=False).sum()

    st.subheader("📋 Dados carregados")

    st.dataframe(df)

    # =========================
    # GERAR TURMAS
    # =========================

    plano = gerar_turmas(df, min_alunos, max_alunos)

    st.subheader("📚 Planejamento de Turmas")

    st.dataframe(plano)

    # =========================
    # DASHBOARD
    # =========================

    col1, col2, col3 = st.columns(3)

    resumo = plano.groupby("Curso").size().reset_index(name="Turmas")

    with col1:

        fig = px.bar(
            resumo,
            x="Curso",
            y="Turmas",
            title="Turmas por curso"
        )

        st.plotly_chart(fig, width="stretch")

    with col2:

        fig2 = px.pie(
            plano,
            names="Curso",
            title="Distribuição das turmas"
        )

        st.plotly_chart(fig2, width="stretch")

    with col3:

        fig3 = px.histogram(
            plano,
            x="Alunos",
            nbins=10,
            title="Distribuição de alunos por turma"
        )

        st.plotly_chart(fig3, width="stretch")

    # =========================
    # EXPORTAÇÃO
    # =========================

    def gerar_excel():

        output = BytesIO()

        with pd.ExcelWriter(output, engine="openpyxl") as writer:

            plano.to_excel(writer, sheet_name="Planejamento", index=False)

            resumo.to_excel(writer, sheet_name="Resumo", index=False)

            df.to_excel(writer, sheet_name="Base_Original", index=False)

        return output.getvalue()

    st.download_button(
        label="📥 Baixar planejamento em Excel",
        data=gerar_excel(),
        file_name="planejamento_turmas.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )