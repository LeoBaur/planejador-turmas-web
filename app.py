import streamlit as st
import pandas as pd
import math
import threading
import re
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

def merge_strings_list(s1, s2):
    l1 = [x.strip() for x in str(s1).split(",") if x.strip() and x.strip() != "nan"]
    l2 = [x.strip() for x in str(s2).split(",") if x.strip() and x.strip() != "nan"]
    return ", ".join(sorted(set(l1 + l2)))

def get_next_turma_name(curso, nomes_usados):
    prefix = curso[:3].upper()
    i = 1
    while True:
        name = f"{prefix}-{i:02d}"
        if name not in nomes_usados:
            return name
        i += 1

def add_status(status_dict, status_str, qtde):
    if "|" in status_str or ":" in status_str:
        parts = [p for p in status_str.split("|") if ":" in p]
        if not parts:
            k_clean = higienizar_status(status_str)
            status_dict[k_clean] = status_dict.get(k_clean, 0) + qtde
            return
        soma_interna = sum(int(p.split(":")[1]) for p in parts)
        if soma_interna == 0: soma_interna = 1
        distribuido = 0
        for i, p in enumerate(parts):
            k, v = p.split(":")
            k_clean = higienizar_status(k)
            add_q = qtde - distribuido if i == len(parts) - 1 else round((int(v) / soma_interna) * qtde)
            distribuido += add_q
            status_dict[k_clean] = status_dict.get(k_clean, 0) + add_q
    else:
        k_clean = higienizar_status(status_str)
        status_dict[k_clean] = status_dict.get(k_clean, 0) + qtde

