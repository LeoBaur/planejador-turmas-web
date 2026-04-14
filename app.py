import streamlit as st
import pandas as pd
import plotly.express as px
import math
from io import BytesIO
from supabase import create_client, Client

# Configurações iniciais
st.set_page_config(page_title="Planejador Inteligente de Turmas", layout="wide")

# =========================
# FUNÇÕES DE APOIO
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
# SISTEMA DE LOGIN (Suporte a Enter)
# =========================
if 'autenticado' not in st.session_state:
    st.session_state.autenticado = False

with st.sidebar:
    st.subheader("🔒 Acesso e Ferramentas")
    if not st.session_state.autenticado:
        with st.form("login_form"):
            usuario = st.text_input("Usuário")
            senha = st.text_input("Senha", type="password")
            botao_entrar = st.form_submit_button("Entrar")
            if botao_entrar:
                if usuario == "admin" and senha == "senac123":
                    st.session_state.autenticado = True
                    st.rerun()
                else:
                    st.error("Credenciais inválidas")
    else:
        st.success("Logado: Admin")
        
        # --- GESTÃO DE ARQUIVOS NO SIDEBAR ---
        st.divider()
        st.subheader("📂 Arquivos na Nuvem")
        
        # Conexão direta para funções de limpeza
        url = st.secrets["SUPABASE_URL"]
        key = st.secrets["SUPABASE_KEY"]
        sb_client = create_client(url, key)

        # Busca arquivos únicos salvos (precisamos da coluna 'Arquivo' na tabela)
        try:
            res_files = sb_client.table("planejamentos_turmas").select("Arquivo").execute()
            df_files = pd.DataFrame(res_files.data)
            if not df_files.empty:
                arquivos_unicos = df_files["Arquivo"].unique()
                for arq in arquivos_unicos:
                    col_arq, col_btn = st.columns([3, 1])
                    col_arq.write(f"📄 {arq}")
                    if col_btn.button("🗑️", key=f"del_{arq}"):
                        sb_client.table("planejamentos_turmas").delete().eq("Arquivo", arq).execute()
                        st.cache_resource.clear()
                        st.rerun()
            else:
                st.info("Nenhum arquivo individual salvo.")
        except:
            st.write("Crie a coluna 'Arquivo' no Supabase para gerenciar individualmente.")

        if st.button("Sair (Logout)"):
            st.session_state.autenticado = False
            st.rerun()
        
        st.divider()
        st.download_button(
            label="📥 Baixar Modelo de Planilha",
            data=gerar_modelo_excel(),
            file_name="modelo_senac.xlsx"
        )

if not st.session_state.autenticado:
    st.title("📊 Planejador Inteligente de Turmas")
    st.info("👋 Olá! Faça login ou pressione Enter para acessar o sistema.")
    st.stop()

# =========================
# CONEXÃO E CARREGAMENTO
# =========================
@st.cache_resource
def init_connection():
    try:
        return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])
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
# MOTOR DE GERAÇÃO
# =========================
def gerar_turmas(df, min_a, max_a, plano_antigo=pd.DataFrame(), nome_arquivo="Upload"):
    turmas_lista = []
    mapa_nomes = {}
    if not plano_antigo.empty:
        for _, row in plano_antigo.iterrows():
            cnpjs_vinc = str(row["CNPJs"]).split(", ")
            for c in cnpjs_vinc:
                chave = f"{c}_{row['Curso']}"
                mapa_nomes[chave] = row["Turma"]

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
        while total / n_turmas < min_a and n_turmas > 1: n_turmas -= 1
            
        tam_base = total // n_turmas
        sobra = total % n_turmas
        ponteiro = 0
        
        for i in range(n_turmas):
            tam = tam_base + (1 if i < sobra else 0)
            grupo = elementos[ponteiro:ponteiro+tam]
            ponteiro += tam
            
            cnpjs = sorted(set([str(g["CNPJ"]) for g in grupo]))
            ufs = sorted(set([str(g["UF"]) for g in grupo]))
            stats = sorted(set([str(g["Status"]) for g in grupo if str(g["Status"]) != "nan"]))
            
            chave_busca = f"{cnpjs[0]}_{curso}"
            nome_original = mapa_nomes.get(chave_busca, f"{curso[:3].upper()}-{i+1:02d}")
            
            turmas_lista.append({
                "Curso": curso,
                "Turma": nome_original,
                "Alunos": len(grupo),
                "UFs": ", ".join(ufs),
                "CNPJs": ", ".join(cnpjs),
                "Status": ", ".join(stats) if stats else "N/A",
                "Arquivo": nome_arquivo
            })
    return pd.DataFrame(turmas_lista)

