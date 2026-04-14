import streamlit as st
import pandas as pd
import math
import threading
from io import BytesIO
from supabase import create_client, Client

# Configurações iniciais
st.set_page_config(page_title="Planejador Inteligente de Turmas", layout="wide")

# =========================
# FUNÇÕES DE APOIO E THREADING
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

def higienizar_status(status_str):
    if pd.isna(status_str) or str(status_str).strip() == "":
        return "Não Informado"
    return " ".join(str(status_str).split()).title()

def salvar_background(dados_dict, url, key):
    try:
        client = create_client(url, key)
        client.table("planejamentos_turmas").delete().neq("Curso", "0").execute()
        client.table("planejamentos_turmas").insert(dados_dict).execute()
    except Exception:
        pass

# =========================
# LÓGICA DE FUSÃO, DISTRIBUIÇÃO E PREENCHIMENTO
# =========================
def merge_strings_list(s1, s2):
    l1 = [x.strip() for x in str(s1).split(",") if x.strip() and x.strip() != "nan"]
    l2 = [x.strip() for x in str(s2).split(",") if x.strip() and x.strip() != "nan"]
    return ", ".join(sorted(set(l1 + l2)))

def fundir_turmas(nome_origem, nome_destino, curso, url, key):
    client = create_client(url, key)
    res = client.table("planejamentos_turmas").select("*").eq("Curso", curso).in_("Turma", [nome_origem, nome_destino]).execute()
    df_db = pd.DataFrame(res.data)
    
    if len(df_db) == 2:
        origem = df_db[df_db["Turma"] == nome_origem].iloc[0]
        destino = df_db[df_db["Turma"] == nome_destino].iloc[0]
        
        novos_alunos = int(destino["Alunos"]) + int(origem["Alunos"])
        novos_cnpjs = merge_strings_list(origem["CNPJs"], destino["CNPJs"])
        novas_ufs = merge_strings_list(origem["UFs"], destino["UFs"])
        novos_arqs = merge_strings_list(origem["Arquivo"], destino["Arquivo"])
        
        stats_dict = {}
        for s in [str(origem["Status"]), str(destino["Status"])]:
            for p in s.split("|"):
                if ":" in p:
                    k, v = p.split(":")
                    k_clean = higienizar_status(k)
                    stats_dict[k_clean] = stats_dict.get(k_clean, 0) + int(v)
        novo_status = "|".join([f"{k}:{v}" for k, v in stats_dict.items()])
        
        client.table("planejamentos_turmas").update({
            "Alunos": novos_alunos, "CNPJs": novos_cnpjs, "UFs": novas_ufs,
            "Arquivo": novos_arqs, "Status": novo_status
        }).eq("id", destino["id"]).execute()
        
        client.table("planejamentos_turmas").delete().eq("id", origem["id"]).execute()

def distribuir_turma(nome_origem, curso, url, key):
    client = create_client(url, key)
    res = client.table("planejamentos_turmas").select("*").eq("Curso", curso).execute()
    df_db = pd.DataFrame(res.data)

    origem = df_db[df_db["Turma"] == nome_origem]
    if origem.empty: return
    origem = origem.iloc[0]
    
    destinos = df_db[df_db["Turma"] != nome_origem]
    num_destinos = len(destinos)
    if num_destinos == 0: return

    alunos_total = int(origem["Alunos"])
    base_add = alunos_total // num_destinos
    sobra_add = alunos_total % num_destinos
    adds = [base_add + (1 if i < sobra_add else 0) for i in range(num_destinos)]

    origem_stats = {}
    for p in str(origem["Status"]).split("|"):
        if ":" in p:
            k, v = p.split(":")
            origem_stats[higienizar_status(k)] = int(v)

    for i, (_, dest) in enumerate(destinos.iterrows()):
        if adds[i] == 0: continue

        new_alunos = int(dest["Alunos"]) + adds[i]
        new_cnpjs = merge_strings_list(dest["CNPJs"], origem["CNPJs"])
        new_ufs = merge_strings_list(dest["UFs"], origem["UFs"])
        new_arqs = merge_strings_list(dest["Arquivo"], origem["Arquivo"])

        dest_stats = {}
        for p in str(dest["Status"]).split("|"):
            if ":" in p:
                k, v = p.split(":")
                dest_stats[higienizar_status(k)] = int(v)

        allocated = 0
        for k in list(origem_stats.keys()):
            if allocated == adds[i]: break
            if origem_stats[k] > 0:
                take = min(origem_stats[k], adds[i] - allocated)
                origem_stats[k] -= take
                dest_stats[k] = dest_stats.get(k, 0) + take
                allocated += take

        new_status = "|".join([f"{k}:{v}" for k, v in dest_stats.items() if v > 0])

        client.table("planejamentos_turmas").update({
            "Alunos": new_alunos, "CNPJs": new_cnpjs, "UFs": new_ufs,
            "Arquivo": new_arqs, "Status": new_status
        }).eq("id", dest["id"]).execute()

    client.table("planejamentos_turmas").delete().eq("id", origem["id"]).execute()

