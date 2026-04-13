import streamlit as st
import pandas as pd
import plotly.express as px
import math
from io import BytesIO
from supabase import create_client, Client

# Configurações iniciais
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
            file_name="modelo_senac.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

if not st.session_state.autenticado:
    st.title("📊 Planejador Inteligente de Turmas")
    st.info("👋 Olá! Faça login na barra lateral para acessar o sistema.")
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

def carregar_do_banco():
    if supabase:
        try:
            res = supabase.table("planejamentos_turmas").select("*").execute()
            return pd.DataFrame(res.data)
        except:
            return pd.DataFrame()
    return pd.DataFrame()

def deletar_banco():
    if supabase:
        try:
            supabase.table("planejamentos_turmas").delete().neq("Curso", "0").execute()
            st.cache_resource.clear()
        except:
            pass

# =========================
# MOTOR DE GERAÇÃO COM MEMÓRIA
# =========================
def gerar_turmas(df, min_a, max_a, plano_antigo=pd.DataFrame()):
    turmas_lista = []
    
    mapa_nomes = {}
    if not plano_antigo.empty:
        for _, row in plano_antigo.iterrows():
            cnpjs_vinc = str(row["CNPJs"]).split(", ")
            for c in cnpjs_vinc:
                mapa_nomes[c] = row["Turma"]

    for curso in df["Curso"].unique():
        dados_curso = df[df["Curso"] == curso]
        elementos = []
        for _, row in dados_curso.iterrows():
            elementos.extend([{
                "UF": str(row["UF"]), 
                "CNPJ": str(row["CNPJ"]), 
                "Status": str(row.get("Status", "N/A"))
            }] * int(row["Qtde"]))
        
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
            status_list = sorted(set([str(g["Status"]) for g in grupo if g["Status"] != "nan"]))
            
            nome_original = mapa_nomes.get(cnpjs[0], f"{curso[:3].upper()}-{i+1:02d}")
            
            turmas_lista.append({
                "Curso": curso,
                "Turma": nome_original,
                "Alunos": len(grupo),
                "UFs": ", ".join(ufs),
                "CNPJs": ", ".join(cnpjs),
                "Status": ", ".join(status_list) if status_list else "Indefinido"
            })
    return pd.DataFrame(turmas_lista)

# =========================
# CONFIGURAÇÕES E FLUXO
# =========================
st.sidebar.header("⚙️ Configurações")
min_alunos = st.sidebar.number_input("Mínimo por turma", min_value=1, value=30)
max_alunos = st.sidebar.number_input("Máximo por turma", min_value=1, value=45)

if st.sidebar.button("🗑️ Resetar Planejamento"):
    deletar_banco()
    st.rerun()

st.title("📊 Planejador Inteligente de Turmas")

plano_nuvem = carregar_do_banco()
arquivo = st.file_uploader("📤 Subir Atualização de Planilha", type=["xlsx"])

plano_final_exibir = pd.DataFrame()
df_base_original = pd.DataFrame()

if arquivo:
    try:
        df_raw = pd.read_excel(arquivo)
        df_base_original = df_raw.copy()
        
        # Normalização inteligente de colunas
        colunas_originais = df_raw.columns.tolist()
        mapa_renomear = {}
        for col in colunas_originais:
            c_upper = str(col).strip().upper()
            if c_upper in ["UF", "ESTADO"]: mapa_renomear[col] = "UF"
            elif c_upper in ["CNPJ", "CLIENTE"]: mapa_renomear[col] = "CNPJ"
            elif c_upper in ["QTDE", "QUANTIDADE", "ALUNOS"]: mapa_renomear[col] = "Qtde"
            elif c_upper in ["STATUS", "SITUAÇÃO", "FASE", "SITUACAO"]: mapa_renomear[col] = "Status"
            elif c_upper in ["CURSO", "NOME DO CURSO"]: mapa_renomear[col] = "Curso"
        
        df_raw = df_raw.rename(columns=mapa_renomear)

        # Limpeza de dados
        df_raw["CNPJ"] = df_raw["CNPJ"].astype(str).str.strip()
        df_raw["Qtde"] = pd.to_numeric(df_raw["Qtde"], errors='coerce').fillna(0).astype(int)
        df_validos = df_raw[df_raw["Qtde"] > 0].copy()

        # =========================
        # NOVO: PAINEL DE INDICADORES (MÉTRICAS)
        # =========================
        st.divider()
        st.subheader("🏁 Painel de Controle - Visão Geral")
        
        total_geral_alunos = df_validos["Qtde"].sum()
        
        # Métrica Principal
        st.metric("Soma Total de Solicitações (Alunos)", f"{total_geral_alunos} alunos")
        
        col_met1, col_met2 = st.columns(2)
        
        with col_met1:
            with st.expander("🎓 Alunos por Tipo de Curso", expanded=True):
                df_curso_met = df_validos.groupby("Curso")["Qtde"].sum().reset_index()
                for _, row in df_curso_met.iterrows():
                    st.write(f"**{row['Curso']}:** {row['Qtde']} alunos")

        with col_met2:
            with st.expander("📋 Vagas por Status", expanded=True):
                if "Status" in df_validos.columns:
                    df_status_met = df_validos.groupby("Status")["Qtde"].sum().reset_index()
                    for _, row in df_status_met.iterrows():
                        st.write(f"**{row['Status']}:** {row['Qtde']} vagas")
                else:
                    st.write("Coluna de Status não encontrada.")

        # Gerar o plano comparando com a nuvem
        df_motor = df_validos.groupby(["Curso", "UF", "CNPJ", "Status"], as_index=False)["Qtde"].sum()
        plano_final_exibir = gerar_turmas(df_motor, min_alunos, max_alunos, plano_nuvem)
        st.success("Planejamento gerado com sucesso!")

    except Exception as e:
        st.error(f"Erro: {e}")

