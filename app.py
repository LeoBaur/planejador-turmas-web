import streamlit as st
import pandas as pd
import math
import threading
import re
import time
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

# Função unificada e robusta para descobrir a UF de um CNPJ específico
def descobrir_uf_cnpj(cnpj, row_ufs):
    c_clean = clean_key(cnpj)
    uf_ref = st.session_state.mapa_cnpj_uf.get(c_clean)
    
    if uf_ref and str(uf_ref).strip() not in ["", "nan", "N/A", "None"]:
        return str(uf_ref).split(",")[0].strip()
    
    ufs_turma = [u.strip() for u in str(row_ufs).split(",") if u.strip() and u.strip() != "nan"]
    if len(ufs_turma) == 1:
        return ufs_turma[0]
    
    return "N/I"

# Função NOVA: Formata os CNPJs garantindo que fiquem agrupados/ordenados pela UF
def formatar_cnpjs_agrupados(cnpj_dict, fallback_ufs=""):
    itens = []
    for cnpj, qtd in cnpj_dict.items():
        uf = descobrir_uf_cnpj(cnpj, fallback_ufs)
        itens.append((uf, cnpj, qtd))
    
    # Ordena primeiro pela UF (x[0]), depois pelo CNPJ (x[1])
    itens.sort(key=lambda x: (x[0], x[1]))
    
    res = []
    for uf, cnpj, qtd in itens:
        if qtd > 0:
            res.append(f"{cnpj} ({qtd} - {uf})")
        else:
            res.append(cnpj)
    return ", ".join(res)

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
        # Ignora a UF ao ler os dados antigos, pois ela será gerada dinamicamente e de forma limpa depois
        match = re.match(r"(.+?)\s*\((\d+).*?\)", p)
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
    return formatar_cnpjs_agrupados(d1, "")

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
if 'autenticado' not in st.session_state: 
    st.session_state.autenticado = False

# Verificação de persistência de login (2 horas = 7200 segundos)
if not st.session_state.autenticado and "login_time" in st.query_params:
    try:
        if time.time() - float(st.query_params["login_time"]) < 7200:
            st.session_state.autenticado = True
        else:
            del st.query_params["login_time"]
    except:
        pass

if 'turmas_ignoradas' not in st.session_state: st.session_state.turmas_ignoradas = []
if 'mapa_cnpj_uf' not in st.session_state: st.session_state.mapa_cnpj_uf = {}

with st.sidebar:
    if not st.session_state.autenticado:
        with st.form("login"):
            u, s = st.text_input("Usuário"), st.text_input("Senha", type="password")
            if st.form_submit_button("Entrar") and u == "admin" and s == "senac123":
                st.session_state.autenticado = True
                st.query_params["login_time"] = str(time.time())
                st.rerun()
    else:
        st.success("👤 Logado como Admin")
        if st.button("🚪 Sair"): 
            st.session_state.autenticado = False
            if "login_time" in st.query_params:
                del st.query_params["login_time"]
            st.rerun()
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
    # Popula o mapa de UFs a partir do banco para reconhecer mesmo sem subir arquivo
    if not st.session_state.dados_salvos.empty:
        for _, row_db in st.session_state.dados_salvos.iterrows():
            ufs_banco = [u.strip() for u in str(row_db.get("UFs", "")).split(",") if u.strip() and u.strip() != "nan"]
            cnpjs_banco = parse_cnpjs(str(row_db.get("CNPJs", "")))
            if len(ufs_banco) == 1:
                for c_key in cnpjs_banco.keys():
                    c_clean = str(c_key).strip()
                    if c_clean.endswith(".0"): c_clean = c_clean[:-2]
                    st.session_state.mapa_cnpj_uf[c_clean] = ufs_banco[0]

# =========================
# INTERFACE E PROCESSAMENTO
# =========================
st.title("📊 Planejador Inteligente de Turmas")
df_final_trabalho = st.session_state.dados_salvos.copy()

# Fixando a ordem da planilha para evitar pulos quando a página recarregar
if not df_final_trabalho.empty:
    df_final_trabalho = df_final_trabalho.sort_values(by=["Curso", "Turma"]).reset_index(drop=True)

df_base_original = pd.DataFrame()
arquivo = st.file_uploader("📤 Porta de Entrada", type=["xlsx"])