# =========================
# INTERFACE PRINCIPAL
# =========================
st.sidebar.header("⚙️ Configurações")
min_alunos = st.sidebar.number_input("Mínimo por turma", min_value=1, value=30)
max_alunos = st.sidebar.number_input("Máximo por turma", min_value=1, value=45)

plano_nuvem = carregar_do_banco()
arquivo = st.file_uploader("📤 Subir Atualização de Planilha", type=["xlsx"])

df_final_trabalho = pd.DataFrame()
df_base_original = pd.DataFrame()

if arquivo:
    try:
        df_raw = pd.read_excel(arquivo)
        df_base_original = df_raw.copy()
        df_raw.columns = [str(c).strip().title() for c in df_raw.columns]
        df_raw = df_raw.rename(columns={"Uf": "UF", "Cnpj": "CNPJ", "Qtde": "Qtde"})
        df_raw["CNPJ"] = df_raw["CNPJ"].astype(str).str.strip()
        df_raw["Qtde"] = pd.to_numeric(df_raw["Qtde"], errors='coerce').fillna(0).astype(int)
        df_validos = df_raw[df_raw["Qtde"] > 0].copy()

        df_motor = df_validos.groupby(["Curso", "UF", "CNPJ", "Status"], as_index=False)["Qtde"].sum()
        # Aqui passamos o nome do arquivo para o motor
        df_final_trabalho = gerar_turmas(df_motor, min_alunos, max_alunos, plano_nuvem, arquivo.name)
        st.success(f"Arquivo '{arquivo.name}' processado com sucesso!")
    except Exception as e:
        st.error(f"Erro: {e}")
elif not plano_nuvem.empty:
    df_final_trabalho = plano_nuvem.copy()

# --- PAINEL DE INDICADORES (KPIs) ---
if not df_final_trabalho.empty:
    st.divider()
    st.subheader("🏁 Painel de Controle")
    
    total_alunos = df_final_trabalho["Alunos"].sum()
    st.metric("Total Geral de Alunos", f"{total_alunos} solicitações")
    
    c1, c2 = st.columns(2)
    with c1:
        with st.expander("🎓 Alunos por Curso", expanded=True):
            res_c = df_final_trabalho.groupby("Curso")["Alunos"].sum().reset_index()
            for _, r in res_c.iterrows(): st.write(f"**{r['Curso']}:** {r['Alunos']}")
    with c2:
        with st.expander("📋 Alunos por Status", expanded=True):
            res_s = df_final_trabalho.groupby("Status")["Alunos"].sum().reset_index()
            for _, r in res_s.iterrows(): st.write(f"**{r['Status']}:** {r['Alunos']}")

    # --- TABELA EDITÁVEL ---
    st.divider()
    if "id" not in df_final_trabalho.columns: df_final_trabalho["id"] = None

    ordem_cols = ["id", "Turma", "Curso", "Alunos", "CNPJs", "Status"]
    ordem_ok = [c for c in ordem_cols if c in df_final_trabalho.columns]
    
    plano_editado = st.data_editor(
        df_final_trabalho[ordem_ok],
        column_config={"id": None, "Status": None},
        disabled=["Curso", "Alunos", "CNPJs"],
        use_container_width=True, hide_index=True, key="editor_principal"
    )

    # Auto-save Inteligente
    if supabase and (arquivo is not None or not plano_nuvem.empty):
        try:
            dados_db = []
            for row in plano_editado.to_dict(orient="records"):
                original = df_final_trabalho.loc[df_final_trabalho['Turma'] == row['Turma']].iloc[0]
                dados_db.append({
                    "Curso": str(row["Curso"]), "Turma": str(row["Turma"]), "Alunos": int(row["Alunos"]),
                    "UFs": original["UFs"], "CNPJs": str(row["CNPJs"]), 
                    "Status": original["Status"], "Arquivo": original["Arquivo"]
                })
            # Substituição total para manter a consistência do editor
            supabase.table("planejamentos_turmas").delete().neq("Curso", "0").execute()
            supabase.table("planejamentos_turmas").insert(dados_db).execute()
            st.toast("Nuvem Sincronizada!", icon="☁️")
        except: pass

    # --- FERRAMENTAS ---
    col_l, col_r = st.columns(2)
    with col_l:
        st.subheader("🔍 Busca")
        busca = st.text_input("Localizar por CNPJ:")
        if busca:
            res = plano_editado[plano_editado["CNPJs"].astype(str).str.contains(busca, na=False)]
            st.dataframe(res[["Curso", "Turma"]], hide_index=True)
    with col_r:
        st.subheader("⚠️ Ocupação")
        baixas = plano_editado[plano_editado["Alunos"] < min_alunos]
        if not baixas.empty: st.error(f"{len(baixas)} turmas abaixo do quórum.")

    st.download_button("📥 Exportar Excel", data=gerar_excel_final(plano_editado, df_base_original), file_name="planejamento.xlsx")
