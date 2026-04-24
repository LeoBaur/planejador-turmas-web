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

def clean_key(k):
    s = str(k).strip().upper()
    if s.endswith(".0"): 
        s = s[:-2]
    return s

def obter_uf_cnpj_seguro(cnpj, string_original="", fallback_ufs=""):
    c_clean = clean_key(cnpj)
    uf_ref = st.session_state.mapa_cnpj_uf.get(c_clean)
    
    if uf_ref and str(uf_ref).strip() not in ["", "nan", "None", "N/A"]:
        return str(uf_ref).split(",")[0].strip()
    
    if string_original:
        match = re.search(re.escape(cnpj) + r"\s*\(\s*\d+\s*-\s*([A-Za-z]{2})\s*\)", string_original)
        if match:
            return match.group(1).upper()
            
    ufs_turma = [u.strip() for u in str(fallback_ufs).split(",") if u.strip() and u.strip() not in ["nan", "N/I"]]
    if len(ufs_turma) == 1:
        return ufs_turma[0]
        
    return "N/I"

def formatar_cnpjs_agrupados(cnpj_dict, string_original="", fallback_ufs=""):
    itens = []
    for cnpj, qtd in cnpj_dict.items():
        uf = obter_uf_cnpj_seguro(cnpj, string_original, fallback_ufs)
        itens.append((uf, cnpj, qtd))
    
    itens.sort(key=lambda x: (x[0], x[1]))
    
    res = []
    for uf, cnpj, qtd in itens:
        if qtd > 0:
            res.append(f"{cnpj} ({qtd} - {uf})")
        else:
            res.append(cnpj)
    return ", ".join(res)

# =========================
# LÓGICA DE STRINGS E PARSERS BLINDADA
# =========================
def parse_cnpjs(cnpj_str):
    res = {}
    if pd.isna(cnpj_str) or str(cnpj_str).strip() in ["", "nan", "None"]:
        return res
    
    # Removemos quebras de linha que quebravam as contas
    s = str(cnpj_str).replace("\n", " ").replace("\r", "")
    
    for p in s.split(","):
        p = p.strip()
        if not p: continue
        
        # BUSCA INTELIGENTE: Caça diretamente onde começa o "(Numero"
        # Isso impede que a conta zere se o nome da empresa tiver símbolos estranhos
        match = re.search(r"\(\s*(\d+)", p)
        if match:
            q = int(match.group(1))
            c = p[:match.start()].strip()
            if not c: c = p
            res[c] = res.get(c, 0) + q
        else:
            res[p] = res.get(p, 0) + 0 
    return res

def merge_cnpjs_str(s1, s2):
    d1 = parse_cnpjs(s1)
    for k, v in parse_cnpjs(s2).items():
        d1[k] = d1.get(k, 0) + v
    return formatar_cnpjs_agrupados(d1)

def merge_strings_list(s1, s2):
    l1 = [x.strip() for x in str(s1).split(",") if x.strip() and x.strip() != "nan"]
    l2 = [x.strip() for x in str(s2).split(",") if x.strip() and x.strip() != "nan"]
    return ", ".join(sorted(set(l1 + l2)))

# =========================
# LOGIN E BANCO DE DADOS
# =========================
if 'autenticado' not in st.session_state: 
    st.session_state.autenticado = False

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

# MIGRAÇÃO AUTOMÁTICA DE DADOS ANTIGOS (Segura)
if not df_final_trabalho.empty:
    df_final_trabalho = df_final_trabalho.sort_values(by=["Curso", "Turma"]).reset_index(drop=True)
    for index, row in df_final_trabalho.iterrows():
        cnpjs_parsed = parse_cnpjs(str(row["CNPJs"]))
        df_final_trabalho.at[index, "CNPJs"] = formatar_cnpjs_agrupados(cnpjs_parsed, str(row["CNPJs"]), str(row["UFs"]))

df_base_original = pd.DataFrame()
arquivo = st.file_uploader("📤 Porta de Entrada", type=["xlsx"])