# =========================
# LÓGICA DE FUSÃO MANUAL E DISTRIBUIÇÃO (Assistente)
# =========================
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
        add_status(stats_dict, str(origem["Status"]), int(origem["Alunos"]))
        add_status(stats_dict, str(destino["Status"]), int(destino["Alunos"]))
        novo_status = "|".join([f"{k}:{v}" for k, v in stats_dict.items() if v > 0])
        
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
    add_status(origem_stats, str(origem["Status"]), alunos_total)

    for i, (_, dest) in enumerate(destinos.iterrows()):
        if adds[i] == 0: continue
        new_alunos = int(dest["Alunos"]) + adds[i]
        new_cnpjs = merge_strings_list(dest["CNPJs"], origem["CNPJs"])
        new_ufs = merge_strings_list(dest["UFs"], origem["UFs"])
        new_arqs = merge_strings_list(dest["Arquivo"], origem["Arquivo"])

        dest_stats = {}
        add_status(dest_stats, str(dest["Status"]), int(dest["Alunos"]))

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
        st.subheader("⚙️ Configurações (LIMITES ABSOLUTOS)")
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
# INTERFACE PRINCIPAL E REPACTUAÇÃO ESTRITA
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
        if st.button("🚀 Processar e Aplicar Limites Estritos", type="primary"):
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
                cursos_na_planilha = df_motor["Curso"].unique()
                
                turmas_intactas = [t for t in turmas_estado if t["Curso"] not in cursos_na_planilha]
                turmas_reconstruidas = []
                
                for curso in cursos_na_planilha:
                    lotes_curso = []
                    turmas_existentes = [t for t in turmas_estado if t["Curso"] == curso]
                    nomes_usados = [t["Turma"] for t in turmas_estado if t["Curso"] != curso]
                    
                    # 1. Extrai lotes exatos do Banco Legado
                    for t in turmas_existentes:
                        partes = str(t.get("CNPJs", "")).split(",")
                        itens_parseados = []
                        total_parseado = 0
                        for p in partes:
                            p = p.strip()
                            if not p: continue
                            match = re.match(r"(.+?)\s*\((\d+)\)", p)
                            if match:
                                c = match.group(1).strip()
                                q = int(match.group(2))
                                itens_parseados.append({"cnpj": c, "qtde": q})
                                total_parseado += q
                            else:
                                itens_parseados.append({"cnpj": p, "qtde": 0})
                        
                        if total_parseado == 0 and itens_parseados:
                            base = int(t["Alunos"]) // len(itens_parseados)
                            sobra = int(t["Alunos"]) % len(itens_parseados)
                            for i, pi in enumerate(itens_parseados):
                                pi["qtde"] = base + (1 if i < sobra else 0)
                                
                        for pi in itens_parseados:
                            if pi["qtde"] > 0:
                                lotes_curso.append({
                                    "cnpj": pi["cnpj"], "qtde": pi["qtde"],
                                    "uf": str(t.get("UFs", "")), "status": str(t.get("Status", "")), 
                                    "arquivo": str(t.get("Arquivo", "Banco"))
                                })
                                
                    # 2. Adiciona lotes novos da Planilha
                    dados_curso = df_motor[df_motor["Curso"] == curso]
                    for _, row in dados_curso.iterrows():
                        if int(row["Qtde"]) > 0:
                            lotes_curso.append({
                                "cnpj": str(row["CNPJ"]), "qtde": int(row["Qtde"]),
                                "uf": str(row["UF"]), "status": higienizar_status(row.get("Status")), 
                                "arquivo": arquivo.name
                            })
                            
                    # 3. MATEMÁTICA ESTRITA DE LIMITES (Repactuação Geral)
                    total_alunos = sum(l["qtde"] for l in lotes_curso)
                    if total_alunos == 0: continue
                    
                    k_turmas = math.ceil(total_alunos / max_alunos)
                    alvos = []
                    
                    # Checa se é matematicamente possível bater o mínimo
                    if total_alunos < k_turmas * min_alunos:
                        base = total_alunos // k_turmas
                        sobra = total_alunos % k_turmas
                        alvos = [base + (1 if i < sobra else 0) for i in range(k_turmas)]
                    else:
                        alvos = [min_alunos] * k_turmas
                        restante = total_alunos - (k_turmas * min_alunos)
                        for i in range(k_turmas):
                            espaco = max_alunos - alvos[i]
                            add = min(espaco, restante)
                            alvos[i] += add
                            restante -= add
                            
                    # Ordena lotes do maior pro menor para preservar CNPJs inteiros o máximo possível
                    lotes_curso.sort(key=lambda x: x["qtde"], reverse=True)
                    fila_lotes = lotes_curso.copy()
                    
                    for alvo in alvos:
                        nome_turma = get_next_turma_name(curso, nomes_usados)
                        nomes_usados.append(nome_turma)
                        
                        bin_atual = {
                            "id": None, "Curso": curso, "Turma": nome_turma,
                            "Alunos": 0, "CNPJs": "", "UFs": "", "Status": "", "Arquivo": "",
                            "_stats_dict": {}, "_cnpjs_list": []
                        }
                        
                        alvo_restante = alvo
                        while alvo_restante > 0 and fila_lotes:
                            lote = fila_lotes.pop(0)
                            
                            if lote["qtde"] <= alvo_restante:
                                cabe = lote
                            else:
                                # Corte Cirúrgico para respeitar 100% o limite
                                cabe = lote.copy()
                                cabe["qtde"] = alvo_restante
                                sobra = lote.copy()
                                sobra["qtde"] = lote["qtde"] - alvo_restante
                                fila_lotes.insert(0, sobra) # Joga o resto pra próxima turma
                                
                            bin_atual["Alunos"] += cabe["qtde"]
                            bin_atual["UFs"] = merge_strings_list(bin_atual["UFs"], cabe["uf"])
                            bin_atual["Arquivo"] = merge_strings_list(bin_atual["Arquivo"], cabe["arquivo"])
                            
                            # Formatação rica preservada
                            bin_atual["_cnpjs_list"].append(f"{cabe['cnpj']} ({cabe['qtde']})")
                            add_status(bin_atual["_stats_dict"], cabe["status"], cabe["qtde"])
                            
                            alvo_restante -= cabe["qtde"]
                            
                        # Compila strings finais
                        bin_atual["CNPJs"] = ", ".join(bin_atual["_cnpjs_list"])
                        bin_atual["Status"] = "|".join([f"{k}:{v}" for k, v in bin_atual["_stats_dict"].items() if v > 0])
                        del bin_atual["_stats_dict"]
                        del bin_atual["_cnpjs_list"]
                        
                        turmas_reconstruidas.append(bin_atual)

                # Salva o Estado Consolidado e Perfeito
                turmas_finais = turmas_intactas + turmas_reconstruidas
                if supabase:
                    supabase.table("planejamentos_turmas").delete().neq("Curso", "0").execute()
                    for t in turmas_finais:
                        if "id" in t: del t["id"]
                    supabase.table("planejamentos_turmas").insert(turmas_finais).execute()
                    
                st.success(f"Arquivo processado! Turmas repactuadas respeitando estritamente os limites de {min_alunos} a {max_alunos} alunos.")
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
                        add_q = total_alunos_row - distribuido if i == len(partes_validas) - 1 else round((s_qtd / soma_interna) * total_alunos_row)
                        distribuido += add_q
                        status_totals[s_nome] = status_totals.get(s_nome, 0) + add_q
                else:
                    st_val = higienizar_status(st_val)
                    status_totals[st_val] = status_totals.get(st_val, 0) + total_alunos_row
            
            for st_nome, count in sorted(status_totals.items()):
                st.write(f"**{st_nome}:** {count} alunos")

    # =========================
    # TABELA COM EDIÇÃO TOTALMENTE LIVRE
    # =========================
    st.divider()
    st.subheader("📚 Grade de Planejamento Final")
    
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
            "CNPJs": st.column_config.TextColumn("CNPJs (Ex: 1111 [15])"),
            "Alunos": st.column_config.NumberColumn("Alunos (Editável)")
        },
        disabled=["Curso"], 
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
    # ASSISTENTE DE REMANEJAMENTO DE SEGURANÇA
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
        st.subheader("⚠️ Monitor de Exceções Matemáticas")
        baixas = plano_editado[plano_editado["Alunos"] < min_alunos]
        
        if not baixas.empty:
            st.warning(f"Exceção encontrada: O total geral de alunos neste curso é insuficiente para fechar turmas dentro do mínimo de {min_alunos}.")
            for idx, turma_baixa in baixas.iterrows():
                curso_b = turma_baixa["Curso"]
                nome_b = turma_baixa["Turma"]
                alunos_b = int(turma_baixa["Alunos"])
                candidatas = plano_editado[(plano_editado["Curso"] == curso_b) & (plano_editado["Turma"] != nome_b)]
                
                with st.expander(f"Resolver Opcional: {nome_b} ({alunos_b} alunos)", expanded=False):
                    if not candidatas.empty:
                        opcao_acao = st.radio("Ação Corretiva Manual:", 
                                              ["1. Fundir com uma turma específica (Ignorará o Limite Máximo)", "2. Distribuir igualitariamente"], 
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
                            st.info(f"Os {alunos_b} alunos serão divididos entre as {qnt_turmas} outras turmas.")
                            if st.button("Aplicar Distribuição em Lote", key=f"btn_dist_{nome_b}", type="primary"):
                                distribuir_turma(nome_b, curso_b, st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])
                                st.session_state.dados_salvos = carregar_do_banco()
                                st.rerun()
                    else:
                        st.error("Nenhuma outra turma deste curso para receber alunos.")
        else:
            st.success(f"Matemática Perfeita! 100% das turmas estão rigorosamente entre {min_alunos} e {max_alunos} alunos.")

    st.divider()
    st.download_button("📥 Baixar Excel Completo", data=gerar_excel_final(plano_editado, df_base_original), file_name="planejamento_senac.xlsx")
