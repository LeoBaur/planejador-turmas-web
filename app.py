import streamlit as st
import pandas as pd
import plotly.express as px
import math
from io import BytesIO
from supabase import create_client, Client

st.set_page_config(page_title="Planejador Inteligente de Turmas", layout="wide")

# =========================
# SISTEMA DE LOGIN (SEGURANÇA)
# =========================
if 'autenticado' not in st.session_state:
    st.session_state.autenticado = False

def tela_login():
    with st.sidebar:
        st.subheader("🔒 Acesso Restrito")
        usuario = st.text_input("Usuário")
        senha = st.text_input("Senha", type="password")
        if st.button("Entrar"):
            # Usuário e senha padrão (você pode alterar aqui)
            if usuario == "admin" and senha == "senac123":
                st.session_state.autenticado = True
                st.rerun()
            else:
                st.error("Credenciais inválidas. Tente novamente.")

if not st.session_state.autenticado:
    tela_login()
    st.title("📊 Planejador Inteligente de Turmas")
    st.info("👋 Olá! Por favor, faça login na barra lateral para acessar o sistema.")
    st.stop() # Bloqueia o restante do código até fazer login

# =========================
# CONEXÃO SUPABASE
# =========================
@st.cache_resource
def init_connection():
    try:
        url = st.secrets["SUPABASE_URL"]
        key = st.secrets["SUPABASE_KEY"]
        return create_client(url, key)
    except:
        return None

supabase = init_connection()

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

if st.sidebar.button("Sair (Logout)"):
    st.session_state.autenticado = False
    st.rerun()

st.title("📊 Planejador Inteligente de Turmas")

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
        if total == 0:
            continue

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
# UPLOAD E PROCESSAMENTO
# =========================
arquivo = st.file_uploader("📤 Envie sua planilha", type=["xlsx"])

if arquivo:
    df_raw = pd.read_excel(arquivo)

    # Limpeza básica
    df_raw.columns = df_raw.columns.str.strip()
    
    colunas_obrigatorias = ["Curso", "UF", "CNPJ", "Qtde"]
    
    if not all(col in df_raw.columns for col in colunas_obrigatorias):
        st.error(f"Erro: A planilha deve conter as colunas: {', '.join(colunas_obrigatorias)}")
    else:
        # Tratamento de dados
        df_raw["Curso"] = df_raw["Curso"].astype(str).str.strip()
        df_raw["UF"] = df_raw["UF"].astype(str).str.strip()
        df_raw["CNPJ"] = df_raw["CNPJ"].astype(str).str.strip()
        df_raw["Qtde"] = pd.to_numeric(df_raw["Qtde"], errors="coerce").fillna(0).astype(int)
        
        # Filtra apenas os que têm quantidade
        df_validos = df_raw[df_raw["Qtde"] > 0]

        # =========================
        # NOVO: DASHBOARD DE STATUS
        # =========================
        if "Status" in df_validos.columns:
            st.subheader("📈 Comparativo de Alunos por Status")
            
            status_contagem = df_validos["Status"].value_counts().reset_index()
            status_contagem.columns = ["Status", "Quantidade de Alunos"]
            
            fig_status = px.bar(
                status_contagem, 
                x="Status", 
                y="Quantidade de Alunos", 
                color="Status",
                title="Distribuição do Atendimento"
            )
            st.plotly_chart(fig_status, use_container_width=True)
            st.divider()

        # =========================
        # GERAR TURMAS E AGRUPAR
        # =========================
        # Agrupa os dados apenas para o motor de gerar turmas (ignora status aqui para não quebrar a lógica)
        df_agrupado = df_validos.groupby(["Curso", "UF", "CNPJ"], as_index=False)["Qtde"].sum()

        st.subheader("📋 Dados Carregados para Planejamento")
        st.dataframe(df_agrupado, use_container_width=True)

        plano = gerar_turmas(df_agrupado, min_alunos, max_alunos)

        if not plano.empty:
            # =========================
            # NOVO: TABELA EDITÁVEL E SUPABASE
            # =========================
            st.subheader("📚 Ajuste Final das Turmas")
            st.info("Dê um duplo clique nos nomes das turmas abaixo (coluna 'Turma') para renomear e organizar do seu jeito.")

            plano_editado = st.data_editor(
                plano,
                column_config={
                    "Turma": st.column_config.TextColumn("Nome da Turma (Editável)"),
                    "Curso": st.column_config.Column(disabled=True),
                    "Alunos": st.column_config.Column(disabled=True),
                    "UFs": st.column_config.Column(disabled=True),
                    "CNPJs": st.column_config.Column(disabled=True),
                },
                use_container_width=True,
                hide_index=True
            )

            if st.button("☁️ Salvar Planejamento na Nuvem (Supabase)"):
                if supabase:
                    try:
                        # Converte a tabela editada para formato JSON (dicionário)
                        dados_para_salvar = plano_editado.to_dict(orient="records")
                        
                        # Insere no Supabase na tabela "planejamentos_turmas"
                        resposta = supabase.table("planejamentos_turmas").insert(dados_para_salvar).execute()
                        st.success("Tudo certo! Planejamento salvo no banco de dados com sucesso.")
                    except Exception as e:
                        st.error(f"Erro ao salvar no banco. Verifique se a tabela 'planejamentos_turmas' existe no Supabase. Detalhes: {e}")
                else:
                    st.warning("⚠️ Conexão com Supabase não encontrada. Configure as chaves nos Secrets do Streamlit.")

            st.divider()

            # =========================
            # DASHBOARD ORIGINAL
            # =========================
            col1, col2, col3 = st.columns(3)
            resumo = plano_editado.groupby("Curso").size().reset_index(name="Turmas")

            with col1:
                fig = px.bar(resumo, x="Curso", y="Turmas", title="Turmas por curso")
                st.plotly_chart(fig, use_container_width=True)

            with col2:
                fig2 = px.pie(plano_editado, names="Curso", title="Distribuição das turmas")
                st.plotly_chart(fig2, use_container_width=True)

            with col3:
                fig3 = px.histogram(plano_editado, x="Alunos", nbins=10, title="Alunos por turma")
                st.plotly_chart(fig3, use_container_width=True)

            # =========================
            # EXPORTAÇÃO
            # =========================
            def gerar_excel():
                output = BytesIO()
                with pd.ExcelWriter(output, engine="openpyxl") as writer:
                    plano_editado.to_excel(writer, sheet_name="Planejamento", index=False)
                    resumo.to_excel(writer, sheet_name="Resumo", index=False)
                    df_raw.to_excel(writer, sheet_name="Base_Original", index=False)
                return output.getvalue()

            st.download_button(
                label="📥 Baixar planejamento final em Excel",
                data=gerar_excel(),
                file_name="planejamento_turmas_final.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
        else:
            st.warning("Não foi possível gerar turmas com os dados fornecidos.")
