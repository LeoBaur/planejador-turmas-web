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
        df_export = plano_df.copy()
        for col in ["id", "Arquivo"]:
            if col in df_export.columns:
                df_export = df_export.drop(columns=[col])
                
        df_export.to_excel(writer, sheet_name="Planejamento", index=False)
        if not original_df.empty:
            original_df.to_excel(writer, sheet_name="Base_Original", index=False)
    return output.getvalue()

# =========================
# SISTEMA DE LOGIN (Logout reposicionado e Enter ativado)
# =========================
if 'autenticado' not in st.session_state:
    st.session_state.autenticado = False

with st.sidebar:
    if not st.session_state.autenticado:
        st.subheader("🔒 Acesso ao Sistema")
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
        # Interface de Logoff intuitiva no topo
        st.success("👤 Logado como Admin")
        if st.button("🚪 Sair (Logout)", use_container_width=True, type="primary"):
            st.session_state.autenticado = False
            st.rerun()
        
        st.divider()
        st.subheader("⚙️ Configurações")
        min_alunos = st.number_input("Mínimo por turma", min_value=1, value=30)
        max_alunos = st.number_input("Máximo por turma", min_value=1, value=45)
        
        st.divider()
        st.subheader("⚠️ Zona de Perigo")
        if st.button("🚨 Resetar Planejamento (Zerar Banco)", use_container_width=True):
            try:
                url = st.secrets["SUPABASE_URL"]
                key = st.secrets["SUPABASE_KEY"]
                client = create_client(url, key)
                client.table("planejamentos_turmas").delete().neq("Curso", "0").execute()
                st.cache_resource.clear()
                st.rerun()
            except: pass
        
        st.divider()
        st.write("📂 **Modelos**")
        st.download_button(
            label="📥 Baixar Modelo de Planilha",
            data=gerar_modelo_excel(),
            file_name="modelo_senac.xlsx",
            use_container_width=True
        )

if not st.session_state.autenticado:
    st.title("📊 Planejador Inteligente de Turmas")
    st.info("👋 Olá! Faça login na barra lateral para acessar o sistema. Suporte a tecla Enter ativado.")
    st.stop()

# =========================
# CONEXÃO SUPABASE
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
                "Status": str(row.get("Status", "Não Informado"))
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
                "Status": ", ".join(stats) if stats else "Não Informado",
                "Arquivo": nome_arquivo
            })
    return pd.DataFrame(turmas_lista)

# =========================
# INTERFACE PRINCIPAL
# =========================
st.title("📊 Planejador Inteligente de Turmas")

plano_nuvem = carregar_do_banco()

# Porta de Entrada
arquivo = st.file_uploader("📤 Porta de Entrada (Subir Nova Planilha)", type=["xlsx"])

df_final_trabalho = pd.DataFrame()
df_base_original = pd.DataFrame()

if arquivo:
    try:
        df_raw = pd.read_excel(arquivo)
        df_base_original = df_raw.copy()
        
        # Padronização de Colunas
        df_raw.columns = [str(c).strip().title() for c in df_raw.columns]
        df_raw = df_raw.rename(columns={"Uf": "UF", "Cnpj": "CNPJ", "Qtde": "Qtde"})

        if "Status" not in df_raw.columns:
            df_raw["Status"] = "Não Informado"

        df_raw["CNPJ"] = df_raw["CNPJ"].astype(str).str.strip()
        df_raw["Qtde"] = pd.to_numeric(df_raw["Qtde"], errors='coerce').fillna(0).astype(int)
        df_validos = df_raw[df_raw["Qtde"] > 0].copy()

        df_motor = df_validos.groupby(["Curso", "UF", "CNPJ", "Status"], as_index=False)["Qtde"].sum()
        df_final_trabalho = gerar_turmas(df_motor, min_alunos, max_alunos, plano_nuvem, arquivo.name)
        st.success(f"Arquivo '{arquivo.name}' sincronizado com a nuvem!")
        
    except Exception as e:
        st.error(f"Erro no processamento da planilha: {e}")
elif not plano_nuvem.empty:
    df_final_trabalho = plano_nuvem.copy()

# =========================
# GESTOR DE ARQUIVOS CENTRAL (Painel intuitivo para exclusão)
# =========================
if not df_final_trabalho.empty and "Arquivo" in df_final_trabalho.columns:
    arquivos_salvos = df_final_trabalho["Arquivo"].dropna().unique()
    if len(arquivos_salvos) > 0:
        with st.expander("📂 Gerenciar Arquivos Consolidados no Banco", expanded=False):
            st.caption("Abaixo estão as planilhas que já estão salvas na nuvem. Excluir aqui removerá os dados do banco definitivamente.")
            for arq in arquivos_salvos:
                c1, c2 = st.columns([8, 1])
                c1.write(f"📄 **{arq}**")
                if c2.button("❌", key=f"del_{arq}", help=f"Remover dados de {arq}"):
                    supabase.table("planejamentos_turmas").delete().eq("Arquivo", arq).execute()
                    st.cache_resource.clear()
                    st.rerun()

