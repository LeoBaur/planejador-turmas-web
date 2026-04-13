import streamlit as st
import pandas as pd
import plotly.express as px
import math
import json
from io import BytesIO
from supabase import create_client, Client

st.set_page_config(page_title="Planejador Inteligente de Turmas", layout="wide")

# =========================
# SISTEMA DE LOGIN
# =========================
if 'autenticado' not in st.session_state:
    st.session_state.autenticado = False

with st.sidebar:
    st.subheader("🔒 Acesso e Ferramentas")
    if not st.session_state.autenticado:
        usuario = st.text_input("Usuário")
        senha = st.text_input("Senha", type="password")
        if st.button("Entrar"):
            if usuario == "admin" and senha == "senac123":
                st.session_state.autenticado = True
                st.rerun()
            else:
                st.error("Credenciais inválidas")
    else:
        st.success(f"Logado como: Admin")
        if st.button("Sair (Logout)"):
            st.session_state.autenticado = False
            st.rerun()

if not st.session_state.autenticado:
    st.title("📊 Planejador Inteligente de Turmas")
    st.warning("Acesse a barra lateral para realizar o login.")
    st.stop()

# =========================
# CONEXÃO SUPABASE
# =========================
@st.cache_resource
def init_connection():
    try:
        url = st.secrets["SUPABASE_URL"]
        key = st.secrets["SUPABASE_KEY"]
        return create_client(url, key)
    except:
        return None

supabase = init_connection()

# =========================
# FUNÇÕES DE PERSISTÊNCIA BLINDADAS
# =========================
def carregar_do_banco():
    if supabase:
        try:
            # Tenta conectar no Supabase
            res = supabase.table("planejamentos_turmas").select("*").execute()
            return pd.DataFrame(res.data)
        except Exception as e:
            # Se a internet falhar ou o Supabase cair, avisa e não quebra o sistema
            st.error("⚠️ Aviso: Falha na conexão com o banco de dados na nuvem. Verifique o status do Supabase. O sistema está rodando em modo local.")
            return pd.DataFrame()
    return pd.DataFrame()

def deletar_banco():
    if supabase:
        try:
            supabase.table("planejamentos_turmas").delete().neq("Curso", "0").execute()
            st.cache_resource.clear()
        except Exception as e:
            st.error(f"Erro ao tentar limpar o banco de dados: {e}")

# =========================
# FUNÇÃO GERAR TURMAS (MOTOR)
# =========================
def gerar_turmas(df, min_a, max_a):
    turmas_lista = []
    for curso in df["Curso"].unique():
        dados_curso = df[df["Curso"] == curso]
        elementos = []
        for _, row in dados_curso.iterrows():
            elementos.extend([{"UF": str(row["UF"]), "CNPJ": str(row["CNPJ"])}] * int(row["Qtde"]))
        
        total = len(elementos)
        if total == 0: continue
        
        n_turmas = math.ceil(total / max_a)
        while total / n_turmas < min_a and n_turmas > 1:
            n_turmas -= 1
            
        tam_base = total // n_turmas
        sobra = total % n_turmas
        ponteiro = 0
        
        for i in range(n_turmas):
            tam = tam_base + (1 if i < sobra else 0)
            grupo = elementos[ponteiro:ponteiro+tam]
            ponteiro += tam
            
            ufs = sorted(set([str(g["UF"]) for g in grupo]))
            cnpjs = sorted(set([str(g["CNPJ"]) for g in grupo]))
            
            turmas_lista.append({
                "Curso": curso,
                "Turma": f"{curso[:3].upper()}-{i+1:02d}",
                "Alunos": len(grupo),
                "UFs": ", ".join(ufs),
                "CNPJs": ", ".join(cnpjs)
            })
    return pd.DataFrame(turmas_lista)

def gerar_excel_final(plano_df, original_df):
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        plano_df.to_excel(writer, sheet_name="Planejamento", index=False)
        original_df.to_excel(writer, sheet_name="Base_Original", index=False)
    return output.getvalue()


# =========================
# PARÂMETROS E MODELOS
# =========================
st.sidebar.header("⚙️ Configurações")
min_alunos = st.sidebar.number_input("Mínimo por turma", min_value=1, value=30)
max_alunos = st.sidebar.number_input("Máximo por turma", min_value=1, value=45)

if st.sidebar.button("🗑️ Deletar Planilha Atual do Banco"):
    deletar_banco()
    st.success("Banco de dados limpo com sucesso!")
    st.rerun()