# =========================
# SISTEMA DE LOGIN E LIMPEZA
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
        min_alunos = st.number_input("Mínimo por turma", min_value=1, value=25)
        max_alunos = st.number_input("Máximo por turma", min_value=1, value=45)
        
        st.divider()
        st.subheader("⚠️ Zona de Perigo")
        if st.button("🚨 Resetar Planejamento (Zerar Banco)", use_container_width=True):
            try:
                client = create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])
                client.table("planejamentos_turmas").delete().neq("Curso", "0").execute()
                st.session_state.dados_salvos = pd.DataFrame()
                if "editor_principal" in st.session_state: del st.session_state["editor_principal"]
                if "last_saved_hash" in st.session_state: del st.session_state["last_saved_hash"]
                st.cache_resource.clear()
                st.rerun()
            except Exception: pass
        
        st.divider()
        st.write("📂 **Modelos**")
        st.download_button("📥 Baixar Modelo de Planilha", data=gerar_modelo_excel(), file_name="modelo_senac.xlsx", use_container_width=True)

if not st.session_state.autenticado:
    st.title("📊 Planejador Inteligente de Turmas")
    st.info("👋 Olá! Faça login na barra lateral para acessar o sistema.")
    st.stop()

# =========================
# CONEXÃO SUPABASE E CACHE LOCAL
# =========================
@st.cache_resource
def init_connection():
    try: return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])
    except Exception: return None

supabase = init_connection()

def carregar_do_banco():
    if supabase:
        try:
            res = supabase.table("planejamentos_turmas").select("*").execute()
            return pd.DataFrame(res.data)
        except Exception: return pd.DataFrame()
    return pd.DataFrame()

if "dados_salvos" not in st.session_state:
    st.session_state.dados_salvos = carregar_do_banco()

# =========================
# INTERFACE PRINCIPAL E UPLOAD INTELIGENTE
# =========================
st.title("📊 Planejador Inteligente de Turmas")

df_final_trabalho = st.session_state.dados_salvos.copy()
df_base_original = pd.DataFrame()

arquivo = st.file_uploader("📤 Porta de Entrada (Subir Nova Planilha)", type=["xlsx"])