if arquivo:
    if st.button("🚀 Processar e Salvar", type="primary"):
        try:
            df_raw = pd.read_excel(arquivo, dtype=str) 
            df_base_original = df_raw.copy()
            mapa = {c: "UF" if str(c).upper() in ["UF", "ESTADO"] else "CNPJ" if str(c).upper() in ["CNPJ", "CLIENTE"] else "Qtde" if str(c).upper() in ["QTDE", "QUANTIDADE", "ALUNOS"] else "Status" if str(c).upper() in ["STATUS", "SITUACAO", "SITUAÇÃO"] else "Curso" if str(c).upper() in ["CURSO", "NOME DO CURSO"] else c for c in df_raw.columns}
            df_raw = df_raw.rename(columns=mapa)
            
            if "Qtde" in df_raw.columns:
                df_raw["Qtde"] = pd.to_numeric(df_raw["Qtde"], errors='coerce').fillna(0).astype(int)
            
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
                        
                        # Usando a nova função para alinhar e ordenar por UF
                        t["CNPJs"] = formatar_cnpjs_agrupados(c_dict, t['UFs'])
                        
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
                    
                    uf_agrupada = ",".join(set(g["UF"] for g in aloc))
                    turmas_estado.append({
                        "Curso": curso, "Turma": f"{curso[:3].upper()}-{len([x for x in turmas_estado if x['Curso']==curso])+1:02d}",
                        "Alunos": len(aloc), "UFs": uf_agrupada,
                        # Usando a nova função para alinhar e ordenar por UF
                        "CNPJs": formatar_cnpjs_agrupados(c_dict, uf_agrupada),
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
    # TABELA: VAGAS POR CURSO E UF
    # =========================
    with st.expander("🗺️ Distribuição de Vagas por Curso e Estado (UF)", expanded=False):
        vagas_curso_uf = []
        for _, row in df_final_trabalho.iterrows():
            curso_atual = row["Curso"]
            row_ufs = str(row.get("UFs", ""))
            cnpjs_dict = parse_cnpjs(str(row["CNPJs"]))
            
            for cnpj, qtd in cnpjs_dict.items():
                uf_atual = descobrir_uf_cnpj(cnpj, row_ufs)
                vagas_curso_uf.append({"Curso": curso_atual, "UF": uf_atual, "Vagas": qtd})

        if vagas_curso_uf:
            df_vagas = pd.DataFrame(vagas_curso_uf)
            df_pivot = df_vagas.groupby(["UF", "Curso"])["Vagas"].sum().unstack(fill_value=0)
            
            st.write("**Quantidade de Vagas Solicitadas:**")
            st.dataframe(df_pivot, use_container_width=True)
        else:
            st.info("Nenhuma vaga mapeada encontrada.")

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
            linha_original = df_final_trabalho[(df_final_trabalho["Curso"] == row["Curso"]) & (df_final_trabalho["Turma"] == row["Turma"])]
            
            if not linha_original.empty:
                orig = linha_original.iloc[0]
            else:
                orig = {"Status": "Aguardando Atendimento:0", "Arquivo": "", "UFs": row.get("UFs", "")}
            
            dados_cnpj = parse_cnpjs(str(row["CNPJs"]))
            novo_total_alunos = sum(dados_cnpj.values())
            
            novas_ufs_detectadas = []
            for c in dados_cnpj.keys():
                c_clean = str(c).strip()
                if c_clean.endswith(".0"): c_clean = c_clean[:-2]
                uf_ref = st.session_state.mapa_cnpj_uf.get(c_clean)
                if uf_ref:
                    for u in str(uf_ref).split(","):
                        if u.strip() and u.strip() != "nan":
                            novas_ufs_detectadas.append(u.strip())
            
            nova_uf_str = ", ".join(sorted(set(novas_ufs_detectadas))) if novas_ufs_detectadas else orig["UFs"]
            
            # Chama a função inteligente de formatação antes de salvar no banco
            cnpjs_final_str = formatar_cnpjs_agrupados(dados_cnpj, nova_uf_str)
            
            db_data.append({
                "Curso": row["Curso"], "Turma": row["Turma"], 
                "Alunos": int(novo_total_alunos),
                "UFs": nova_uf_str, 
                "CNPJs": cnpjs_final_str, 
                "Status": orig["Status"], "Arquivo": orig["Arquivo"]
            })
            
        salvar_background(db_data, st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])
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
            if not res.empty:
                st.dataframe(res[["Curso", "Turma", "UFs"]], hide_index=True)
            else:
                st.warning("Não encontrado")
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
                        if st.button("Confirmar Fusão", key=f"btn_f_{t_b['Turma']}"):
                            fundir_turmas(t_b["Turma"], dest, t_b["Curso"], st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])
                            st.session_state.dados_salvos = carregar_do_banco(); st.rerun()
                    elif acao == "Distribuir":
                        if st.button("Confirmar Distribuição", key=f"btn_d_{t_b['Turma']}"):
                            distribuir_turma(t_b["Turma"], t_b["Curso"], st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])
                            st.session_state.dados_salvos = carregar_do_banco(); st.rerun()
                    else:
                        if st.button("Ocultar Alerta", key=f"btn_i_{t_b['Turma']}"):
                            st.session_state.turmas_ignoradas.append(t_b["Turma"]); st.rerun()
        else: st.success("Tudo em conformidade!")

    # =========================
    # RELATÓRIO: AGUARDANDO ATENDIMENTO
    # =========================
    st.divider()
    with st.expander("📄 Relatório de CNPJs (Aguardando atendimento)", expanded=True):
        st.write("CNPJs e quantidades de alunos com status 'Aguardando Atendimento', detalhados por UF e Curso.")

        if not df_final_trabalho.empty:
            status_alvo = "Aguardando Atendimento"
            lista_pendencias = []

            for index, row in df_final_trabalho.iterrows():
                status_raw, qtd_aguardando_linha = str(row.get("Status", "")), 0
                for p in status_raw.split("|"):
                    if ":" in p:
                        label, valor = p.split(":")
                        if higienizar_status(label) == status_alvo:
                            qtd_aguardando_linha = int(valor)

                if qtd_aguardando_linha > 0:
                    cnpjs_na_linha = parse_cnpjs(row.get("CNPJs", ""))
                    total_alunos_linha = sum(cnpjs_na_linha.values())
                    fator = qtd_aguardando_linha / total_alunos_linha if total_alunos_linha > 0 else 0
                    
                    curso_linha = str(row.get("Curso", "Não Informado"))
                    row_ufs = str(row.get("UFs", ""))

                    for cnpj, qtd_cnpj in cnpjs_na_linha.items():
                        pendencia_calculada = round(qtd_cnpj * fator)
                        if pendencia_calculada > 0:
                            uf_real = descobrir_uf_cnpj(cnpj, row_ufs)

                            lista_pendencias.append({
                                "Curso": curso_linha,
                                "UF": uf_real, 
                                "CNPJ": cnpj, 
                                "Qtd": pendencia_calculada
                            })

            if lista_pendencias:
                df_detalhe = pd.DataFrame(lista_pendencias).groupby(["Curso", "UF", "CNPJ"], as_index=False)["Qtd"].sum()
                df_total_uf = df_detalhe.groupby("UF")["Qtd"].sum().reset_index()
                df_total_uf.columns = ["UF", "Total Aguardando"]

                st.write("**Total de Vagas por UF:**")
                for _, row_uf in df_total_uf.iterrows():
                    st.write(f"📍 **{row_uf['UF']}:** {int(row_uf['Total Aguardando'])} vagas")
                
                st.divider()
                st.write("**Detalhamento por Cliente e Curso:**")
                st.dataframe(df_detalhe.sort_values(by=["Curso", "UF", "Qtd"], ascending=[True, True, False]), 
                             use_container_width=True, hide_index=True)

                def gerar_excel_pendencias_completo(df_d, df_t):
                    output = BytesIO()
                    with pd.ExcelWriter(output, engine="openpyxl") as writer:
                        df_t.to_excel(writer, index=False, sheet_name="Resumo_por_UF")
                        df_d.to_excel(writer, index=False, sheet_name="Detalhe_por_CNPJ")
                    return output.getvalue()

                st.download_button(
                    label="📥 Baixar Relatório Completo (Resumo + Detalhe)",
                    data=gerar_excel_pendencias_completo(df_detalhe, df_total_uf),
                    file_name="relatorio_aguardando_atendimento.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
            else:
                st.info("Nenhuma pendência encontrada.")
