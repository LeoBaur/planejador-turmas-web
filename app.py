import streamlit as st
import pandas as pd
import math
import threading
import re
from io import BytesIO
from supabase import create_client, Client

st.set_page_config(page_title="Planejador Inteligente de Turmas", layout="wide")

# =========================
# FUNÇÕES DE APOIO E THREADING
# =========================
def gerar_modelo_excel():
    modelo_df = pd.DataFrame({
        "Curso": ["Administração", "Logística"], "UF": ["PR", "SP"],
        "CNPJ": ["11111111000100", "22222222000100"], "Qtde": [30, 25], "Status": ["Em Atendimento", "Pré-Matrícula"]
    })
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer: modelo_df.to_excel(writer, index=False, sheet_name="Modelo")
    return output.getvalue()

def gerar_excel_final(plano_df, original_df):
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df_export = plano_df.copy()
        for col in ["id", "Arquivo"]:
            if col in df_export.columns: df_export = df_export.drop(columns=[col])
        df_export.to_excel(writer, sheet_name="Planejamento", index=False)
        if not original_df.empty: original_df.to_excel(writer, sheet_name="Base_Original", index=False)
    return output.getvalue()

def higienizar_status(status_str):
    if pd.isna(status_str) or str(status_str).strip() == "": return "Não Informado"
    return " ".join(str(status_str).split()).title()

def salvar_background(dados_dict, url, key):
    try:
        client = create_client(url, key)
        client.table("planejamentos_turmas").delete().neq("Curso", "0").execute()
        client.table("planejamentos_turmas").insert(dados_dict).execute()
    except Exception: pass

def get_next_turma_name(curso, nomes_usados):
    prefix = curso[:3].upper()
    i = 1
    while True:
        name = f"{prefix}-{i:02d}"
        if name not in nomes_usados:
            nomes_usados.append(name)
            return name
        i += 1

def extrair_cnpjs_historico(turmas_estado):
    """Tira uma 'foto' de onde cada CNPJ está antes do processamento"""
    historico = {}
    for t in turmas_estado:
        partes = str(t.get("CNPJs", "")).split(",")
        for p in partes:
            p = p.strip()
            if not p: continue
            match = re.match(r"(.+?)\s*\((\d+)\)", p)
            cnpj_limpo = match.group(1).strip() if match else p
            historico[f"{t['Curso']}_{cnpj_limpo}"] = t["Turma"]
    return historico

# =========================
# SISTEMA DE LOGIN
# =========================
if 'autenticado' not in st.session_state: st.session_state.autenticado = False

with st.sidebar:
    if not st.session_state.autenticado:
        st.subheader("🔒 Acesso")
        with st.form("login_form"):
            usuario = st.text_input("Usuário")
            senha = st.text_input("Senha", type="password")
            if st.form_submit_button("Entrar"):
                if usuario == "admin" and senha == "senac123":
                    st.session_state.autenticado = True
                    st.rerun()
                else: st.error("Credenciais inválidas")
    else:
        st.success("👤 Admin Logado")
        if st.button("🚪 Sair", use_container_width=True, type="primary"):
            st.session_state.autenticado = False
            st.rerun()
        
        st.divider()
        st.subheader("⚙️ Regras de Ocupação")
        min_alunos = st.number_input("Mínimo por turma", min_value=1, value=25)
        max_alunos = st.number_input("Máximo por turma", min_value=1, value=45)
        
        st.divider()
        st.subheader("⚠️ Zona de Perigo")
        if st.button("🚨 Resetar Planejamento", use_container_width=True):
            try:
                create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"]).table("planejamentos_turmas").delete().neq("Curso", "0").execute()
                st.session_state.dados_salvos = pd.DataFrame()
                if "editor_principal" in st.session_state: del st.session_state["editor_principal"]
                if "last_saved_hash" in st.session_state: del st.session_state["last_saved_hash"]
                st.cache_resource.clear()
                st.rerun()
            except Exception: pass
        
        st.download_button("📥 Modelo", data=gerar_modelo_excel(), file_name="modelo_senac.xlsx", use_container_width=True)

if not st.session_state.autenticado:
    st.title("📊 Planejador Inteligente de Turmas")
    st.info("Faça login na barra lateral.")
    st.stop()

# =========================
# CONEXÃO SUPABASE
# =========================
@st.cache_resource
def init_connection():
    try: return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])
    except Exception: return None

supabase = init_connection()

def carregar_do_banco():
    if supabase:
        try: return pd.DataFrame(supabase.table("planejamentos_turmas").select("*").execute().data)
        except Exception: return pd.DataFrame()
    return pd.DataFrame()