if arquivo:
    if st.button("🚀 Processar e Salvar", type="primary"):
        try:
            df_raw = pd.read_excel(arquivo, dtype=str) 
            df_base_original = df_raw.copy()
            mapa = {c: "UF" if str(c).upper() in ["UF", "ESTADO"] else "CNPJ" if str(c).upper() in ["CNPJ", "CLIENTE"] else "Qtde" if str(c).upper() in ["QTDE", "QUANTIDADE", "ALUNOS"] else "Status" if str(c).upper() in ["STATUS", "SITUACAO", "SITUAÇÃO"] else "Curso" if str(c).upper() in ["CURSO", "NOME DO CURSO"] else c for c in df_raw.columns}
            df_raw = df_raw.rename(columns=mapa)
            
            # TRAVA DE SEGURANÇA NO PANDAS: Impede que linhas com dados em branco sejam deletadas na conta
            df_raw = df_raw.fillna("Não Informado")
            
            if "Qtde" in df_raw.columns:
                # O errors=coerce converte lixo em NaN, e o fillna(0) transforma em 0, não perdendo a linha
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
                        t["CNPJs"] = formatar_cnpjs_agrupados(c_dict, t.get("CNPJs", ""), t["UFs"])
                        s_dict = {}
                        for p in str(t["Status"]).split("|"):
                            if ":" in p: k, v = p.split(":"); s_dict[higienizar_status(k)] = int(v)
                        for g in aloc: s_dict[g["Status"]] = s_dict.get(g["Status"], 0) + int(1)
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
                        "CNPJs": formatar_cnpjs_agrupados(c_dict, "", uf_agrupada),
                        "Status": "|".join([f"{k}:{v}" for k, v in s_dict.items()]), "Arquivo": arquivo.name
                    })

            init_connection().table("planejamentos_turmas").delete().neq("Curso", "0").execute()
            for t in turmas_estado: 
                if "id" in t: del t["id"]
            init_connection().table("planejamentos_turmas").insert(turmas_estado).execute()
            st.session_state.dados_salvos = carregar_do_banco(); st.rerun()
        except Exception as e: st.error(f"Erro ao processar: {e}")