elif not plano_nuvem.empty:
    st.info("📂 Exibindo dados salvos na nuvem.")
    plano_final_exibir = plano_nuvem.copy()

# =========================
# TABELA E AUTO-SAVE
# =========================
if not plano_final_exibir.empty:
    st.divider()
    st.subheader("📚 Ajuste de Planejamento")
    
    if "id" not in plano_final_exibir.columns:
        plano_final_exibir["id"] = None

    # ORDEM SOLICITADA: id, Turma, Curso, Alunos, CNPJ, Status
    ordem_colunas = ["id", "Turma", "Curso", "Alunos", "CNPJs", "Status"]
    ordem_final = [c for c in ordem_colunas if c in plano_final_exibir.columns]
    plano_para_editor = plano_final_exibir[ordem_final]

    plano_editado = st.data_editor(
        plano_para_editor,
        column_config={
            "id": None,      # Invisível
            "Status": None,  # Invisível
            "Turma": st.column_config.TextColumn("Turma"),
            "Curso": st.column_config.TextColumn("Curso"),
            "Alunos": st.column_config.NumberColumn("Alunos"),
            "CNPJs": st.column_config.TextColumn("CNPJ"),
        },
        disabled=["Curso", "Alunos", "CNPJs"],
        use_container_width=True,
        hide_index=True,
        key="editor_principal"
    )

    # Auto-save no Supabase
    if supabase and (arquivo is not None or not plano_nuvem.empty):
        try:
            dados_db = []
            for row in plano_editado.to_dict(orient="records"):
                ufs_orig = str(plano_final_exibir.loc[plano_final_exibir['Turma'] == row['Turma'], 'UFs'].values[0])
                stat_orig = str(plano_final_exibir.loc[plano_final_exibir['Turma'] == row['Turma'], 'Status'].values[0])
                
                dados_db.append({
                    "Curso": str(row["Curso"]),
                    "Turma": str(row["Turma"]),
                    "Alunos": int(row["Alunos"]),
                    "UFs": ufs_orig,
                    "CNPJs": str(row["CNPJs"]),
                    "Status": stat_orig
                })
            supabase.table("planejamentos_turmas").delete().neq("Curso", "0").execute()
            supabase.table("planejamentos_turmas").insert(dados_db).execute()
            st.toast("Sincronizado!", icon="☁️")
        except Exception as e:
            st.error(f"Erro ao salvar: {e}")

    # Ferramentas Adicionais
    col_l, col_r = st.columns(2)
    with col_l:
        st.subheader("🔍 Localizador")
        busca_cnpj = st.text_input("Buscar por CNPJ:")
        if busca_cnpj:
            res = plano_editado[plano_editado["CNPJs"].astype(str).str.contains(busca_cnpj, na=False)]
            if not res.empty:
                st.dataframe(res[["Curso", "Turma"]], hide_index=True)

    with col_r:
        st.subheader("⚠️ Alertas de Ocupação")
        baixas_ocp = plano_editado[plano_editado["Alunos"] < min_alunos]
        if not baixas_ocp.empty:
            st.warning(f"{len(baixas_ocp)} turmas abaixo do quórum.")
            st.dataframe(baixas_ocp[["Curso", "Turma", "Alunos"]], hide_index=True)

    # Gráficos (Mantidos)
    st.divider()
    c1, c2, c3 = st.columns(3)
    resumo_c = plano_editado.groupby("Curso").size().reset_index(name="Turmas")
    with c1: st.plotly_chart(px.bar(resumo_c, x="Curso", y="Turmas", title="Turmas por Curso"), use_container_width=True)
    with c2: st.plotly_chart(px.pie(plano_editado, names="Curso", title="Mix de Cursos"), use_container_width=True)
    with c3: st.plotly_chart(px.histogram(plano_editado, x="Alunos", title="Distribuição de Alunos"), use_container_width=True)

    st.download_button("📥 Baixar Excel Completo", data=gerar_excel_final(plano_editado, df_base_original), file_name="planejamento_senac.xlsx")