if "dados_salvos" not in st.session_state: st.session_state.dados_salvos = carregar_do_banco()

# =========================
# MOTOR PRINCIPAL (BIN PACKING COM AUDITORIA)
# =========================
st.title("📊 Planejador Inteligente de Turmas")

df_final_trabalho = st.session_state.dados_salvos.copy()
df_base_original = pd.DataFrame()

arquivo = st.file_uploader("📤 Subir Nova Planilha de Alunos", type=["xlsx"])

if arquivo:
    if not df_final_trabalho.empty and "Arquivo" in df_final_trabalho.columns and any(arquivo.name in str(val) for val in df_final_trabalho["Arquivo"].values):
        st.info(f"O arquivo '{arquivo.name}' já está no banco de dados.")
    else:
        if st.button("🚀 Processar Otimização Automática", type="primary"):
            try:
                df_raw = pd.read_excel(arquivo)
                df_base_original = df_raw.copy()
                
                # Padronização e Higienização
                mapa_renomear = {c: "UF" if str(c).upper() in ["UF", "ESTADO"] else "CNPJ" if str(c).upper() in ["CNPJ", "CLIENTE"] else "Qtde" if str(c).upper() in ["QTDE", "QUANTIDADE", "ALUNOS"] else "Status" if str(c).upper() in ["STATUS", "SITUAÇÃO", "FASE", "SITUACAO"] else "Curso" if str(c).upper() in ["CURSO", "NOME DO CURSO"] else c for c in df_raw.columns}
                df_raw = df_raw.rename(columns=mapa_renomear)
                if "Status" not in df_raw.columns: df_raw["Status"] = "Não Informado"
                
                df_raw["CNPJ"] = df_raw["CNPJ"].astype(str).str.strip()
                df_raw["Qtde"] = pd.to_numeric(df_raw["Qtde"], errors='coerce').fillna(0).astype(int)
                df_validos = df_raw[df_raw["Qtde"] > 0].copy()
                df_motor = df_validos.groupby(["Curso", "UF", "CNPJ", "Status"], as_index=False)["Qtde"].sum()

                turmas_estado_atual = df_final_trabalho.to_dict('records') if not df_final_trabalho.empty else []
                foto_antiga = extrair_cnpjs_historico(turmas_estado_atual)
                cursos_na_planilha = df_motor["Curso"].unique()
                
                turmas_intactas = [t for t in turmas_estado_atual if t["Curso"] not in cursos_na_planilha]
                turmas_otimizadas = []
                
                for curso in cursos_na_planilha:
                    lotes_curso = []
                    
                    # Extrai Blocos Intactos do Banco
                    for t in [x for x in turmas_estado_atual if x["Curso"] == curso]:
                        partes = str(t.get("CNPJs", "")).split(",")
                        for p in partes:
                            p = p.strip()
                            if not p: continue
                            match = re.match(r"(.+?)\s*\((\d+)\)", p)
                            if match:
                                c = match.group(1).strip()
                                q = int(match.group(2))
                                lotes_curso.append({"cnpj": c, "qtde": q, "uf": str(t.get("UFs", "")), "status": str(t.get("Status", "")), "arquivo": str(t.get("Arquivo", "Banco"))})
                    
                    # Adiciona Blocos Novos da Planilha
                    for _, row in df_motor[df_motor["Curso"] == curso].iterrows():
                        if int(row["Qtde"]) > 0:
                            lotes_curso.append({"cnpj": str(row["CNPJ"]), "qtde": int(row["Qtde"]), "uf": str(row["UF"]), "status": higienizar_status(row.get("Status")), "arquivo": arquivo.name})
                            
                    # Agrupa lotes do mesmo CNPJ para garantir que não serão separados
                    agrupamento_cnpjs = {}
                    for lote in lotes_curso:
                        chave = lote["cnpj"]
                        if chave not in agrupamento_cnpjs:
                            agrupamento_cnpjs[chave] = {"cnpj": chave, "qtde": 0, "ufs": set(), "arquivos": set(), "status_dict": {}}
                        
                        agrupamento_cnpjs[chave]["qtde"] += lote["qtde"]
                        for u in lote["uf"].split(","): 
                            if u.strip() and u.strip() != "nan": agrupamento_cnpjs[chave]["ufs"].add(u.strip())
                        for a in lote["arquivo"].split(","): 
                            if a.strip() and a.strip() != "nan": agrupamento_cnpjs[chave]["arquivos"].add(a.strip())
                        
                        # Soma Status
                        partes_status = lote["status"].split("|")
                        for ps in partes_status:
                            if ":" in ps:
                                k, v = ps.split(":")
                                agrupamento_cnpjs[chave]["status_dict"][k] = agrupamento_cnpjs[chave]["status_dict"].get(k, 0) + int(v)
                            else:
                                if ps.strip() and ps.strip() != "Não Informado":
                                    agrupamento_cnpjs[chave]["status_dict"][ps] = agrupamento_cnpjs[chave]["status_dict"].get(ps, 0) + lote["qtde"]
                    
                    # Transforma de volta em lista e ordena (First Fit Decreasing - O melhor para Bin Packing)
                    blocos_finais = list(agrupamento_cnpjs.values())
                    blocos_finais.sort(key=lambda x: x["qtde"], reverse=True)
                    
                    caixas_turmas = []
                    nomes_usados_curso = []
                    
                    # Empacotamento
                    for bloco in blocos_finais:
                        alocado = False
                        # Tenta encaixar na primeira turma que tiver espaço
                        for caixa in caixas_turmas:
                            if caixa["Alunos"] + bloco["qtde"] <= max_alunos:
                                caixa["Alunos"] += bloco["qtde"]
                                caixa["_blocos"].append(bloco)
                                alocado = True
                                break
                        
                        # Se não coube em nenhuma, abre turma nova
                        if not alocado:
                            nome_t = get_next_turma_name(curso, nomes_usados_curso)
                            caixas_turmas.append({
                                "id": None, "Curso": curso, "Turma": nome_t, 
                                "Alunos": bloco["qtde"], "_blocos": [bloco]
                            })
                    
                    # TENTATIVA DE ROBIN HOOD (Rebalanceamento para salvar os abaixo do mínimo)
                    for pobre in [c for c in caixas_turmas if c["Alunos"] < min_alunos]:
                        for rico in [c for c in caixas_turmas if c["Alunos"] >= min_alunos]:
                            for b_cand in rico["_blocos"].copy():
                                # Se eu tirar o bloco do rico, ele continua acima do min? E o pobre não estoura o max?
                                if (rico["Alunos"] - b_cand["qtde"] >= min_alunos) and (pobre["Alunos"] + b_cand["qtde"] <= max_alunos):
                                    rico["_blocos"].remove(b_cand)
                                    pobre["_blocos"].append(b_cand)
                                    rico["Alunos"] -= b_cand["qtde"]
                                    pobre["Alunos"] += b_cand["qtde"]

                    # Compilar dados estruturados
                    for caixa in caixas_turmas:
                        caixa["CNPJs"] = ", ".join([f"{b['cnpj']} ({b['qtde']})" for b in caixa["_blocos"]])
                        caixa["UFs"] = ", ".join(sorted(set().union(*[b["ufs"] for b in caixa["_blocos"]])))
                        caixa["Arquivo"] = ", ".join(sorted(set().union(*[b["arquivos"] for b in caixa["_blocos"]])))
                        
                        stats_consolidados = {}
                        for b in caixa["_blocos"]:
                            for k, v in b["status_dict"].items():
                                stats_consolidados[k] = stats_consolidados.get(k, 0) + v
                        
                        if not stats_consolidados: caixa["Status"] = "Não Informado"
                        else: caixa["Status"] = "|".join([f"{k}:{v}" for k, v in stats_consolidados.items() if v > 0])
                        
                        del caixa["_blocos"]
                        turmas_otimizadas.append(caixa)

                turmas_finais = turmas_intactas + turmas_otimizadas
                
                # Geração do Relatório de Auditoria (Rastreamento de Movimentos)
                relatorio_auditoria = []
                for t_nova in turmas_otimizadas:
                    partes = t_nova["CNPJs"].split(",")
                    for p in partes:
                        match = re.match(r"(.+?)\s*\(", p.strip())
                        if match:
                            cnpj = match.group(1).strip()
                            chave_banco = f"{t_nova['Curso']}_{cnpj}"
                            if chave_banco in foto_antiga and foto_antiga[chave_banco] != t_nova["Turma"]:
                                relatorio_auditoria.append(f"🔄 **CNPJ {cnpj}** | Movido de: `{foto_antiga[chave_banco]}` ➡️ Para: `{t_nova['Turma']}`")

                # Salvar no Banco
                if supabase:
                    supabase.table("planejamentos_turmas").delete().neq("Curso", "0").execute()
                    for t in turmas_finais:
                        if "id" in t: del t["id"]
                    supabase.table("planejamentos_turmas").insert(turmas_finais).execute()
                    
                st.success(f"Arquivo e otimização concluídos respeitando os blocos de CNPJ!")
                
                # Guarda o relatório na sessão para exibir após o rerun
                if relatorio_auditoria: st.session_state.relatorio_auditoria = relatorio_auditoria
                else: st.session_state.relatorio_auditoria = ["✅ Nenhum CNPJ precisou mudar de turma. Alocação perfeita mantida."]
                
                st.session_state.dados_salvos = carregar_do_banco()
                st.rerun()
            except Exception as e:
                st.error(f"Erro no processamento: {e}")

