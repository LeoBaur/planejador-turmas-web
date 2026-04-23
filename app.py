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
        "Status": ["Aguardando Atendimento", "Pré-Matrícula"]
    })
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        modelo_df.to_excel(writer, index=False, sheet_name="Modelo")
    return output.getvalue()

def gerar_excel_final(plano_df, original_df):
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df_export = plano_df.copy()
        if "id" in df_export.columns:
            df_export = df_export.drop(columns=["id"])
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

# Função para garantir que os CNPJs sejam comparados de forma exata (sem .0 ou espaços)
def clean_key(k):
    s = str(k).strip().upper()
    if s.endswith(".0"): 
        s = s[:-2]
    return s

# =========================
# LÓGICA DE STRINGS E PARSERS
# =========================
def parse_cnpjs(cnpj_str):
    res = {}
    if pd.isna(cnpj_str) or str(cnpj_str).strip() in ["", "nan"]:
        return res
    for p in str(cnpj_str).split(","):
        p = p.strip()
        if not p: continue
        match = re.match(r"(.+?)\s*\((\d+)\)", p)
        if match:
            c, q = match.group(1).strip(), int(match.group(2))
            res[c] = res.get(c, 0) + q
        else:
            res[p] = res.get(p, 0) + 0 
    return res

def merge_cnpjs_str(s1, s2):
    d1 = parse_cnpjs(s1)
    for k, v in parse_cnpjs(s2).items():
        d1[k] = d1.get(k, 0) + v
    return ", ".join([f"{k} ({v})" if v > 0 else k for k, v in sorted(d1.items())])

def merge_strings_list(s1, s2):
    l1 = [x.strip() for x in str(s1).split(",") if x.strip() and x.strip() != "nan"]
    l2 = [x.strip() for x in str(s2).split(",") if x.strip() and x.strip() != "nan"]
    return ", ".join(sorted(set(l1 + l2)))

# =========================
# LÓGICA DE FUSÃO E DISTRIBUIÇÃO
# =========================
def fundir_turmas(nome_origem, nome_destino, curso, url, key):
    client = create_client(url, key)
    res = client.table("planejamentos_turmas").select("*").eq("Curso", curso).in_("Turma", [nome_origem, nome_destino]).execute()
    df_db = pd.DataFrame(res.data)
    if len(df_db) == 2:
        origem = df_db[df_db["Turma"] == nome_origem].iloc[0]
        destino = df_db[df_db["Turma"] == nome_destino].iloc[0]
        
        novos_alunos = int(destino["Alunos"]) + int(origem["Alunos"])
        novas_ufs = merge_strings_list(origem["UFs"], destino["UFs"])
        novos_arqs = merge_strings_list(origem["Arquivo"], destino["Arquivo"])
        novos_cnpjs = merge_cnpjs_str(origem["CNPJs"], destino["CNPJs"])
        
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
    origem = df_db[df_db["Turma"] == nome_origem].iloc[0]
    destinos = df_db[df_db["Turma"] != nome_origem]
    if destinos.empty: return

    alunos_total = int(origem["Alunos"])
    adds = [alunos_total // len(destinos)] * len(destinos)
    for i in range(alunos_total % len(destinos)): adds[i] += 1
    
    for i, (_, dest) in enumerate(destinos.iterrows()):
        client.table("planejamentos_turmas").update({
            "Alunos": int(dest["Alunos"]) + adds[i],
            "UFs": merge_strings_list(dest["UFs"], origem["UFs"]),
            "CNPJs": merge_cnpjs_str(dest["CNPJs"], origem["CNPJs"]),
            "Status": str(dest["Status"]) + "|" + str(origem["Status"])
        }).eq("id", dest["id"]).execute()
    client.table("planejamentos_turmas").delete().eq("id", origem["id"]).execute()

# =========================
# LOGIN E BANCO DE DADOS
# =========================
if 'autenticado' not in st.session_state: st.session_state.autenticado = False
if 'turmas_ignoradas' not in st.session_state: st.session_state.turmas_ignoradas = []
if 'mapa_cnpj_uf' not in st.session_state: st.session_state.mapa_cnpj_uf = {}

with st.sidebar:
    if not st.session_state.autenticado:
        with st.form("login"):
            u, s = st.text_input("Usuário"), st.text_input("Senha", type="password")
            if st.form_submit_button("Entrar") and u == "admin" and s == "senac123":
                st.session_state.autenticado = True
                st.rerun()
    else:
        st.success("👤 Logado como Admin")
        if st.button("🚪 Sair"): st.session_state.autenticado = False; st.rerun()
        st.divider()
        min_alunos = st.number_input("Mínimo", min_value=1, value=25)
        max_alunos = st.number_input("Máximo", min_value=1, value=45)
        if st.button("🚨 Resetar Banco"):
            create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"]).table("planejamentos_turmas").delete().neq("Curso", "0").execute()
            st.session_state.mapa_cnpj_uf = {}
            st.session_state.dados_salvos = pd.DataFrame(); st.rerun()
        st.divider()
        st.download_button("📥 Baixar Modelo", data=gerar_modelo_excel(), file_name="modelo_senac.xlsx", use_container_width=True)

if not st.session_state.autenticado: st.stop()

@st.cache_resource
def init_connection():
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])