st.title("📊 Planejador Inteligente de Turmas")

# =========================
# CARREGAMENTO E LÓGICA
# =========================
plano_existente = carregar_do_banco()

arquivo = st.file_uploader("📤 Subir Atualização de Planilha", type=["xlsx"])

if arquivo:
    try:
        df_raw = pd.read_excel(arquivo)
        df_raw.columns = [str(c).strip().title() for c in df_raw.columns]
        df_raw = df_raw.rename(columns={"Uf": "UF", "Cnpj": "CNPJ", "Qtde": "Qtde"})

        cols_obrigatorias = ["Curso", "UF", "CNPJ", "Qtde"]
        if not all(c in df_raw.columns for c in cols_obrigatorias):
            st.error(f"Faltam colunas. Certifique-se de ter: {cols_obrigatorias}")
            st.stop()

        # Limpeza e Tipagem
        df_raw["Curso"] = df_raw["Curso"].astype(str).str.strip()
        df_raw["UF"] = df_raw["UF"].astype(str).str.strip()
        df_raw["CNPJ"] = df_raw["CNPJ"].astype(str).str.strip()
        df_raw["Qtde"] = pd.to_numeric(df_raw["Qtde"], errors='coerce').fillna(0).astype(int)
        df_validos = df_raw[df_raw["Qtde"] > 0].copy()

        # 1. Dashboard de Status (Soma de Alunos Real)
        if "Status" in df_validos.columns:
            st.subheader("📈 Alunos por Status (Quantitativo Real)")
            df_status = df_validos.groupby("Status")["Qtde"].sum().reset_index()
            fig = px.bar(df_status, x="Status", y="Qtde", color="Status", text_auto=True, title="Totais por Fase")
            st.plotly_chart(fig, use_container_width=True)
            st.divider()

        # 2. Gera novo planejamento baseado na planilha
        df_motor = df_validos.groupby(["Curso", "UF", "CNPJ"], as_index=False)["Qtde"].sum()
        novo_plano = gerar_turmas(df_motor, min_alunos, max_alunos)

        if not novo_plano.empty:
            st.subheader("📚 Planejamento Atualizado (Auto-Save)")
            st.info("Altere o nome da Turma abaixo. As alterações são sincronizadas com a nuvem.")
            
            plano_editado = st.data_editor(
                novo_plano,
                column_config={"Turma": st.column_config.TextColumn("Nome da Turma (Editável)")},
                disabled=["Curso", "Alunos", "UFs", "CNPJs"],
                use_container_width=True,
                hide_index=True
            )

            # Auto-save Inteligente
            if supabase:
                try:
                    dados_json = plano_editado.to_dict(orient="records")
                    supabase.table("planejamentos_turmas").delete().neq("Curso", "0").execute()
                    supabase.table("planejamentos_turmas").insert(dados_json).execute()
                    st.toast("Sincronizado com Supabase!", icon="✅")
                except:
                    pass

            # 3. Alertas de Cancelamento/Baixa Ocupação
            st.subheader("⚠️ Alertas de Ocupação")
            turmas_baixas = plano_editado[plano_editado["Alunos"] < min_alunos]
            if not turmas_baixas.empty:
                st.error("Detectamos turmas abaixo do quantitativo mínimo configurado. Sugerimos cancelamento ou remanejamento:")
                st.dataframe(turmas_baixas[["Curso", "Turma", "Alunos"]], hide_index=True)
            else:
                st.success("Todas as turmas atingiram o quórum mínimo de alunos!")

            st.divider()
            
            st.download_button(
                label="📥 Baixar Planejamento (Excel)",
                data=gerar_excel_final(plano_editado, df_raw),
                file_name="planejamento_senac_sincronizado.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

    except Exception as e:
        st.error(f"Erro no processamento: {e}")

# Se não foi feito upload de planilha nova, exibe os dados que já estavam salvos no Supabase
elif not plano_existente.empty:
    st.subheader("📂 Último Planejamento Salvo na Nuvem")
    st.write("Estes são os dados que já estavam salvos. Para atualizar, suba uma nova planilha.")
    st.dataframe(plano_existente, use_container_width=True, hide_index=True)

    st.download_button(
        label="📥 Baixar Planejamento em Nuvem (Excel)",
        data=gerar_excel_final(plano_existente, pd.DataFrame()),
        file_name="planejamento_salvo_nuvem.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
