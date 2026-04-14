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
            res[match.group(1).strip()] = res.get(match.group(1).strip(), 0) + int(match.group(2))
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
        client.table("planejamentos_turmas").update({
            "Alunos": int(destino["Alunos"]) + int(origem["Alunos"]),
            "CNPJs": merge_cnpjs_str(origem["CNPJs"], destino["CNPJs"]),
            "UFs": merge_strings_list(origem["UFs"], destino["UFs"]),
            "Arquivo": merge_strings_list(origem["Arquivo"], destino["Arquivo"]),
            "Status": str(destino["Status"]) + "|" + str(origem["Status"])
        }).eq("id", destino["id"]).execute()
        client.table("planejamentos_turmas").delete().eq("id", origem["id"]).execute()

def distribuir_turma(nome_origem, curso, url, key):
    client = create_client(url, key)
    res = client.table("planejamentos_turmas").select("*").eq("Curso", curso).execute()
    df_db = pd.DataFrame(res.data)
    origem = df_db[df_db["Turma"] == nome_origem].iloc[0]
    destinos = df_db[df_db["Turma"] != nome_origem]
    if destinos.empty: return
    adds = [int(origem["Alunos"]) // len(destinos)] * len(destinos)
    for i in range(int(origem["Alunos"]) % len(destinos)): adds[i] += 1
    for i, (_, dest) in enumerate(destinos.iterrows()):
        client.table("planejamentos_turmas").update({"Alunos": int(dest["Alunos"]) + adds[i]}).eq("id", dest["id"]).execute()
    client.table("planejamentos_turmas").delete().eq("id", origem["id"]).execute()

# =========================
# LOGIN E CONFIGURAÇÕES
# =========================
if 'autenticado' not in st.session_state: st.session_state.autenticado = False
if 'turmas_ignoradas' not in st.session_state: st.session_state.turmas_ignoradas = []

with st.sidebar:
    if not st.session_state.autenticado:
        with st.form("login"):
            u, s = st.text_input("Usuário"), st.text_input("Senha", type="password")
            if st.form_submit_button("Entrar") and u == "admin" and s == "senac123":
                st.session_state.autenticado = True
                st.rerun()
    else:
        if st.button("🚪 Sair"): st.session_state.autenticado = False; st.rerun()
        min_alunos = st.number_input("Mínimo", min_value=1, value=25)
        max_alunos = st.number_input("Máximo", min_value=1, value=45)
        if st.button("🚨 Resetar Banco"):
            create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"]).table("planejamentos_turmas").delete().neq("Curso", "0").execute()
            st.session_state.dados_salvos = pd.DataFrame(); st.rerun()

if not st.session_state.autenticado: st.stop()

# =========================
# BANCO DE DADOS
# =========================
def carregar_do_banco():
    try:
        supabase = create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])
        res = supabase.table("planejamentos_turmas").select("*").execute()
        return pd.DataFrame(res.data)
    except: return pd.DataFrame()

df_final_trabalho = carregar_do_banco()

# =========================
# UPLOAD E PROCESSAMENTO (TOP-OFF)
# =========================
st.title("📊 Planejador Inteligente de Turmas")
arquivo = st.file_uploader("📤 Porta de Entrada", type=["xlsx"])

if arquivo:
    if st.button("🚀 Processar e Salvar"):
        try:
            df_raw = pd.read_excel(arquivo)
            mapa = {c: "UF" if str(c).upper() in ["UF", "ESTADO"] else "CNPJ" if str(c).upper() in ["CNPJ", "CLIENTE"] else "Qtde" if str(c).upper() in ["QTDE", "QUANTIDADE", "ALUNOS"] else "Status" if str(c).upper() in ["STATUS", "SITUACAO", "SITUAÇÃO"] else "Curso" if str(c).upper() in ["CURSO", "NOME DO CURSO"] else c for c in df_raw.columns}
            df_raw = df_raw.rename(columns=mapa)
            df_motor = df_raw.groupby(["Curso", "UF", "CNPJ", "Status"], as_index=False)["Qtde"].sum()

            turmas_estado = df_final_trabalho.to_dict('records') if not df_final_trabalho.empty else []
            
            for curso in df_motor["Curso"].unique():
                dados_curso = df_motor[df_motor["Curso"] == curso]
                elementos = []
                for _, r in dados_curso.iterrows():
                    elementos.extend([{"UF": str(r["UF"]), "CNPJ": str(r["CNPJ"]), "Status": higienizar_status(r["Status"])}] * int(r["Qtde"]))
                
                # Preenchimento
                for t in [x for x in turmas_estado if x["Curso"] == curso]:
                    vagas = max_alunos - int(t["Alunos"])
                    if vagas > 0 and elementos:
                        aloc = elementos[:vagas]
                        elementos = elementos[vagas:]
                        t["Alunos"] += len(aloc)
                        t["UFs"] = merge_strings_list(t["UFs"], ",".join([g["UF"] for g in aloc]))
                        t["Arquivo"] = merge_strings_list(t.get("Arquivo", ""), arquivo.name)
                        c_dict = parse_cnpjs(t["CNPJs"])
                        for g in aloc: c_dict[g["CNPJ"]] = c_dict.get(g["CNPJ"], 0) + 1
                        t["CNPJs"] = ", ".join([f"{k} ({v})" for k, v in sorted(c_dict.items())])
                        s_dict = {}
                        for p in str(t["Status"]).split("|"):
                            if ":" in p: k,v = p.split(":"); s_dict[higienizar_status(k)] = int(v)
                        for g in aloc: s_dict[g["Status"]] = s_dict.get(g["Status"], 0) + 1
                        t["Status"] = "|".join([f"{k}:{v}" for k, v in s_dict.items()])

                # Novas Turmas
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

            supabase = create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])
            supabase.table("planejamentos_turmas").delete().neq("Curso", "0").execute()
            for t in turmas_estado: 
                if "id" in t: del t["id"]
            supabase.table("planejamentos_turmas").insert(turmas_estado).execute()
            st.rerun()
        except Exception as e: st.error(f"Erro: {e}")

