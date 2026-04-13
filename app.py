import streamlit as st
import pandas as pd
import plotly.express as px
import math
import json
from io import BytesIO
from supabase import create_client, Client

# Configuração da Página
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

def gerar_excel_final(plano_df, resumo_df, original_df):
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        plano_df.to_excel(writer, sheet_name="Planejamento", index=False)
        resumo_df.to_excel(writer, sheet_name="Resumo", index=False)
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
        st.success(f"Logado como: Admin")
        if st.button("Sair (Logout)"):
            st.session_state.autenticado = False
            st.rerun()
        
        st.divider()
        st.write("📂 **Modelos**")
        st.download_button(
            label="📥 Baixar Modelo de Planilha",
            data=gerar_modelo_excel(),
            file_name="modelo_planejamento.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

if not st.session_state.autenticado:
    st.title("📊 Planejador Inteligente de Turmas")
    st.warning("Por favor, realize o login na barra lateral para continuar.")
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
    except Exception as e:
        return None

supabase = init_connection()

# =========================
# PARÂMETROS
# =========================
st.sidebar.header("⚙️ Parâmetros")
min_alunos = st.sidebar.number_input("Mínimo por turma", min_value=1, value=30)
max_alunos = st.sidebar.number_input("Máximo por turma", min_value=1, value=45)

st.title("📊 Planejador Inteligente de Turmas")

# =========================
# MOTOR DE GERAÇÃO
# =========================
def gerar_turmas(df, min_a, max_a):
    turmas_lista = []
    for curso in df["Curso"].unique():
        dados_curso = df[df["Curso"] == curso]
        elementos = []
        for _, row in dados_curso.iterrows():
            # Garantimos que CNPJ e UF são strings para não dar erro no .join posterior
            elementos.extend([{"UF": str(row["UF"]), "CNPJ": str(row["CNPJ"])}] * int(row["Qtde"]))
        
        total = len(elementos)
        if total == 0: continue
        
        n_turmas = math.ceil(total / max_a)
        while total / n_turmas < min_a and n_turmas > 1:
            n_turmas -= 1
            
        tam_base = total // n_turmas
        sobra = total % n_turmas
        ponteiro = 0
        
        for i in range(n_turmas):
            tam = tam_base + (1 if i < sobra else 0)
            grupo = elementos[ponteiro:ponteiro+tam]
            ponteiro += tam
            
            ufs = sorted(set([str(g["UF"]) for g in grupo]))
            cnpjs = sorted(set([str(g["CNPJ"]) for g in grupo]))
            
            turmas_lista.append({
                "Curso": curso,
                "Turma": f"{curso[:3].upper()}-{i+1:02d}",
                "Alunos": len(grupo),
                "UFs": ", ".join(ufs),
                "CNPJs": ", ".join(cnpjs)
            })
    return pd.DataFrame(turmas_lista)

# =========================
# CARREGAMENTO DE ARQUIVO
# =========================
arquivo = st.file_uploader("📤 Envie sua planilha", type=["xlsx"])

if arquivo:
    try:
        df_raw = pd.read_excel(arquivo)
        
        # Normalização de Colunas
        df_raw.columns = [str(c).strip().title() for c in df_raw.columns]
        df_raw = df_raw.rename(columns={"Uf": "UF", "Cnpj": "CNPJ", "Qtde": "Qtde", "Status": "Status"})
        
        cols_obrigatorias = ["Curso", "UF", "CNPJ", "Qtde"]
        if not all(c in df_raw.columns for c in cols_obrigatorias):
            st.error(f"A planilha precisa conter: {cols_obrigatorias}")
            st.stop()

        # LIMPEZA CRÍTICA: Forçar Texto e Números corretos
        df_raw["Curso"] = df_raw["Curso"].astype(str).str.strip()
        df_raw["UF"] = df_raw["UF"].astype(str).str.strip()
        df_raw["CNPJ"] = df_raw["CNPJ"].astype(str).str.strip()
        df_raw["Qtde"] = pd.to_numeric(df_raw["Qtde"], errors='coerce').fillna(0).astype(int)
        
        df_validos = df_raw[df_raw["Qtde"] > 0].copy()

        # --- DASHBOARD DE STATUS ---
        if "Status" in df_validos.columns:
            st.subheader("📈 Comparativo por Status (Total de Alunos)")
            df_status = df_validos.groupby("Status")["Qtde"].sum().reset_index()
            fig_st = px.bar(df_status, x="Status", y="Qtde", color="Status", text_auto=True, title="Alunos por Fase")
            st.plotly_chart(fig_st, use_container_width=True)
            st.divider()

        # --- GERAÇÃO DO PLANEJAMENTO ---
        df_motor = df_validos.groupby(["Curso", "UF", "CNPJ"], as_index=False)["Qtde"].sum()
        plano = gerar_turmas(df_motor, min_alunos, max_alunos)

        if not plano.empty:
            st.subheader("📚 Planejamento Gerado")
            st.caption("Dê um duplo clique na coluna 'Turma' para renomear. O salvamento na nuvem é automático.")
            
            # Editor Interativo
            plano_final = st.data_editor(
                plano,
                column_config={"Turma": st.column_config.TextColumn("Nome da Turma (Editável)")},
                disabled=["Curso", "Alunos", "UFs", "CNPJs"],
                use_container_width=True,
                hide_index=True,
                key="editor_principal"
            )

            # --- AUTO-SAVE SUPABASE ---
            if supabase:
                try:
                    dados_db = plano_final.to_dict(orient="records")
                    # Limpa e reinsere para garantir sincronia
                    supabase.table("planejamentos_turmas").delete().neq("Curso", "0").execute()
                    supabase.table("planejamentos_turmas").insert(dados_db).execute()
                    st.toast("Dados salvos na nuvem!", icon="☁️")
                except:
                    pass

            # --- BUSCADOR DE CNPJ ---
            st.divider()
            st.subheader("🔍 Localizador de CNPJ")
            busca = st.text_input("Pesquisar CNPJ na lista gerada:")
            if busca:
                # Busca na coluna de CNPJs (que é uma string separada por vírgulas)
                resultado = plano_final[plano_final["CNPJs"].str.contains(busca, na=False)]
                if not resultado.empty:
                    st.success(f"Encontrado!")
                    # Aqui usamos 'UFs' no plural para evitar o KeyError
                    st.dataframe(resultado[["Curso", "Turma", "Alunos", "UFs"]], hide_index=True, use_container_width=True)
                else:
                    st.warning("CNPJ não localizado neste planejamento.")

            # --- DASHBOARDS DE DISTRIBUIÇÃO ---
            st.divider()
            col1, col2 = st.columns(2)
            resumo_curso = plano_final.groupby("Curso").size().reset_index(name="Total Turmas")
            
            with col1:
                fig1 = px.bar(resumo_curso, x="Curso", y="Total Turmas", title="Turmas por Curso")
                st.plotly_chart(fig1, use_container_width=True)
            
            with col2:
                fig2 = px.histogram(plano_final, x="Alunos", title="Distribuição de Alunos por Turma")
                st.plotly_chart(fig2, use_container_width=True)

            # --- BOTÃO DE DOWNLOAD FINAL ---
            st.download_button(
                label="📥 Baixar Planejamento Final (Excel)",
                data=gerar_excel_final(plano_final, resumo_curso, df_raw),
                file_name="planejamento_senac_final.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

    except Exception as e:
        st.error(f"Erro ao processar a planilha: {e}")