if arquivo:
    arquivo_ja_existe = False
    if not df_final_trabalho.empty and "Arquivo" in df_final_trabalho.columns:
        if any(arquivo.name in str(val) for val in df_final_trabalho["Arquivo"].values):
            arquivo_ja_existe = True

    if arquivo_ja_existe:
        st.info(f"O arquivo '{arquivo.name}' já foi processado e está mesclado no banco.")
    else:
        if st.button("🚀 Processar, Preencher Vagas e Salvar", type="primary"):
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
                if "Status" not in df_raw.columns: df_raw["Status"] = "Não Informado"
                
                df_raw["CNPJ"] = df_raw["CNPJ"].astype(str).str.strip()
                df_raw["Qtde"] = pd.to_numeric(df_raw["Qtde"], errors='coerce').fillna(0).astype(int)
                df_validos = df_raw[df_raw["Qtde"] > 0].copy()
                df_motor = df_validos.groupby(["Curso", "UF", "CNPJ", "Status"], as_index=False)["Qtde"].sum()

                turmas_estado = df_final_trabalho.to_dict('records') if not df_final_trabalho.empty else []
                
                for curso in df_motor["Curso"].unique():
                    dados_curso = df_motor[df_motor["Curso"] == curso]
                    elementos_novos = []
                    for _, row in dados_curso.iterrows():
                        elementos_novos.extend([{
                            "UF": str(row["UF"]), "CNPJ": str(row["CNPJ"]), 
                            "Status": higienizar_status(row.get("Status", "Não Informado"))
                        }] * int(row["Qtde"]))
                    
                    if not elementos_novos: continue
                    
                    turmas_curso_existente = [t for t in turmas_estado if t["Curso"] == curso]
                    for turma in turmas_curso_existente:
                        vagas = max_alunos - int(turma["Alunos"])
                        if vagas > 0 and len(elementos_novos) > 0:
                            alocados = elementos_novos[:vagas]
                            elementos_novos = elementos_novos[vagas:]
                            
                            turma["Alunos"] += len(alocados)
                            turma["CNPJs"] = merge_strings_list(turma["CNPJs"], ", ".join([g["CNPJ"] for g in alocados]))
                            turma["UFs"] = merge_strings_list(turma["UFs"], ", ".join([g["UF"] for g in alocados]))
                            turma["Arquivo"] = merge_strings_list(turma["Arquivo"], arquivo.name)
                            
                            stats_dict = {}
                            for p in str(turma["Status"]).split("|"):
                                if ":" in p:
                                    k, v = p.split(":")
                                    stats_dict[higienizar_status(k)] = int(v)
                            for g in alocados:
                                s = g["Status"]
                                stats_dict[s] = stats_dict.get(s, 0) + 1
                            turma["Status"] = "|".join([f"{k}:{v}" for k, v in stats_dict.items()])

                    if len(elementos_novos) > 0:
                        total_sobra = len(elementos_novos)
                        n_turmas = math.ceil(total_sobra / max_alunos)
                        while total_sobra / n_turmas < min_alunos and n_turmas > 1: n_turmas -= 1
                        
                        tam_base = total_sobra // n_turmas
                        sobra = total_sobra % n_turmas
                        ponteiro = 0
                        n_existentes = len(turmas_curso_existente)
                        
                        nomes_usados = [t["Turma"] for t in turmas_estado if t["Curso"] == curso]
                        
                        for i in range(n_turmas):
                            tam = tam_base + (1 if i < sobra else 0)
                            grupo = elementos_novos[ponteiro:ponteiro+tam]
                            ponteiro += tam
                            
                            cnpjs = sorted(set([str(g["CNPJ"]) for g in grupo]))
                            ufs = sorted(set([str(g["UF"]) for g in grupo]))
                            stats_counts = {}
                            for g in grupo:
                                s = g["Status"]
                                stats_counts[s] = stats_counts.get(s, 0) + 1
                            stats_str_coded = "|".join([f"{k}:{v}" for k, v in stats_counts.items()])
                            
                            novo_id = n_existentes + i + 1
                            nome_original = f"{curso[:3].upper()}-{novo_id:02d}"
                            while nome_original in nomes_usados:
                                novo_id += 1
                                nome_original = f"{curso[:3].upper()}-{novo_id:02d}"
                            nomes_usados.append(nome_original)
                            
                            turmas_estado.append({
                                "id": None, "Curso": curso, "Turma": nome_original, "Alunos": len(grupo),
                                "UFs": ", ".join(ufs), "CNPJs": ", ".join(cnpjs),
                                "Status": stats_str_coded, "Arquivo": arquivo.name
                            })
                
                if supabase:
                    supabase.table("planejamentos_turmas").delete().neq("Curso", "0").execute()
                    for t in turmas_estado:
                        if "id" in t: del t["id"]
                    supabase.table("planejamentos_turmas").insert(turmas_estado).execute()
                    
                st.success(f"Arquivo '{arquivo.name}' preenchido e distribuído com sucesso!")
                st.session_state.dados_salvos = carregar_do_banco()
                if "editor_principal" in st.session_state: del st.session_state["editor_principal"]
                if "last_saved_hash" in st.session_state: del st.session_state["last_saved_hash"]
                st.rerun()
            except Exception as e:
                st.error(f"Erro no processamento da planilha: {e}")