def carregar_do_banco():
    try:
        supabase = init_connection()
        res = supabase.table("planejamentos_turmas").select("*").execute()
        return pd.DataFrame(res.data)
    except: return pd.DataFrame()

if "dados_salvos" not in st.session_state:
    st.session_state.dados_salvos = carregar_do_banco()

# =========================
# HEURÍSTICA DE APRENDIZADO DE UFs
# =========================
df_final_trabalho = st.session_state.dados_salvos.copy()

# Varre os dados já salvos: se uma turma possui apenas UMA UF,
# sabemos que todos os CNPJs dela são daquela UF. Isso reconstrói o mapa de forma autônoma.
if not df_final_trabalho.empty:
    for _, row_db in df_final_trabalho.iterrows():
        ufs_banco = [u.strip() for u in str(row_db.get("UFs", "")).split(",") if u.strip() and u.strip() != "nan"]
        cnpjs_banco = parse_cnpjs(str(row_db.get("CNPJs", "")))
        
        if len(ufs_banco) == 1:
            for c_key in cnpjs_banco.keys():
                st.session_state.mapa_cnpj_uf[clean_key(c_key)] = ufs_banco[0]

# =========================
# INTERFACE E PROCESSAMENTO
# =========================
st.title("📊 Planejador Inteligente de Turmas")
df_base_original = pd.DataFrame()
arquivo = st.file_uploader("📤 Porta de Entrada", type=["xlsx"])

