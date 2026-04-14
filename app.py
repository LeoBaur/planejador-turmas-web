import streamlit as st
import pandas as pd
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
# SISTEMA DE LOGIN
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
                st.session_state.dados_salvos = pd.DataFrame()
                st.cache_resource.clear()
                st.rerun()
            except Exception:
                pass
        
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
    st.info("👋 Olá! Faça login na barra lateral para acessar o sistema.")
    st.stop()

# =========================
# CONEXÃO SUPABASE E MEMÓRIA CACHE
# =========================
@st.cache_resource
def init_connection():
    try:
        return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])
    except Exception:
        return None

supabase = init_connection()

def carregar_do_banco():
    if supabase:
        try:
            res = supabase.table("planejamentos_turmas").select("*").execute()
            return pd.DataFrame(res.data)
        except Exception:
            return pd.DataFrame()
    return pd.DataFrame()

# NOVO: Cache local para fluidez da tabela
if "dados_salvos" not in st.session_state:
    st.session_state.dados_salvos = carregar_do_banco()

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
            
            stats_counts = {}
            for g in grupo:
                s = str(g["Status"])
                if s != "nan" and s.strip() != "":
                    stats_counts[s] = stats_counts.get(s, 0) + 1
            
            stats_str_coded = "|".join([f"{k}:{v}" for k, v in stats_counts.items()])
            
            chave_busca = f"{cnpjs[0]}_{curso}"
            nome_original = mapa_nomes.get(chave_busca, f"{curso[:3].upper()}-{i+1:02d}")
            
            turmas_lista.append({
                "Curso": curso,
                "Turma": nome_original,
                "Alunos": len(grupo),
                "UFs": ", ".join(ufs),
                "CNPJs": ", ".join(cnpjs),
                "Status": stats_str_coded if stats_str_coded else "Não Informado:0",
                "Arquivo": nome_arquivo
            })
    return pd.DataFrame(turmas_lista)

# =========================
# INTERFACE PRINCIPAL E UPLOAD
# =========================
st.title("📊 Planejador Inteligente de Turmas")

arquivo = st.file_uploader("📤 Porta de Entrada (Subir Nova Planilha)", type=["xlsx"])

df_final_trabalho = st.session_state.dados_salvos.copy()
df_base_original = pd.DataFrame()

if arquivo:
    # Verificação inteligente para não reprocessar a mesma planilha
    arquivo_ja_existe = False
    if not df_final_trabalho.empty and "Arquivo" in df_final_trabalho.columns:
        if arquivo.name in df_final_trabalho["Arquivo"].values:
            arquivo_ja_existe = True

    if arquivo_ja_existe:
        st.info(f"O arquivo '{arquivo.name}' já está no painel. Se for uma nova versão, exclua a antiga no gestor abaixo primeiro.")
    else:
        try:
            df_raw = pd.read_excel(arquivo)
            df_base_original = df_raw.copy()
            
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

            if "Status" not in df_raw.columns:
                df_raw["Status"] = "Não Informado"

            df_raw["CNPJ"] = df_raw["CNPJ"].astype(str).str.strip()
            df_raw["Qtde"] = pd.to_numeric(df_raw["Qtde"], errors='coerce').fillna(0).astype(int)
            df_validos = df_raw[df_raw["Qtde"] > 0].copy()

            df_motor = df_validos.groupby(["Curso", "UF", "CNPJ", "Status"], as_index=False)["Qtde"].sum()
            df_novo_arquivo = gerar_turmas(df_motor, min_alunos, max_alunos, df_final_trabalho, arquivo.name)
            
            if supabase:
                supabase.table("planejamentos_turmas").delete().eq("Arquivo", arquivo.name).execute()
                dados_para_db = []
                for _, row in df_novo_arquivo.iterrows():
                    dados_para_db.append({
                        "Curso": str(row["Curso"]), "Turma": str(row["Turma"]), "Alunos": int(row["Alunos"]),
                        "UFs": str(row["UFs"]), "CNPJs": str(row["CNPJs"]), "Status": str(row["Status"]),
                        "Arquivo": str(row["Arquivo"])
                    })
                supabase.table("planejamentos_turmas").insert(dados_para_db).execute()
                
            st.success(f"Arquivo '{arquivo.name}' adicionado ao planejamento!")
            st.session_state.dados_salvos = carregar_do_banco()
            st.rerun()
            
        except Exception as e:
            st.error(f"Erro no processamento da planilha: {e}")

# =========================
# GESTOR DE ARQUIVOS CENTRAL
# =========================
if not df_final_trabalho.empty and "Arquivo" in df_final_trabalho.columns:
    arquivos_salvos = df_final_trabalho["Arquivo"].dropna().unique()
    if len(arquivos_salvos) > 0:
        with st.expander("📂 Gerenciar Arquivos Consolidados no Banco", expanded=False):
            st.caption("Planilhas unificadas na nuvem. Excluir uma planilha aqui removerá apenas os dados dela do planejamento total.")
            for arq in arquivos_salvos:
                c1, c2 = st.columns([8, 1])
                c1.write(f"📄 **{arq}**")
                if c2.button("❌", key=f"del_{arq}", help=f"Remover dados de {arq}"):
                    if supabase:
                        supabase.table("planejamentos_turmas").delete().eq("Arquivo", arq).execute()
                        st.session_state.dados_salvos = carregar_do_banco()
                        st.rerun()