# =========================
# RELATÓRIO DE AUDITORIA (MOVIMENTAÇÕES)
# =========================
if hasattr(st.session_state, 'relatorio_auditoria') and st.session_state.relatorio_auditoria:
    with st.expander("📋 Relatório de Movimentação Automática de CNPJs", expanded=True):
        st.write("O sistema otimizou as turmas e executou as seguintes mudanças de rota para tentar preservar limites sem dividir os CNPJs:")
        for r in st.session_state.relatorio_auditoria: st.write(r)

# =========================
# PAINEL DE INDICADORES (KPIs)
# =========================
if not df_final_trabalho.empty:
    st.divider()
    st.subheader("🏁 Painel de Controle")
    st.metric("Total Geral de Alunos", f"{df_final_trabalho['Alunos'].sum()} alunos")
    
    # KPIs Visuais (Resumidos para clareza do código)
    c_met1, c_met2 = st.columns(2)
    with c_met1:
        with st.expander("🎓 Alunos por Curso", expanded=True):
            for _, r in df_final_trabalho.groupby("Curso")["Alunos"].sum().reset_index().iterrows():
                st.write(f"**{r['Curso']}:** {r['Alunos']} alunos")

    with c_met2:
        with st.expander("⚠️ Alerta de Paradoxo Matemático", expanded=True):
            baixas = df_final_trabalho[df_final_trabalho["Alunos"] < min_alunos]
            altas = df_final_trabalho[df_final_trabalho["Alunos"] > max_alunos]
            if not baixas.empty or not altas.empty:
                st.warning("Devido à regra de não dividir os CNPJs, as seguintes turmas ficaram fora do padrão estrito. O algoritmo fez o melhor possível. Ajuste manualmente se necessário.")
                if not baixas.empty: st.error(f"Abaixo de {min_alunos}: " + ", ".join(baixas["Turma"].tolist()))
                if not altas.empty: st.error(f"Acima de {max_alunos}: " + ", ".join(altas["Turma"].tolist()))
            else:
                st.success(f"100% das turmas estão saudáveis (entre {min_alunos} e {max_alunos}). Encaixe perfeito!")

    # =========================
    # TABELA COM EDIÇÃO LIVRE
    # =========================
    st.divider()
    st.subheader("📚 Grade de Planejamento Final")
    
    ordem_cols = ["id", "Turma", "Curso", "Alunos", "CNPJs", "Status", "Arquivo", "UFs"]
    ordem_ok = [c for c in ordem_cols if c in df_final_trabalho.columns]
    
    plano_editado = st.data_editor(
        df_final_trabalho[ordem_ok],
        column_config={"Turma": "Turma", "CNPJs": "CNPJs (Ex: 1111 [15])", "Alunos": "Alunos"},
        disabled=["Curso"], use_container_width=True, hide_index=True
    )

    if supabase and not plano_editado.empty:
        dict_editado = plano_editado.fillna("").astype(str).to_dict("records")
        current_hash = hash(str(dict_editado))
        if "last_saved_hash" not in st.session_state: st.session_state.last_saved_hash = hash(str(df_final_trabalho[ordem_ok].fillna("").astype(str).to_dict("records")))
        if current_hash != st.session_state.last_saved_hash:
            st.session_state.last_saved_hash = current_hash
            dados_para_db = [{"Curso": str(r.get("Curso", "")), "Turma": str(r.get("Turma", "")), "Alunos": int(r.get("Alunos", 0)), "UFs": str(df_final_trabalho.iloc[i]["UFs"]), "CNPJs": str(r.get("CNPJs", "")), "Status": str(df_final_trabalho.iloc[i]["Status"]), "Arquivo": str(df_final_trabalho.iloc[i]["Arquivo"])} for i, r in plano_editado.iterrows()]
            threading.Thread(target=salvar_background, args=(dados_para_db, st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])).start()

    st.download_button("📥 Baixar Excel Completo", data=gerar_excel_final(plano_editado, df_base_original), file_name="planejamento_senac.xlsx")
