import streamlit as st
import pandas as pd
import plotly.express as px
import math
import json
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
            if usuario == "admin" and senha == "senac123":
                st.session_state.autenticado = True
                st.rerun()
            else:
                st.error("Credenciais inválidas. Tente novamente.")

if not st.session_state.autenticado:
    tela_login()
    st.title("📊 Planejador Inteligente de Turmas")
    st.info("👋 Olá! Por favor, faça login na barra lateral para acessar o sistema.")
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
# PARÂMETROS
# =========================
st.sidebar.header("⚙️ Parâmetros")

min_alunos = st.sidebar.number_input(
    "Mínimo de alunos por turma", min_value=1, max_value=100, value=30
)
max_alunos = st.sidebar.number_input(
    "Máximo de alunos por turma", min_value=1, max_value=100, value=45
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
        if total == 0: continue

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

    # 1. NORMALIZAÇÃO A FORÇA DAS COLUNAS (Ignora espaços e caixa alta/baixa)
    df_raw.columns = [str(c).strip().title() for c in df_raw.columns]
    df_raw = df_raw.rename(columns={"Uf": "UF", "Cnpj": "CNPJ"}) # Ajusta as siglas
    
    colunas_obrigatorias = ["Curso", "UF", "CNPJ", "Qtde"]
    
    if not all(col in df_raw.columns for col in colunas_obrigatorias):
        st.error(f"Erro: A planilha deve conter as colunas: {', '.join(colunas_obrigatorias)}")
        st.write("Colunas encontradas no seu arquivo:", list(df_raw.columns))
    else:
        # Tratamento de dados garantido
        df_raw["Curso"] = df_raw["Curso"].astype(str).str.strip()
        df_raw["UF"] = df_raw["UF"].astype(str).str.strip()
        df_raw["CNPJ"] = df_raw["CNPJ"].astype(str).str.strip()
        df_raw["Qtde"] = pd.to_numeric(df_raw["Qtde"], errors="coerce").fillna(0).astype(int)
        
        df_validos = df_raw[df_raw["Qtde"] > 0]

        # =========================
        # DASHBOARD DE STATUS CORRIGIDO
        # =========================
        if "Status" in df_validos.columns:
            st.subheader("📈 Comparativo de Alunos por Status")
            
            # Agora ele SOMA a quantidade de alunos, em vez de contar as linhas!
            status_contagem = df_validos.groupby("Status")["Qtde"].sum().reset_index()
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
        # GERAR TURMAS
        # =========================
        df_agrupado = df_validos.groupby(["Curso", "UF", "CNPJ"], as_index=False)["Qtde"].sum()
        plano = gerar_turmas(df_agrupado, min_alunos, max_alunos)

        if not plano.empty:
            # =========================
            # TABELA EDITÁVEL E AUTO-SAVE
            # =========================
            st.subheader("📚 Ajuste de Turmas (Salva Automaticamente)")
            st.info("Dê um duplo clique nos nomes das turmas (coluna 'Turma') para alterar. O sistema salvará na nuvem sozinho a cada mudança.")

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

            # Lógica de Salvamento Automático (Verifica se houve alteração desde a última leitura)
            dados_atuais = plano_editado.to_dict(orient="records")
            hash_atual = hash(json.dumps(dados_atuais, sort_keys=True))

            if st.session_state.get("ultimo_hash_salvo") != hash_atual:
                if supabase:
                    try:
                        # Substitui todos os dados no banco pelos dados novos da tela
                        supabase.table("planejamentos_turmas").delete().neq("Turma", "limpeza_total").execute()
                        supabase.table("planejamentos_turmas").insert(dados_atuais).execute()
                        
                        st.session_state.ultimo_hash_salvo = hash_atual
                        st.toast("☁️ Alteração salva na nuvem com sucesso!", icon="✅")
                    except Exception as e:
                        st.error(f"Erro no auto-save. Verifique se a tabela foi criada no Supabase. Detalhes: {e}")

            # =========================
            # NOVO: BUSCADOR DE CNPJ
            # =========================
            st.divider()
            st.subheader("🔍 Localizador de Empresa")
            busca_cnpj = st.text_input("Digite um CNPJ para descobrir em qual turma ele foi alocado:")
            
            if busca_cnpj:
                # Filtra a tabela onde a coluna CNPJs contém o texto digitado
                encontrados = plano_editado[plano_editado["CNPJs"].astype(str).str.contains(busca_cnpj, case=False, na=False)]
                
                if not encontrados.empty:
                    st.success(f"CNPJ localizado em {len(encontrados)} turma(s):")
                    st.dataframe(encontrados[["Curso", "Turma", "Alunos", "UF"]], hide_index=True, use_container_width=True)
                else:
                    st.warning("CNPJ não localizado no planejamento atual.")
            
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