# =========================
# PAINEL DE INDICADORES (KPIs)
# =========================
if not df_final_trabalho.empty:
    st.divider()
    st.subheader("🏁 Painel de Controle - Visão Geral")
    
    total_alunos = df_final_trabalho["Alunos"].sum()
    st.metric("Total Geral de Alunos no Planejamento", f"{total_alunos} alunos")
    
    c_met1, c_met2 = st.columns(2)
    
    with c_met1:
        with st.expander("🎓 Alunos por Tipo de Curso", expanded=True):
            resumo_curso = df_final_trabalho.groupby("Curso")["Alunos"].sum().reset_index()
            for _, r in resumo_curso.iterrows():
                st.write(f"**{r['Curso']}:** {r['Alunos']} alunos")

    with c_met2:
        with st.expander("📋 Alunos por Tipo de Solicitação", expanded=True):
            status_totals = {}
            for _, row in df_final_trabalho.iterrows():
                st_val = str(row["Status"]).strip()
                total_alunos_row = int(row["Alunos"])
                
                if not st_val or st_val == "nan":
                    st_val = "Não Informado"
                
                if ":" in st_val or "|" in st_val:
                    partes = st_val.split("|")
                    soma_interna = sum(int(p.split(":")[1]) for p in partes if ":" in p)
                    if soma_interna == 0: soma_interna = 1
                    
                    distribuido = 0
                    partes_validas = [p for p in partes if ":" in p]
                    
                    for i, p in enumerate(partes_validas):
                        s_nome, s_qtd = p.split(":")
                        s_qtd = int(s_qtd)
                        if i == len(partes_validas) - 1:
                            adjusted_qtd = total_alunos_row - distribuido
                        else:
                            adjusted_qtd = round((s_qtd / soma_interna) * total_alunos_row)
                            distribuido += adjusted_qtd
                        status_totals[s_nome] = status_totals.get(s_nome, 0) + adjusted_qtd
                        
                elif "," in st_val:
                    partes = [p.strip() for p in st_val.split(",") if p.strip()]
                    if not partes: partes = ["Não Informado"]
                    val_base = total_alunos_row // len(partes)
                    sobra = total_alunos_row % len(partes)
                    for i, p in enumerate(partes):
                        adjusted_qtd = val_base + (1 if i < sobra else 0)
                        status_totals[p] = status_totals.get(p, 0) + adjusted_qtd
                else:
                    status_totals[st_val] = status_totals.get(st_val, 0) + total_alunos_row
            
            for st_nome, count in status_totals.items():
                st.write(f"**{st_nome}:** {count} alunos")

    # =========================
    # TABELA E AUTO-SAVE BLINDADO (Sem travamentos)
    # =========================
    st.divider()
    st.subheader("📚 Ajuste de Planejamento")
    
    df_final_trabalho = df_final_trabalho.reset_index(drop=True)
    
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

    # LÓGICA DE SALVAMENTO DE ALTA PERFORMANCE
    if supabase and not plano_editado.empty:
        df_comp_old = df_final_trabalho[ordem_ok].fillna("").astype(str).to_dict("records")
        df_comp_new = plano_editado.fillna("").astype(str).to_dict("records")
        
        if df_comp_old != df_comp_new:
            try:
                dados_para_db = []
                for index, row in plano_editado.iterrows():
                    original = df_final_trabalho.iloc[index]
                    dados_para_db.append({
                        "Curso": str(row.get("Curso", "")), 
                        "Turma": str(row.get("Turma", "")), 
                        "Alunos": int(row.get("Alunos", 0)),
                        "UFs": str(original["UFs"]), 
                        "CNPJs": str(row.get("CNPJs", "")), 
                        "Status": str(original["Status"]),
                        "Arquivo": str(original["Arquivo"])
                    })
                
                # 1. Atualiza a memória instantaneamente para evitar bugs na digitação
                st.session_state.dados_salvos = pd.DataFrame(dados_para_db)
                
                # 2. Salva silenciosamente no Supabase
                supabase.table("planejamentos_turmas").delete().neq("Curso", "0").execute()
                supabase.table("planejamentos_turmas").insert(dados_para_db).execute()
                
            except Exception as e:
                pass

    # =========================
    # BUSCA E ALERTAS (Gráficos Removidos para Limpeza Visual)
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
    st.download_button("📥 Baixar Excel Completo", data=gerar_excel_final(plano_editado, df_base_original), file_name="planejamento_senac.xlsx")