# =========================
# INDICADORES E TABELA
# =========================
if not df_final_trabalho.empty:
    st.divider()
    st.subheader("🏁 Painel de Controle - Visão Geral")
    st.metric("Total Geral de Alunos", df_final_trabalho["Alunos"].sum())
    c1, c2 = st.columns(2)
    with c1:
        with st.expander("🎓 Resumo por Curso", expanded=True):
            resumo = df_final_trabalho.groupby("Curso").agg(Alunos=('Alunos', 'sum'), Turmas=('Turma', 'count')).reset_index()
            st.dataframe(resumo, hide_index=True, use_container_width=True)
    with c2:
        with st.expander("📋 Solicitações", expanded=True):
            status_totals = {}
            for _, row in df_final_trabalho.iterrows():
                for p in str(row["Status"]).split("|"):
                    if ":" in p:
                        k, v = p.split(":"); status_totals[higienizar_status(k)] = status_totals.get(higienizar_status(k), 0) + int(v)
            for k, v in sorted(status_totals.items()): st.write(f"**{k}:** {v}")

    # TABELA PRINCIPAL
    st.divider()
    plano_editado = st.data_editor(
        df_final_trabalho[["Curso", "Turma", "Alunos", "UFs", "CNPJs"]],
        column_config={"CNPJs": st.column_config.TextColumn("CNPJs", width=1000)},
        use_container_width=True, hide_index=True, key="editor_principal"
    )

    # SALVAMENTO AUTOMÁTICO
    dict_editado = plano_editado.to_dict("records")
    current_hash = hash(str(dict_editado))
    if "last_saved_hash" not in st.session_state: st.session_state.last_saved_hash = current_hash
    if current_hash != st.session_state.last_saved_hash:
        st.session_state.last_saved_hash = current_hash
        db_data = []
        for row in dict_editado:
            orig = df_final_trabalho[(df_final_trabalho["Curso"] == row["Curso"]) & (df_final_trabalho["Turma"] == row["Turma"])].iloc[0]
            dados_cnpj = parse_cnpjs(str(row["CNPJs"]))
            novo_total = sum(dados_cnpj.values())
            
            detectadas = []
            for c in dados_cnpj.keys():
                uf_r = obter_uf_cnpj_seguro(c, str(row["CNPJs"]), row["UFs"])
                if uf_r != "N/I": detectadas.append(uf_r)
            nova_uf_str = ", ".join(sorted(set(detectadas))) if detectadas else row["UFs"]
            
            db_data.append({
                "Curso": row["Curso"], "Turma": row["Turma"], "Alunos": int(novo_total),
                "UFs": nova_uf_str, "CNPJs": formatar_cnpjs_agrupados(dados_cnpj, str(row["CNPJs"]), nova_uf_str),
                "Status": orig["Status"], "Arquivo": orig["Arquivo"]
            })
        salvar_background(db_data, st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])
        st.session_state.dados_salvos = pd.DataFrame(db_data); st.rerun()

    # =========================
    # RELATÓRIO: AGUARDANDO ATENDIMENTO
    # =========================
    st.divider()
    with st.expander("📄 Relatório de Aguardando Atendimento", expanded=True):
        status_alvo = "Aguardando Atendimento"
        lista_pendencias = []
        for index, row in df_final_trabalho.iterrows():
            status_raw = str(row.get("Status", ""))
            qtd_aguardando = 0
            for p in status_raw.split("|"):
                if ":" in p:
                    label, valor = p.split(":")
                    if higienizar_status(label) == status_alvo: qtd_aguardando = int(valor)

            if qtd_aguardando > 0:
                string_original = str(row.get("CNPJs", ""))
                cnpjs_na_linha = parse_cnpjs(string_original)
                total_alunos_linha = sum(cnpjs_na_linha.values())
                fator = qtd_aguardando / total_alunos_linha if total_alunos_linha > 0 else 0
                
                for cnpj, qtd_cnpj in cnpjs_na_linha.items():
                    pendencia_calc = round(qtd_cnpj * fator)
                    if pendencia_calc > 0:
                        uf_real = obter_uf_cnpj_seguro(cnpj, string_original, row["UFs"])
                        lista_pendencias.append({
                            "Curso": str(row["Curso"]),
                            "UF": uf_real, 
                            "CNPJ": cnpj, 
                            "Qtd": pendencia_calc
                        })

        if lista_pendencias:
            df_detalhe = pd.DataFrame(lista_pendencias)
            df_total_uf = df_detalhe.groupby(["Curso", "UF"])["Qtd"].sum().reset_index()
            df_total_uf.columns = ["Curso", "UF", "Vagas Aguardando"]

            st.write("**Resumo das Pendências:**")
            for _, r_uf in df_total_uf.iterrows():
                st.write(f"📍 **{r_uf['Curso']} ({r_uf['UF']}):** {int(r_uf['Vagas Aguardando'])} vagas")
            
            st.divider()
            st.dataframe(df_detalhe[["Curso", "UF", "CNPJ", "Qtd"]].sort_values(["Curso", "UF"]), use_container_width=True, hide_index=True)

            def gerar_excel_pendencias(df_d, df_t):
                output = BytesIO()
                with pd.ExcelWriter(output, engine="openpyxl") as writer:
                    df_t[["Curso", "UF", "Vagas Aguardando"]].to_excel(writer, index=False, sheet_name="Resumo_Curso_UF")
                    df_d[["Curso", "UF", "CNPJ", "Qtd"]].to_excel(writer, index=False, sheet_name="Detalhe_por_CNPJ")
                return output.getvalue()

            st.download_button(
                label="📥 Baixar Relatório com Nomes dos Cursos",
                data=gerar_excel_pendencias(df_detalhe, df_total_uf),
                file_name="relatorio_aguardando_atendimento.xlsx"
            )
        else:
            st.info("Nenhuma pendência encontrada.")