# =========================
# PAINEL DE INDICADORES (KPIs REFINADOS)
# =========================
if not df_final_trabalho.empty:
    st.divider()
    st.subheader("🏁 Painel de Controle - Visão Geral")
    
    total_alunos = df_final_trabalho["Alunos"].sum()
    
    # KPI Principal focado apenas em alunos
    st.metric("Soma Total de Alunos no Planejamento", f"{total_alunos} alunos")
    
    c_met1, c_met2 = st.columns(2)
    
    with c_met1:
        with st.expander("🎓 Alunos por Tipo de Curso", expanded=True):
            resumo_curso = df_final_trabalho.groupby("Curso")["Alunos"].sum().reset_index()
            for _, r in resumo_curso.iterrows():
                st.write(f"**{r['Curso']}:** {r['Alunos']} alunos")

    with c_met2:
        # MELHORIA 2: Novo nome e lógica focada apenas em alunos
        with st.expander("📋 Alunos por Tipo de Solicitação", expanded=True):
            resumo_status = df_final_trabalho.groupby("Status")["Alunos"].sum().reset_index()
            for _, r in resumo_status.iterrows():
                st.write(f"**{r['Status']}:** {r['Alunos']} alunos")

    # =========================
    # TABELA E AUTO-SAVE
    # =========================
    st.divider()
    st.subheader("📚 Ajuste de Planejamento")
    
    if "id" not in df_final_trabalho.columns: df_final_trabalho["id"] = None
    if "Arquivo" not in df_final_trabalho.columns: df_final_trabalho["Arquivo"] = "Desconhecido"
    if "UFs" not in df_final_trabalho.columns: df_final_trabalho["UFs"] = "N/A"

    ordem_cols = ["id", "Turma", "Curso", "Alunos", "CNPJs", "Status", "Arquivo", "UFs"]
    ordem_ok = [c for c in ordem_cols if c in df_final_trabalho.columns]
    
    plano_editado = st.data_editor(
        df_final_trabalho[ordem_ok],
        column_config={
            "id": None,
            "Status": None,
            "Arquivo": None,
            "UFs": None,
            "Turma": st.column_config.TextColumn("Turma (Editável)"),
            "CNPJs": st.column_config.TextColumn("CNPJs"),
        },
        disabled=["Curso", "Alunos", "CNPJs"],
        use_container_width=True,
        hide_index=True,
        key="editor_principal"
    )

    if supabase and (arquivo is not None or not plano_nuvem.empty):
        try:
            dados_para_db = []
            for row in plano_editado.to_dict(orient="records"):
                dados_para_db.append({
                    "Curso": str(row.get("Curso", "")), 
                    "Turma": str(row.get("Turma", "")), 
                    "Alunos": int(row.get("Alunos", 0)),
                    "UFs": str(row.get("UFs", "")), 
                    "CNPJs": str(row.get("CNPJs", "")), 
                    "Status": str(row.get("Status", "")),
                    "Arquivo": str(row.get("Arquivo", ""))
                })
            
            supabase.table("planejamentos_turmas").delete().neq("Curso", "0").execute()
            supabase.table("planejamentos_turmas").insert(dados_para_db).execute()
        except: pass

    # =========================
    # BUSCA, ALERTAS E GRÁFICOS
    # =========================
    col_l, col_r = st.columns(2)
    with col_l:
        st.subheader("🔍 Localizador de CNPJ")
        busca = st.text_input("Digite o CNPJ para localizar a turma:")
        if busca:
            res = plano_editado[plano_editado["CNPJs"].astype(str).str.contains(busca, na=False)]
            if not res.empty:
                st.success("Localizado!")
                st.dataframe(res[["Curso", "Turma"]], hide_index=True)
            else:
                st.warning("CNPJ não encontrado.")

    with col_r:
        st.subheader("⚠️ Alertas de Ocupação")
        baixas = plano_editado[plano_editado["Alunos"] < min_alunos]
        if not baixas.empty:
            st.error(f"{len(baixas)} turmas abaixo do quórum.")
            st.dataframe(baixas[["Curso", "Turma", "Alunos"]], hide_index=True)
        else:
            st.success("Quórum atingido em todas as turmas.")

    st.divider()
    c1, c2, c3 = st.columns(3)
    resumo_t = plano_editado.groupby("Curso").size().reset_index(name="Turmas")
    with c1: st.plotly_chart(px.bar(resumo_t, x="Curso", y="Turmas", title="Turmas por Curso"), use_container_width=True)
    with c2: st.plotly_chart(px.pie(plano_editado, names="Curso", title="Mix de Cursos"), use_container_width=True)
    with c3: st.plotly_chart(px.histogram(plano_editado, x="Alunos", title="Distribuição de Alunos"), use_container_width=True)

    st.download_button("📥 Baixar Excel Completo", data=gerar_excel_final(plano_editado, df_base_original), file_name="planejamento_senac.xlsx")