if arquivo:
    if st.button("🚀 Processar e Salvar", type="primary"):
        try:
            # Lê forçando string para evitar que CNPJs numéricos virem "1.11e13" ou adicionem ".0"
            df_raw = pd.read_excel(arquivo, dtype=str) 
            df_base_original = df_raw.copy()
            mapa = {c: "UF" if str(c).upper() in ["UF", "ESTADO"] else "CNPJ" if str(c).upper() in ["CNPJ", "CLIENTE"] else "Qtde" if str(c).upper() in ["QTDE", "QUANTIDADE", "ALUNOS"] else "Status" if str(c).upper() in ["STATUS", "SITUACAO", "SITUAÇÃO"] else "Curso" if str(c).upper() in ["CURSO", "NOME DO CURSO"] else c for c in df_raw.columns}
            df_raw = df_raw.rename(columns=mapa)
            
            # Como forçamos dtype=str, precisamos garantir que 'Qtde' volte a ser número para a matemática não falhar
            if "Qtde" in df_raw.columns:
                df_raw["Qtde"] = pd.to_numeric(df_raw["Qtde"], errors='coerce').fillna(0).astype(int)
            
            # Alimentar o mapa de referência de forma cravada
            for _, r in df_raw.iterrows():
                c_limpo = clean_key(r.get("CNPJ", ""))
                if c_limpo and c_limpo != "NAN": 
                    st.session_state.mapa_cnpj_uf[c_limpo] = str(r.get("UF", "")).strip()

            df_motor = df_raw.groupby(["Curso", "UF", "CNPJ", "Status"], as_index=False)["Qtde"].sum()
            turmas_estado = df_final_trabalho.to_dict('records') if not df_final_trabalho.empty else []
            
            for curso in df_motor["Curso"].unique():
                dados_curso = df_motor[df_motor["Curso"] == curso]
                elementos = []
                for _, r in dados_curso.iterrows():
                    elementos.extend([{"UF": str(r["UF"]), "CNPJ": str(r["CNPJ"]), "Status": higienizar_status(r["Status"])}] * int(r["Qtde"]))
                
                for t in [x for x in turmas_estado if x["Curso"] == curso]:
                    vagas = max_alunos - int(t["Alunos"])
                    if vagas > 0 and elementos:
                        aloc = elementos[:vagas]; elementos = elementos[vagas:]
                        t["Alunos"] += len(aloc)
                        t["UFs"] = merge_strings_list(t["UFs"], ",".join([g["UF"] for g in aloc]))
                        t["Arquivo"] = merge_strings_list(t.get("Arquivo", ""), arquivo.name)
                        c_dict = parse_cnpjs(t["CNPJs"])
                        for g in aloc: c_dict[g["CNPJ"]] = c_dict.get(g["CNPJ"], 0) + 1
                        t["CNPJs"] = ", ".join([f"{k} ({v})" for k, v in sorted(c_dict.items())])
                        s_dict = {}
                        for p in str(t["Status"]).split("|"):
                            if ":" in p: k, v = p.split(":"); s_dict[higienizar_status(k)] = int(v)
                        for g in aloc: s_dict[g["Status"]] = s_dict.get(g["Status"], 0) + 1
                        t["Status"] = "|".join([f"{k}:{v}" for k, v in s_dict.items()])

                while elementos:
                    tam = min(len(elementos), max_alunos)
                    aloc = elementos[:tam]; elementos = elementos[tam:]
                    c_dict = {}; s_dict = {}
                    for g in aloc: 
                        c_dict[g["CNPJ"]] = c_dict.get(g["CNPJ"], 0) + 1
                        s_dict[g["Status"]] = s_dict.get(g["Status"], 0) + 1
                    turmas_estado.append({
                        "Curso": curso, "Turma": f"{curso[:3].upper()}-{len([x for x in turmas_estado if x['Curso']==curso])+1:02d}",
                        "Alunos": len(aloc), "UFs": ",".join(set(g["UF"] for g in aloc)),
                        "CNPJs": ", ".join([f"{k} ({v})" for k, v in sorted(c_dict.items())]),
                        "Status": "|".join([f"{k}:{v}" for k, v in s_dict.items()]), "Arquivo": arquivo.name
                    })

            init_connection().table("planejamentos_turmas").delete().neq("Curso", "0").execute()
            for t in turmas_estado: 
                if "id" in t: del t["id"]
            init_connection().table("planejamentos_turmas").insert(turmas_estado).execute()
            st.session_state.dados_salvos = carregar_do_banco(); st.rerun()
        except Exception as e: st.error(f"Erro: {e}")

# =========================
# GESTOR DE ARQUIVOS
# =========================
if not df_final_trabalho.empty:
    with st.expander("📂 Gerenciar Arquivos Consolidados"):
        arqs = set()
        for v in df_final_trabalho["Arquivo"].dropna():
            for p in str(v).split(","):
                if p.strip() and p.strip() != "nan": arqs.add(p.strip())
        for a in sorted(arqs):
            c1, c2 = st.columns([8, 1])
            c1.write(f"📄 {a}")
            if c2.button("❌", key=f"del_{a}"):
                init_connection().table("planejamentos_turmas").delete().ilike("Arquivo", f"%{a}%").execute()
                st.session_state.dados_salvos = carregar_do_banco(); st.rerun()

