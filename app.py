import streamlit as st
import pandas as pd
import plotly.express as px
import math
import json
from io import BytesIO
from supabase import create_client, Client

st.set_page_config(page_title="Planejador Inteligente de Turmas", layout="wide")

# =========================
# FUNÇÕES DE APOIO (DOWNLOADS)
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
        plano_df.to_excel(writer, sheet_name="Planejamento", index=False)
        if not original_df.empty:
            original_df.to_excel(writer, sheet_name="Base_Original", index=False)
    return output.getvalue()

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
        
        # O modelo de planilha voltou para a barra lateral!
        st.divider()
        st.write("📂 **Modelos**")
        st.download_button(
            label="📥 Baixar Modelo de Planilha",
            data=gerar_modelo_excel(),
            file_name="modelo_planejamento.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

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
            res = supabase.table("planejamentos_turmas").select("*").execute()
            return pd.DataFrame(res.data)
        except Exception as e:
            st.error("⚠️ Aviso: Falha na conexão com o banco de dados na nuvem. O sistema está rodando em modo local.")
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
# MOTOR DE GERAÇÃO
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

# =========================
# PARÂMETROS DA LATERAL
# =========================
st.sidebar.header("⚙️ Configurações")
min_alunos = st.sidebar.number_input("Mínimo por turma", min_value=1, value=30)
max_alunos = st.sidebar.number_input("Máximo por turma", min_value=1, value=45)

if st.sidebar.button("🗑️ Deletar Planilha do Banco"):
    deletar_banco()
    st.success("Banco de dados limpo com sucesso!")
    st.rerun()

st.title("📊 Planejador Inteligente de Turmas")

# =========================
# FLUXO PRINCIPAL
# =========================
plano_existente = carregar_do_banco()
arquivo = st.file_uploader("📤 Subir Atualização de Planilha", type=["xlsx"])

df_base_exportacao = pd.DataFrame()
plano_para_exibir = pd.DataFrame()

# CENÁRIO 1: O USUÁRIO SUBIU UMA PLANILHA NOVA
if arquivo:
    try:
        df_raw = pd.read_excel(arquivo)
        df_base_exportacao = df_raw.copy()
        
        df_raw.columns = [str(c).strip().title() for c in df_raw.columns]
        df_raw = df_raw.rename(columns={"Uf": "UF", "Cnpj": "CNPJ", "Qtde": "Qtde"})

        cols_obrigatorias = ["Curso", "UF", "CNPJ", "Qtde"]
        if not all(c in df_raw.columns for c in cols_obrigatorias):
            st.error(f"Faltam colunas. Certifique-se de ter: {cols_obrigatorias}")
            st.stop()

        df_raw["Curso"] = df_raw["Curso"].astype(str).str.strip()
        df_raw["UF"] = df_raw["UF"].astype(str).str.strip()
        df_raw["CNPJ"] = df_raw["CNPJ"].astype(str).str.strip()
        df_raw["Qtde"] = pd.to_numeric(df_raw["Qtde"], errors='coerce').fillna(0).astype(int)
        df_validos = df_raw[df_raw["Qtde"] > 0].copy()

        # Dashboard de Status Real
        if "Status" in df_validos.columns:
            st.subheader("📈 Alunos por Status (Quantitativo Real)")
            df_status = df_validos.groupby("Status")["Qtde"].sum().reset_index()
            fig_st = px.bar(df_status, x="Status", y="Qtde", color="Status", text_auto=True, title="Totais por Fase")
            st.plotly_chart(fig_st, use_container_width=True)
            st.divider()

        # Gerar o Planejamento
        df_motor = df_validos.groupby(["Curso", "UF", "CNPJ"], as_index=False)["Qtde"].sum()
        plano_para_exibir = gerar_turmas(df_motor, min_alunos, max_alunos)
        
        if not plano_para_exibir.empty:
            st.subheader("📚 Planejamento Atualizado (Auto-Save)")
            st.info("Altere o nome da Turma abaixo. As alterações são sincronizadas com a nuvem.")

    except Exception as e:
        st.error(f"Erro no processamento da planilha: {e}")

# CENÁRIO 2: NÃO SUBIU PLANILHA, MAS TEM DADOS NO BANCO
elif not plano_existente.empty:
    st.subheader("📂 Último Planejamento Salvo na Nuvem")
    st.caption("Você pode continuar editando os nomes das turmas abaixo ou buscar por CNPJ.")
    plano_para_exibir = plano_existente.copy()

# =========================
# INTERFACE COMUM (Sempre aparece se houver dados)
# =========================
if not plano_para_exibir.empty:
    
    # 1. Tabela Editável e Auto-Save
    plano_editado = st.data_editor(
        plano_para_exibir,
        column_config={"Turma": st.column_config.TextColumn("Nome da Turma (Editável)")},
        disabled=["Curso", "Alunos", "UFs", "CNPJs"],
        use_container_width=True,
        hide_index=True,
        key="editor_principal"
    )

    # Auto-save Inteligente
    if supabase:
        try:
            dados_json = plano_editado.to_dict(orient="records")
            supabase.table("planejamentos_turmas").delete().neq("Curso", "0").execute()
            supabase.table("planejamentos_turmas").insert(dados_json).execute()
        except:
            pass # Erros de rede já foram avisados lá em cima

    # 2. Alertas de Ocupação (Avisa se a turma ficou abaixo do mínimo)
    st.subheader("⚠️ Alertas de Ocupação")
    turmas_baixas = plano_editado[plano_editado["Alunos"] < min_alunos]
    if not turmas_baixas.empty:
        st.error("Detectamos turmas abaixo do quantitativo mínimo configurado. Sugerimos cancelamento ou remanejamento:")
        st.dataframe(turmas_baixas[["Curso", "Turma", "Alunos"]], hide_index=True)
    else:
        st.success("Todas as turmas atingiram o quórum mínimo de alunos!")

    st.divider()

    # 3. O BUSCADOR DE CNPJ VOLTOU!
    st.subheader("🔍 Localizador de CNPJ")
    busca = st.text_input("Pesquisar CNPJ na lista gerada para saber a qual turma pertence:")
    if busca:
        resultado = plano_editado[plano_editado["CNPJs"].astype(str).str.contains(busca, na=False)]
        if not resultado.empty:
            st.success(f"Encontrado!")
            st.dataframe(resultado[["Curso", "Turma", "Alunos", "UFs"]], hide_index=True, use_container_width=True)
        else:
            st.warning("CNPJ não localizado neste planejamento.")

    st.divider()

    # 4. OS TRÊS GRÁFICOS ORIGINAIS VOLTARAM!
    col1, col2, col3 = st.columns(3)
    resumo_curso = plano_editado.groupby("Curso").size().reset_index(name="Turmas")

    with col1:
        fig1 = px.bar(resumo_curso, x="Curso", y="Turmas", title="Turmas por Curso")
        st.plotly_chart(fig1, use_container_width=True)

    with col2:
        fig2 = px.pie(plano_editado, names="Curso", title="Distribuição das Turmas")
        st.plotly_chart(fig2, use_container_width=True)

    with col3:
        fig3 = px.histogram(plano_editado, x="Alunos", nbins=10, title="Alunos por Turma")
        st.plotly_chart(fig3, use_container_width=True)

    st.divider()

    # 5. BOTÃO DE EXPORTAÇÃO FINAL
    st.download_button(
        label="📥 Baixar Planejamento (Excel)",
        data=gerar_excel_final(plano_editado, df_base_exportacao),
        file_name="planejamento_senac_sincronizado.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
