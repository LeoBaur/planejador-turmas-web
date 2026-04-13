import streamlit as st
import pandas as pd
import plotly.express as px
import math
import json
from io import BytesIO
from supabase import create_client, Client

st.set_page_config(page_title="Planejador Inteligente de Turmas", layout="wide")

# =========================
# FUNÇÕES DE APOIO (DOWNLOADS)
# =========================
def gerar_modelo_excel():
    modelo_df = pd.DataFrame({
        "Curso": ["Administração", "Logística"],
        "UF": ["PR", "SP"],
        "CNPJ": ["11111111000100", "22222222000100"],
        "Qtde": [30, 25],
        "Status": ["Em Atendimento", "Pré-Matrícula"]
    })
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        modelo_df.to_excel(writer, index=False, sheet_name="Modelo")
    return output.getvalue()

def gerar_excel_final(plano_df, original_df):
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        plano_df.to_excel(writer, sheet_name="Planejamento", index=False)
        if not original_df.empty:
            original_df.to_excel(writer, sheet_name="Base_Original", index=False)
    return output.getvalue()

# =========================
# SISTEMA DE LOGIN
# =========================
if 'autenticado' not in st.session_state:
    st.session_state.autenticado = False

with st.sidebar:
    st.subheader("🔒 Acesso e Ferramentas")
    if not st.session_state.autenticado:
        usuario = st.text_input("Usuário")
        senha = st.text_input("Senha", type="password")
        if st.button("Entrar"):
            if usuario == "admin" and senha == "senac123":
                st.session_state.autenticado = True
                st.rerun()
            else:
                st.error("Credenciais inválidas")
    else:
        st.success("Logado: Admin")
        if st.button("Sair (Logout)"):
            st.session_state.autenticado = False
            st.rerun()
        
        st.divider()
        st.write("📂 **Modelos**")
        st.download_button(
            label="📥 Baixar Modelo de Planilha",
            data=gerar_modelo_excel(),
            file_name="modelo_senac_v2.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

if not st.session_state.autenticado:
    st.title("📊 Planejador Inteligente de Turmas")
    st.info("👋 Olá! Faça login na barra lateral para acessar o sistema do Senac.")
    st.stop()

# =========================
# CONEXÃO SUPABASE (BLINDADA)
# =========================
@st.cache_resource
def init_connection():
    try:
        url = st.secrets["SUPABASE_URL"]
        key = st.secrets["SUPABASE_KEY"]
        return create_client(url, key)
    except: return None

supabase = init_connection()

def carregar_do_banco():
    if supabase:
        try:
            res = supabase.table("planejamentos_turmas").select("*").execute()
            return pd.DataFrame(res.data)
        except: return pd.DataFrame()
    return pd.DataFrame()

# =========================
# CONFIGURAÇÕES E MOTOR
# =========================
st.sidebar.header("⚙️ Configurações")
min_alunos = st.sidebar.number_input("Mínimo por turma", min_value=1, value=30)
max_alunos = st.sidebar.number_input("Máximo por turma", min_value=1, value=45)

st.title("📊 Planejador Inteligente de Turmas")

plano_nuvem = carregar_do_banco()
arquivo = st.file_uploader("📤 Subir Atualização de Planilha", type=["xlsx"])

# Lógica principal de processamento
if arquivo:
    try:
        df_raw = pd.read_excel(arquivo)
        df_raw.columns = [str(c).strip().title() for c in df_raw.columns]
        df_raw = df_raw.rename(columns={"Uf": "UF", "Cnpj": "CNPJ", "Qtde": "Qtde"})

        df_raw["CNPJ"] = df_raw["CNPJ"].astype(str).str.strip()
        df_raw["Qtde"] = pd.to_numeric(df_raw["Qtde"], errors='coerce').fillna(0).astype(int)
        df_validos = df_raw[df_raw["Qtde"] > 0].copy()

        # Dashboard de Status Real (Soma de Alunos)
        if "Status" in df_validos.columns:
            st.subheader("📈 Comparativo por Status (Total de Alunos)")
            df_status = df_validos.groupby("Status")["Qtde"].sum().reset_index()
            fig = px.bar(df_status, x="Status", y="Qtde", color="Status", text_auto=True)
            st.plotly_chart(fig, use_container_width=True)

        # Lógica de Planejamento (Aqui integraria o motor de gerar_turmas)
        # [Simulado para brevidade - use sua função gerar_turmas aqui]
        
        st.success("Planilha processada com sucesso!")
        
    except Exception as e:
        st.error(f"Erro no processamento: {e}")

# Exibição de Dados (Seja do arquivo ou da nuvem)
if not plano_nuvem.empty:
    st.subheader("📂 Planejamento Salvo na Nuvem")
    
    # 1. Tabela Editável
    plano_editado = st.data_editor(
        plano_nuvem,
        column_config={"Turma": st.column_config.TextColumn("Nome da Turma (Editável)")},
        use_container_width=True,
        hide_index=True,
        key="editor_nuvem"
    )

    # 2. Localizador de CNPJ
    st.subheader("🔍 Localizador de CNPJ")
    busca = st.text_input("Pesquisar CNPJ para saber a qual turma ele pertence:")
    if busca:
        res_busca = plano_editado[plano_editado["CNPJs"].astype(str).str.contains(busca, na=False)]
        if not res_busca.empty:
            st.success("Localizado!")
            st.dataframe(res_busca[["Curso", "Turma", "Alunos", "UFs"]], hide_index=True)

    # 3. Alertas de Cancelamento
    st.subheader("⚠️ Alertas de Ocupação")
    baixas = plano_editado[plano_editado["Alunos"] < min_alunos]
    if not baixas.empty:
        st.error(f"Atenção: Existem {len(baixas)} turmas abaixo do quórum mínimo. Sugerido cancelamento.")
        st.dataframe(baixas[["Curso", "Turma", "Alunos"]], hide_index=True)

    # 4. Gráficos de Apoio
    col1, col2 = st.columns(2)
    with col1:
        st.plotly_chart(px.pie(plano_editado, names="Curso", title="Distribuição de Turmas"), use_container_width=True)
    with col2:
        st.plotly_chart(px.histogram(plano_editado, x="Alunos", title="Ocupação das Turmas"), use_container_width=True)