# =========================
# PAINEL DE INDICADORES (KPIs)
# =========================
if not df_final_trabalho.empty:
    st.divider()
    st.subheader("🏁 Painel de Controle - Visão Geral")
    st.metric("Total Geral de Alunos", df_final_trabalho["Alunos"].sum())
    c1, c2 = st.columns(2)
    with c1:
        with st.expander("🎓 Resumo por Curso (Turmas e Alunos)", expanded=True):
            resumo = df_final_trabalho.groupby("Curso").agg(Alunos=('Alunos', 'sum'), Turmas=('Turma', 'count')).reset_index()
            st.write(f"**Total Geral:** {resumo['Turmas'].sum()} turmas")
            st.dataframe(resumo, hide_index=True, use_container_width=True)
    with c2:
        with st.expander("📋 Alunos por Tipo de Solicitação", expanded=True):
            status_totals = {}
            for _, row in df_final_trabalho.iterrows():
                for p in str(row["Status"]).split("|"):
                    if ":" in p:
                        k, v = p.split(":"); k = higienizar_status(k)
                        status_totals[k] = status_totals.get(k, 0) + int(v)
            for k, v in sorted(status_totals.items()): st.write(f"**{k}:** {v} alunos")

    # =========================
    # TABELA PRINCIPAL E DOWNLOAD IMEDIATO
    # =========================
    st.divider()
    st.subheader("📚 Planejamento e Logística de Turmas")
    colunas_ok = ["Curso", "Turma", "Alunos", "UFs", "CNPJs"]
    plano_editado = st.data_editor(
        df_final_trabalho[colunas_ok],
        column_config={
            "Curso": st.column_config.TextColumn("Curso", disabled=True),
            "Alunos": st.column_config.NumberColumn("Alunos", disabled=True),
            "CNPJs": st.column_config.TextColumn("CNPJs", width=1000),
            "UFs": st.column_config.TextColumn("Estados (UFs)", width="medium", disabled=True)
        },
        use_container_width=True, hide_index=True, key="editor_principal"
    )

    st.download_button("📥 Baixar Planilha Principal (Excel Completo)", 
                       data=gerar_excel_final(plano_editado, df_base_original), 
                       file_name="planejamento_senac.xlsx")

    # =========================
    # SALVAMENTO AUTOMÁTICO E RECALCULO UFs/ALUNOS
    # =========================
    dict_editado = plano_editado.to_dict("records")
    current_hash = hash(str(dict_editado))
    
    if "last_saved_hash" not in st.session_state: 
        st.session_state.last_saved_hash = current_hash
        
    if current_hash != st.session_state.last_saved_hash:
        st.session_state.last_saved_hash = current_hash
        db_data = []
        for i, row in plano_editado.iterrows():
            orig = df_final_trabalho.iloc[i]
            
            dados_cnpj = parse_cnpjs(str(row["CNPJs"]))
            novo_total_alunos = sum(dados_cnpj.values())
            
            novas_ufs_detectadas = []
            for c in dados_cnpj.keys():
                uf_ref = st.session_state.mapa_cnpj_uf.get(clean_key(c))
                if uf_ref:
                    # Garantindo que se vier "PR, SP" seja tratado de forma limpa
                    for u in str(uf_ref).split(","):
                        if u.strip() and u.strip() != "nan":
                            novas_ufs_detectadas.append(u.strip())
            
            nova_uf_str = ", ".join(sorted(set(novas_ufs_detectadas))) if novas_ufs_detectadas else orig["UFs"]
            
            db_data.append({
                "Curso": row["Curso"], "Turma": row["Turma"], 
                "Alunos": int(novo_total_alunos),
                "UFs": nova_uf_str, 
                "CNPJs": row["CNPJs"], 
                "Status": orig["Status"], "Arquivo": orig["Arquivo"]
            })
            
        threading.Thread(target=salvar_background, args=(db_data, st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])).start()
        st.session_state.dados_salvos = pd.DataFrame(db_data)
        st.rerun()

    # =========================
    # ASSISTENTE E LOCALIZADOR
    # =========================
    st.divider()
    col_l, col_r = st.columns(2)
    with col_l:
        st.subheader("🔍 Localizador de CNPJ")
        busca = st.text_input("CNPJ:")
        if busca:
            res = plano_editado[plano_editado["CNPJs"].astype(str).str.contains(busca)]
            st.dataframe(res[["Curso", "Turma", "UFs"]], hide_index=True) if not res.empty else st.warning("Não encontrado")
    with col_r:
        st.subheader("🔄 Assistente de Remanejamento")
        baixas = plano_editado[plano_editado["Alunos"] < min_alunos]
        baixas = baixas[~baixas["Turma"].isin(st.session_state.turmas_ignoradas)]
        if not baixas.empty:
            for _, t_b in baixas.iterrows():
                with st.expander(f"Resolver: {t_b['Turma']}"):
                    acao = st.radio("Ação:", ["Fundir", "Distribuir", "Ignorar"], key=f"ac_{t_b['Turma']}")
                    if acao == "Fundir":
                        cands = plano_editado[(plano_editado["Curso"] == t_b["Curso"]) & (plano_editado["Turma"] != t_b["Turma"])]
                        dest = st.selectbox("Destino:", cands["Turma"], key=f"dest_{t_b['Turma']}")
                        if st.button("Confirmar Fusão", key=f"btn_