# =========================
# PAINEL E TABELA (COM SCROLL)
# =========================
if not df_final_trabalho.empty:
    st.divider()
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("🎓 Resumo por Curso")
        resumo = df_final_trabalho.groupby("Curso").agg(Alunos=('Alunos', 'sum'), Turmas=('Turma', 'count')).reset_index()
        st.write(f"**Total Geral:** {resumo['Turmas'].sum()} turmas")
        st.dataframe(resumo, hide_index=True)
    
    st.subheader("📚 Ajuste de Planejamento")
    # Tabela com barra de rolagem horizontal garantida
    plano_editado = st.data_editor(
        df_final_trabalho[["Curso", "Turma", "Alunos", "UFs", "CNPJs"]],
        column_config={
            "CNPJs": st.column_config.TextColumn("CNPJs", width=800),
            "UFs": st.column_config.TextColumn("Estados", width=200),
            "Alunos": st.column_config.NumberColumn("Qtd")
        },
        use_container_width=True, hide_index=True, key="editor_principal"
    )

    # Salvamento automático
    dict_editado = plano_editado.to_dict("records")
    current_hash = hash(str(dict_editado))
    if "last_hash" not in st.session_state: st.session_state.last_hash = current_hash
    if current_hash != st.session_state.last_hash:
        st.session_state.last_hash = current_hash
        db_data = []
        for i, row in plano_editado.iterrows():
            orig = df_final_trabalho.iloc[i]
            db_data.append({
                "Curso": row["Curso"], "Turma": row["Turma"], "Alunos": int(row["Alunos"]),
                "UFs": row["UFs"], "CNPJs": row["CNPJs"], "Status": orig["Status"], "Arquivo": orig["Arquivo"]
            })
        threading.Thread(target=salvar_background, args=(db_data, st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])).start()

    # =========================
    # NOVO RELATÓRIO: AGUARDANDO ATENDIMENTO
    # =========================
    st.divider()
    st.subheader("📄 Relatório de CNPJs (Aguardando atendimento)")
    
    pendencias = []
    status_alvo = "Aguardando Atendimento"
    
    for _, row in df_final_trabalho.iterrows():
        status_raw = str(row.get("Status", ""))
        stats_dict = {}
        for p in status_raw.split("|"):
            if ":" in p:
                k, v = p.split(":")
                stats_dict[higienizar_status(k)] = int(v)
        
        qtd_aguardando = stats_dict.get(status_alvo, 0)
        
        if qtd_aguardando > 0:
            cnpjs_row = parse_cnpjs(row["CNPJs"])
            total_alunos_row = sum(cnpjs_row.values())
            fator = qtd_aguardando / total_alunos_row if total_alunos_row > 0 else 0
            
            # Pega a primeira UF da lista para o relatório
            uf_lista = str(row["UFs"]).split(",")
            uf_principal = uf_lista[0].strip() if uf_lista else "N/A"
            
            for c, q in cnpjs_row.items():
                qtd_pendente = round(q * fator)
                if qtd_pendente > 0:
                    pendencias.append({"UF": uf_principal, "CNPJ": c, "Qtd Aguardando": qtd_pendente})

    if pendencias:
        df_rel = pd.DataFrame(pendencias).groupby(["UF", "CNPJ"], as_index=False)["Qtd Aguardando"].sum()
        st.dataframe(df_rel, use_container_width=True, hide_index=True)
        
        out_rel = BytesIO()
        with pd.ExcelWriter(out_rel, engine="openpyxl") as wr:
            df_rel.to_excel(wr, index=False, sheet_name="Pendencias")
        st.download_button("📥 Baixar Relatório (Aguardando)", data=out_rel.getvalue(), file_name="aguardando_atendimento.xlsx")
    else:
        st.info("Nenhuma pendência encontrada.")

    st.divider()
    st.download_button("📥 Baixar Planejamento Completo", data=gerar_excel_final(plano_editado, pd.DataFrame()), file_name="planejamento_completo.xlsx")
