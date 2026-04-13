import streamlit as st
import pandas as pd
import plotly.express as px
import math
import json
from io import BytesIO
from supabase import create_client, Client

st.set_page_config(page_title="Planejador Inteligente de Turmas", layout="wide")

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
        st.success(f"Logado como: Admin")
        if st.button("Sair (Logout)"):
            st.session_state.autenticado = False
            st.rerun()

if not st.session_state.autenticado:
    st.title("📊 Planejador Inteligente de Turmas")
    st.warning("Acesse a barra lateral para realizar o login.")
    st.stop()

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
# FUNÇÕES DE PERSISTÊNCIA
# =========================
def carregar_do_banco():
    if supabase:
        res = supabase.table("planejamentos_turmas").select("*").execute()
        return pd.DataFrame(res.data)
    return pd.DataFrame()

def deletar_banco():
    if supabase:
        supabase.table("planejamentos_turmas").delete().neq("Curso", "0").execute()
        st.cache_resource.clear()

# =========================
# PARÂMETROS E MODELOS
# =========================
st.sidebar.header("⚙️ Configurações")
min_alunos = st.sidebar.number_input("Mínimo por turma", min_value=1, value=30)
max_alunos = st.sidebar.number_input("Máximo por turma", min_value=1, value=45)

if st.sidebar.button("🗑️ Deletar Planilha Atual"):
    deletar_banco()
    st.success("Banco de dados limpo!")
    st.rerun()

st.title("📊 Planejador Inteligente de Turmas")

# =========================
# CARREGAMENTO E LÓGICA
# =========================
plano_existente = carregar_do_banco()

arquivo = st.file_uploader("📤 Subir Atualização de Banco", type=["xlsx"])

if arquivo:
    try:
        df_raw = pd.read_excel(arquivo)
        df_raw.columns = [str(c).strip().title() for c in df_raw.columns]
        df_raw = df_raw.rename(columns={"Uf": "UF", "Cnpj": "CNPJ", "Qtde": "Qtde"})

        # Limpeza e Tipagem
        df_raw["CNPJ"] = df_raw["CNPJ"].astype(str).str.strip()
        df_raw["Qtde"] = pd.to_numeric(df_raw["Qtde"], errors='coerce').fillna(0).astype(int)
        df_validos = df_raw[df_raw["Qtde"] > 0].copy()

        # 1. Dashboard de Status (Soma de Alunos)
        if "Status" in df_validos.columns:
            st.subheader("📈 Alunos por Status (Quantitativo Real)")
            df_status = df_validos.groupby("Status")["Qtde"].sum().reset_index()
            fig = px.bar(df_status, x="Status", y="Qtde", color="Status", text_auto=True)
            st.plotly_chart(fig, use_container_width=True)

        # 2. Inteligência de Atualização
        # (Aqui o sistema compararia o CNPJ do arquivo com o Turma do Banco)
        # Para fins de simplificação, geramos o plano e permitimos o auto-save
        
        # [Lógica de geração de turmas simplificada para o exemplo]
        # ... (Manter a função gerar_turmas que já usamos anteriormente)

        # 3. Alertas de Cancelamento
        st.subheader("📚 Planejamento e Alertas")
        # Se Alunos < min_alunos, avisar o usuário
        # st.error("⚠️ Atenção: A Turma X baixou o quantitativo. Sugerido Cancelamento.")

    except Exception as e:
        st.error(f"Erro no processamento: {e}")

# Se houver dados no banco, eles aparecem aqui mesmo sem upload
if not plano_existente.empty:
    st.write("📂 Exibindo dados salvos na nuvem:")
    st.data_editor(plano_existente, use_container_width=True)