# =========================
# GESTOR DE ARQUIVOS CENTRAL
# =========================
if not df_final_trabalho.empty and "Arquivo" in df_final_trabalho.columns:
    arquivos_salvos = df_final_trabalho["Arquivo"].dropna().unique()
    lista_arqs_unica = set()
    for aq_str in arquivos_salvos:
        for p in str(aq_str).split(","):
            if p.strip() and p.strip() != "nan": lista_arqs_unica.add(p.strip())
            
    if len(lista_arqs_unica) > 0:
        with st.expander("📂 Gerenciar Arquivos Consolidados no Banco", expanded=False):
            st.caption("Atenção: Turmas mistas serão excluídas integralmente se contiverem o arquivo que você apagar.")
            for arq in sorted(lista_arqs_unica):
                c1, c2 = st.columns([8, 1])
                c1.write(f"📄 **{arq}**")
                if c2.button("❌", key=f"del_{arq}", help=f"Remover dados atrelados a {arq}"):
                    if supabase:
                        supabase.table("planejamentos_turmas").delete().ilike("Arquivo", f"%{arq}%").execute()
                        st.session_state.dados_salvos = carregar_do_banco()
                        if "editor_principal" in st.session_state: del st.session_state["editor_principal"]
                        if "last_saved_hash" in st.session_state: del st.session_state["last_saved_hash"]
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
                
                if not st_val or st_val == "nan": st_val = "Não Informado"
                
                if ":" in st_val or "|" in st_val:
                    partes = st_val.split("|")
                    soma_interna = sum(int(p.split(":")[1]) for p in partes if ":" in p)
                    if soma_interna == 0: soma_interna = 1
                    distribuido = 0
                    partes_validas = [p for p in partes if ":" in p]
                    
                    for i, p in enumerate(partes_validas):
                        s_nome, s_qtd = p.split(":")
                        s_nome = higienizar_status(s_nome)
                        s_qtd = int(s_qtd)
                        if i == len(partes_validas) - 1: adjusted_qtd = total_alunos_row - distribuido
                        else:
                            adjusted_qtd = round((s_qtd / soma_interna) * total_alunos_row)
                            distribuido += adjusted_qtd
                        status_totals[s_nome] = status_totals.get(s_nome, 0) + adjusted_qtd
                elif "," in st_val:
                    partes = [higienizar_status(p) for p in st_val.split(",") if p.strip()]
                    if not partes: partes = ["Não Informado"]
                    val_base = total_alunos_row // len(partes)
                    sobra = total_alunos_row % len(partes)
                    for i, p in enumerate(partes):
                        adjusted_qtd = val_base + (1 if i < sobra else 0)
                        status_totals[p] = status_totals.get(p, 0) + adjusted_qtd
                else:
                    st_val = higienizar_status(st_val)
                    status_totals[st_val] = status_totals.get(st_val, 0) + total_alunos_row
            
            for st_nome, count in sorted(status_totals.items()):
                st.write(f"**{st_nome}:** {count} alunos")

    # =========================
    # TABELA COM EDIÇÃO TOTALMENTE LIVRE (MODO DEUS)
    # =========================
    st.divider()
    st.subheader("📚 Ajuste de Planejamento e Cirurgia Manual")
    
    df_final_trabalho = df_final_trabalho.reset_index(drop=True)
    
    if "id" not in df_final_trabalho.columns: df_final_trabalho["id"] = None
    if "Arquivo" not in df_final_trabalho.columns: df_final_trabalho["Arquivo"] = "Desconhecido"
    if "UFs" not in df_final_trabalho.columns: df_final_trabalho["UFs"] = "N/A"

    ordem_cols = ["id", "Turma", "Curso", "Alunos", "CNPJs", "Status", "Arquivo", "UFs"]
    ordem_ok = [c for c in ordem_cols if c in df_final_trabalho.columns]
    
    plano_editado = st.data_editor(
        df_final_trabalho[ordem_ok],
        column_config={
            "id": None, "Status": None, "Arquivo": None, "UFs": None,
            "Turma": st.column_config.TextColumn("Turma (Editável)"),
            "CNPJs": st.column_config.TextColumn("CNPJs (Editável Livre)"),
            "Alunos": st.column_config.NumberColumn("Alunos (Editável Livre)")
        },
        disabled=["Curso"], # <-- Apenas o curso fica travado agora!
        use_container_width=True, hide_index=True, key="editor_principal"
    )

    if supabase and not plano_editado.empty:
        dict_editado = plano_editado.fillna("").astype(str).to_dict("records")
        current_hash = hash(str(dict_editado))
        
        if "last_saved_hash" not in st.session_state:
            dict_base = df_final_trabalho[ordem_ok].fillna("").astype(str).to_dict("records")
            st.session_state.last_saved_hash = hash(str(dict_base))
            
        if current_hash != st.session_state.last_saved_hash:
            st.session_state.last_saved_hash = current_hash
            dados_para_db = []
            for index, row in plano_editado.iterrows():
                original = df_final_trabalho.iloc[index]
                dados_para_db.append({
                    "Curso": str(row.get("Curso", "")), "Turma": str(row.get("Turma", "")), 
                    "Alunos": int(row.get("Alunos", 0)), "UFs": str(original["UFs"]), 
                    "CNPJs": str(row.get("CNPJs", "")), "Status": str(original["Status"]),
                    "Arquivo": str(original["Arquivo"])
                })
            threading.Thread(target=salvar_background, args=(dados_para_db, st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])).start()

    # =========================
    # ASSISTENTE DE REMANEJAMENTO AVANÇADO
    # =========================
    st.divider()
    col_l, col_r = st.columns([1, 1])
    
    with col_l:
        st.subheader("🔍 Localizador de CNPJ")
        busca = st.text_input("Digite o CNPJ para localizar a turma:")
        if busca:
            res = plano_editado[plano_editado["CNPJs"].astype(str).str.contains(busca, na=False)]
            if not res.empty:
                st.success("Localizado!")
                st.dataframe(res[["Curso", "Turma"]], hide_index=True)
            else: st.warning("CNPJ não encontrado.")

    with col_r:
        st.subheader("🔄 Assistente de Remanejamento")
        baixas = plano_editado[plano_editado["Alunos"] < min_alunos]
        
        if not baixas.empty:
            st.warning(f"⚠️ **{len(baixas)} turma(s) cancelada(s) ou abaixo do quórum de {min_alunos}.**")
            
            for idx, turma_baixa in baixas.iterrows():
                curso_b = turma_baixa["Curso"]
                nome_b = turma_baixa["Turma"]
                alunos_b = int(turma_baixa["Alunos"])
                
                candidatas = plano_editado[(plano_editado["Curso"] == curso_b) & (plano_editado["Turma"] != nome_b)]
                
                with st.expander(f"⚙️ Resolver: {nome_b} ({alunos_b} alunos)", expanded=False):
                    if not candidatas.empty:
                        opcao_acao = st.radio("Estratégia de Remanejamento:", 
                                              ["1. Fundir com uma turma específica", "2. Distribuir igualitariamente entre as outras"], 
                                              key=f"rad_{nome_b}")
                        
                        if opcao_acao.startswith("1"):
                            opcoes = [f"{cand['Turma']} (Ficará com {int(cand['Alunos']) + alunos_b} alunos)" for _, cand in candidatas.iterrows()]
                            destino_sel = st.selectbox("Escolha a turma de destino:", opcoes, key=f"sel_{nome_b}")
                            
                            if st.button("Aplicar Fusão Direta", key=f"btn_fusao_{nome_b}", type="primary"):
                                nome_destino = destino_sel.split(" (")[0]
                                fundir_turmas(nome_b, nome_destino, curso_b, st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])
                                st.session_state.dados_salvos = carregar_do_banco()
                                st.rerun()
                                
                        else:
                            qnt_turmas = len(candidatas)
                            st.info(f"Os {alunos_b} alunos serão divididos entre as {qnt_turmas} outras turmas ativas de {curso_b}.")
                            if st.button("Aplicar Distribuição em Lote", key=f"btn_dist_{nome_b}", type="primary"):
                                distribuir_turma(nome_b, curso_b, st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])
                                st.session_state.dados_salvos = carregar_do_banco()
                                st.rerun()
                    else:
                        st.error("Nenhuma outra turma deste curso para receber alunos. Ajuste as informações na tabela ao lado manualmente.")
        else:
            st.success("Todas as turmas estão saudáveis e dentro do quórum!")

    st.divider()
    st.download_button("📥 Baixar Excel Completo", data=gerar_excel_final(plano_editado, df_base_original), file_name="planejamento_senac.xlsx")
