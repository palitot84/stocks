import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime, timedelta
import json
import os
import time
from functools import lru_cache

# Configuração da página
st.set_page_config(page_title="Análise de Ações", layout="wide", page_icon="📈")

# Arquivo para persistência de dados
DATA_FILE = "stocks_data.json"
CACHE_FILE = "stocks_cache.json"
CACHE_DURATION = 300  # Cache duration in seconds (5 minutes)
REQUEST_DELAY = 2  # Delay between requests in seconds

# Inicializar dados
def load_data():
    """Carrega dados salvos do arquivo JSON"""
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {
        "stocks": [],
        "categories": {},  # {ticker: category_name}
        "category_list": [],  # Lista de categorias disponíveis
        "filters": {},
        "selected_columns": [
            "Open", "High", "Low", "Close", "Volume", 
            "Dividends", "Stock Splits"
        ]
    }

def save_data(data):
    """Salva dados no arquivo JSON"""
    try:
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        # Verificar se foi salvo corretamente
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            saved = json.load(f)
            return len(saved.get("stocks", [])) == len(data.get("stocks", []))
    except Exception as e:
        st.error(f"Erro ao salvar dados: {e}")
        return False

def load_cache():
    """Carrega cache de dados de ações"""
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_cache(cache):
    """Salva cache de dados de ações"""
    with open(CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)

def get_cache_key(stock, period):
    """Gera chave única para o cache"""
    return f"{stock}_{period}"

def is_cache_valid(cache_entry):
    """Verifica se o cache ainda é válido"""
    if 'timestamp' not in cache_entry:
        return False
    age = time.time() - cache_entry['timestamp']
    return age < CACHE_DURATION

def fetch_stock_data_with_retry(ticker, period_value, max_retries=3):
    """Busca dados com retry e backoff exponencial"""
    for attempt in range(max_retries):
        try:
            # Adicionar delay entre requisições
            if attempt > 0:
                wait_time = REQUEST_DELAY * (2 ** attempt)  # Exponential backoff
                time.sleep(wait_time)
            
            # Tentar com ticker.history primeiro
            df = ticker.history(
                period=period_value,
                auto_adjust=True,
                actions=True,
                timeout=15
            )
            
            if not df.empty:
                return df, None
            
            # Se vazio ou erro de crumb, tentar download direto imediatamente
            if attempt == 0 or attempt == max_retries - 1:
                df_alt, error_alt = try_alternative_download(ticker.ticker, period_value)
                if not df_alt.empty:
                    return df_alt, None
                
        except Exception as e:
            error_msg = str(e)
            
            # Para erros de Crumb, tentar método alternativo imediatamente
            if "Crumb" in error_msg or "Unauthorized" in error_msg:
                df_alt, error_alt = try_alternative_download(ticker.ticker, period_value)
                if not df_alt.empty:
                    return df_alt, None
                elif attempt < max_retries - 1:
                    continue
                    
            if "429" in error_msg or "Too Many Requests" in error_msg:
                if attempt < max_retries - 1:
                    wait_time = REQUEST_DELAY * (2 ** (attempt + 2))  # Longer wait for 429
                    time.sleep(wait_time)
                    continue
            elif attempt < max_retries - 1:
                continue
            return pd.DataFrame(), f"Erro após {max_retries} tentativas: {error_msg}"
    
    return pd.DataFrame(), "Não foi possível obter dados após múltiplas tentativas"

def try_alternative_download(stock, period_value):
    """Método alternativo de download usando yf.download"""
    try:
        end_date = datetime.now()
        
        # Calcular data inicial baseado no período
        if period_value == "7d":
            start_date = end_date - timedelta(days=7)
        elif period_value == "1mo":
            start_date = end_date - timedelta(days=30)
        elif period_value == "ytd":
            start_date = datetime(end_date.year, 1, 1)
        elif period_value == "1y":
            start_date = end_date - timedelta(days=365)
        elif period_value == "3y":
            start_date = end_date - timedelta(days=365*3)
        elif period_value == "5y":
            start_date = end_date - timedelta(days=365*5)
        else:
            start_date = end_date - timedelta(days=30)
        
        time.sleep(REQUEST_DELAY * 0.5)  # Shorter delay for alternative method
        
        # Usar yf.download que tem melhor tratamento de cookies/crumbs
        df = yf.download(
            stock,
            start=start_date,
            end=end_date,
            progress=False,
            timeout=15,
            ignore_tz=True,  # Evitar problemas de timezone
            prepost=False,   # Não incluir pre/post market
            repair=True      # Tentar reparar dados com problemas
        )
        
        # Se df for multi-index (quando tem múltiplos tickers), simplificar
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.droplevel(1)
            
        return df, None
    except Exception as e:
        return pd.DataFrame(), str(e)

def fetch_ticker_info_safe(ticker, max_retries=2):
    """Busca informações do ticker com tratamento de erro"""
    for attempt in range(max_retries):
        try:
            if attempt > 0:
                time.sleep(REQUEST_DELAY * 2)
            
            # Tentar obter info básico
            info = ticker.info
            
            # Se vier vazio ou com erro, tentar fast_info como fallback
            if not info or len(info) < 5:
                try:
                    fast_info = ticker.fast_info
                    info = {
                        'currency': getattr(fast_info, 'currency', 'USD'),
                        'exchange': getattr(fast_info, 'exchange', 'N/A'),
                        'quoteType': getattr(fast_info, 'quote_type', 'N/A'),
                        'timezone': getattr(fast_info, 'timezone', 'N/A')
                    }
                except:
                    pass
                    
            return info
        except Exception as e:
            error_msg = str(e)
            # Para erros de Crumb ou 429, não insistir muito em info
            if "Crumb" in error_msg or "Unauthorized" in error_msg or "429" in error_msg:
                return {}
            if attempt < max_retries - 1:
                continue
            return {}
    return {}

# Carregar dados - sempre recarregar do arquivo para garantir persistência
if 'data' not in st.session_state:
    st.session_state.data = load_data()
else:
    # Recarregar do arquivo para garantir que temos a versão mais recente
    loaded_data = load_data()
    if loaded_data != st.session_state.data:
        st.session_state.data = loaded_data

if 'cache' not in st.session_state:
    st.session_state.cache = load_cache()

if 'last_request_time' not in st.session_state:
    st.session_state.last_request_time = 0

# Todos os campos disponíveis do Yahoo Finance
ALL_YAHOO_FIELDS = [
    "Open", "High", "Low", "Close", "Volume", 
    "Dividends", "Stock Splits", "Adj Close"
]

# Opções de período
PERIOD_OPTIONS = {
    "Hoje (Tempo Real)": "1d",
    "1 Semana": "7d",
    "1 Mês": "1mo",
    "No Ano (YTD)": "ytd",
    "1 Ano": "1y",
    "3 Anos": "3y",
    "5 Anos": "5y"
}

def get_current_price(ticker_symbol):
    """Obtém preço atual com delay de 10 minutos"""
    try:
        ticker = yf.Ticker(ticker_symbol)
        is_brazilian = '.SA' in ticker_symbol.upper()
        
        # Tentar fast_info primeiro (mais rápido)
        try:
            fast_info = ticker.fast_info
            return {
                'price': fast_info.last_price,
                'previous_close': fast_info.previous_close,
                'open': fast_info.open,
                'day_high': fast_info.day_high,
                'day_low': fast_info.day_low,
                'volume': fast_info.last_volume,
                'timestamp': datetime.now() - timedelta(minutes=10),
                'currency': 'BRL' if is_brazilian else 'USD'
            }
        except:
            pass
        
        # Fallback: pegar dados do último dia
        hist = ticker.history(period='1d', interval='1m')
        if not hist.empty:
            last_row = hist.iloc[-1]
            first_row = hist.iloc[0]
            return {
                'price': last_row['Close'],
                'previous_close': first_row['Open'],
                'open': first_row['Open'],
                'day_high': hist['High'].max(),
                'day_low': hist['Low'].min(),
                'volume': hist['Volume'].sum(),
                'timestamp': hist.index[-1],
                'currency': 'BRL' if is_brazilian else 'USD'
            }
        
        return None
    except:
        return None

# Título principal
st.title("📈 Sistema de Análise de Ações")

# Info de persistência
if os.path.exists(DATA_FILE):
    file_time = datetime.fromtimestamp(os.path.getmtime(DATA_FILE))
    st.caption(f"📁 Dados carregados de: {DATA_FILE} | Última modificação: {file_time.strftime('%d/%m/%Y %H:%M:%S')}")
else:
    st.caption(f"📁 Arquivo de dados será criado em: {os.path.abspath(DATA_FILE)}")

st.markdown("---")

# Sidebar para configurações
with st.sidebar:
    st.header("⚙️ Configurações")
    

    
    # Seção de Colunas da Tabela
    st.subheader("📋 Colunas da Tabela")
    
    with st.expander("Configurar Colunas"):
        st.write("Selecione as colunas que aparecerão na tabela:")
        selected_columns = []
        
        for field in ALL_YAHOO_FIELDS:
            if st.checkbox(
                field, 
                value=field in st.session_state.data["selected_columns"],
                key=f"col_{field}"
            ):
                selected_columns.append(field)
        
        if st.button("Salvar Colunas", type="primary"):
            st.session_state.data["selected_columns"] = selected_columns
            save_data(st.session_state.data)
            st.success("✅ Colunas salvas!")
    
    st.markdown("---")
    
    # Seção de Filtros
    st.subheader("🔍 Filtros Personalizados")
    
    with st.expander("➕ Adicionar Filtro"):
        filter_name = st.text_input("Nome do Filtro", placeholder="Ex: Volume Alto")
        filter_field = st.selectbox("Campo", ALL_YAHOO_FIELDS)
        filter_operator = st.selectbox("Operador", [">", "<", ">=", "<=", "=="])
        filter_value = st.number_input("Valor", value=0.0)
        
        if st.button("Adicionar Filtro", type="primary"):
            if filter_name:
                st.session_state.data["filters"][filter_name] = {
                    "field": filter_field,
                    "operator": filter_operator,
                    "value": filter_value
                }
                save_data(st.session_state.data)
                st.success(f"✅ Filtro '{filter_name}' adicionado!")
                st.rerun()
            else:
                st.warning("⚠️ Digite um nome para o filtro!")
    
    # Lista de filtros cadastrados
    if st.session_state.data["filters"]:
        st.write("**Filtros Cadastrados:**")
        for filter_name, filter_config in list(st.session_state.data["filters"].items()):
            col1, col2 = st.columns([3, 1])
            with col1:
                st.write(f"• {filter_name}")
                st.caption(f"{filter_config['field']} {filter_config['operator']} {filter_config['value']}")
            with col2:
                if st.button("🗑️", key=f"del_filter_{filter_name}"):
                    del st.session_state.data["filters"][filter_name]
                    save_data(st.session_state.data)
                    st.rerun()
    else:
        st.info("Nenhum filtro cadastrado ainda.")

def calcular_variacao(ticker_symbol, dias):
    """Calcula variação percentual para um período específico usando dados do yfinance"""
    try:
        ticker = yf.Ticker(ticker_symbol)
        
        # Usar fast_info para dados rápidos quando disponível
        try:
            fast_info = ticker.fast_info
            current_price = fast_info.last_price
        except:
            # Fallback: buscar histórico recente
            hist_recent = ticker.history(period='1d')
            if hist_recent.empty:
                return None, None
            current_price = hist_recent['Close'].iloc[-1]
        
        # Mapear dias para períodos do yfinance
        if dias == 1:
            period = '5d'  # Pegar últimos 5 dias para garantir ter 2 pregões
        elif dias <= 7:
            period = '1mo'
        elif dias <= 30:
            period = '1mo'
        elif dias <= 90:
            period = '3mo'
        elif dias <= 180:
            period = '6mo'
        else:
            period = '1y'
        
        hist = ticker.history(period=period)
        
        if hist.empty or len(hist) < 2:
            return None, None
        
        # Calcular quantos dias de negociação correspondem ao período
        # Aproximadamente 252 dias úteis por ano
        if dias == 1:
            # Variação diária: último vs penúltimo pregão
            if len(hist) >= 2:
                preco_inicial = hist['Close'].iloc[-2]
                preco_final = hist['Close'].iloc[-1]
            else:
                return None, None
        else:
            # Para outros períodos, calcular dias úteis aproximados
            # Considerar ~21 dias úteis por mês (252/12)
            dias_uteis = int(dias * (252 / 365))
            
            # Garantir que não pegamos mais dias do que temos
            if dias_uteis >= len(hist):
                preco_inicial = hist['Close'].iloc[0]
            else:
                # Pegar o preço N dias úteis atrás
                preco_inicial = hist['Close'].iloc[-(dias_uteis + 1)]
            
            preco_final = hist['Close'].iloc[-1]
        
        variacao = ((preco_final - preco_inicial) / preco_inicial) * 100
        return variacao, preco_final
        
    except Exception as e:
        return None, None

def calcular_variacao_ytd(ticker_symbol):
    """Calcula variação desde o início do ano corrente (YTD - Year To Date)"""
    try:
        ticker = yf.Ticker(ticker_symbol)
        
        # Buscar dados desde o início do ano
        hist = ticker.history(period='ytd')
        
        if hist.empty or len(hist) < 2:
            return None, None
        
        # Primeiro preço do ano
        preco_inicial = hist['Close'].iloc[0]
        # Último preço disponível
        preco_final = hist['Close'].iloc[-1]
        
        variacao = ((preco_final - preco_inicial) / preco_inicial) * 100
        return variacao, preco_final
        
    except Exception as e:
        return None, None

def gerar_relatorio_comparativo(lista_acoes, categories_dict):
    """Gera relatório comparativo de todas as ações"""
    relatorio = []
    
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    for i, ticker_symbol in enumerate(lista_acoes):
        status_text.text(f"Processando {ticker_symbol}... ({i+1}/{len(lista_acoes)})")
        
        linha = {
            'Ação': ticker_symbol,
            'Categoria': categories_dict.get(ticker_symbol, 'Sem categoria')
        }
        
        # Preço atual
        current_data = get_current_price(ticker_symbol)
        if current_data:
            linha['Preço Atual'] = current_data['price']
        else:
            linha['Preço Atual'] = None
        
        # Calcular variações
        var_1d, _ = calcular_variacao(ticker_symbol, 1)
        var_7d, _ = calcular_variacao(ticker_symbol, 7)
        var_30d, _ = calcular_variacao(ticker_symbol, 30)
        var_90d, _ = calcular_variacao(ticker_symbol, 90)
        var_180d, _ = calcular_variacao(ticker_symbol, 180)
        var_365d_full, _ = calcular_variacao(ticker_symbol, 365)
        var_ano, _ = calcular_variacao_ytd(ticker_symbol)  # Desde 01/01 do ano corrente
        
        linha['Var. Dia (%)'] = var_1d
        linha['Var. 7 Dias (%)'] = var_7d
        linha['Var. 30 Dias (%)'] = var_30d
        linha['Var. Trimestre (%)'] = var_90d
        linha['Var. Semestre (%)'] = var_180d
        linha['Var. 365 Dias (%)'] = var_365d_full
        linha['Var. Ano (%)'] = var_ano
        
        relatorio.append(linha)
        
        progress_bar.progress((i + 1) / len(lista_acoes))
        time.sleep(0.5)  # Evitar rate limiting
    
    progress_bar.empty()
    status_text.empty()
    
    return pd.DataFrame(relatorio)

# Área principal
if not st.session_state.data["stocks"]:
    st.info("👈 Comece cadastrando ações na área de Gerenciamento!")
else:
    # Tabs para organizar conteúdo
    tab1, tab2, tab3 = st.tabs(["📊 Análise Individual", "📋 Relatório Comparativo", "⚙️ Gerenciamento"])
    
    with tab2:
        st.header("📋 Relatório Comparativo de Ações")
        st.write("Compare o desempenho de todas as ações cadastradas")
        
        # Seleção de categoria
        col1, col2 = st.columns([3, 1])
        with col1:
            categories_available = st.session_state.data.get("category_list", [])
            if categories_available:
                selected_categories = st.multiselect(
                    "Selecione as Categorias:",
                    options=["Todas"] + categories_available,
                    default=["Todas"],
                    key="selected_categories"
                )
            else:
                selected_categories = ["Todas"]
                st.info("Nenhuma categoria cadastrada. Mostrando todas as ações.")
        
        with col2:
            st.write("")  # Espaçamento
        
        if st.button("🔄 Gerar Relatório", type="primary"):
            with st.spinner("Gerando relatório comparativo..."):
                categories_dict = st.session_state.data.get("categories", {})
                
                # Filtrar ações por categoria selecionada
                if "Todas" in selected_categories or not categories_available:
                    acoes_filtradas = st.session_state.data["stocks"]
                else:
                    acoes_filtradas = [
                        stock for stock in st.session_state.data["stocks"]
                        if categories_dict.get(stock, "Sem categoria") in selected_categories
                    ]
                
                if acoes_filtradas:
                    df_relatorio = gerar_relatorio_comparativo(acoes_filtradas, categories_dict)
                    st.session_state.relatorio = df_relatorio
                else:
                    st.warning("⚠️ Nenhuma ação encontrada nas categorias selecionadas!")
        
        if 'relatorio' in st.session_state and not st.session_state.relatorio.empty:
            df = st.session_state.relatorio
            
            # Opções de ordenação
            col1, col2 = st.columns([3, 1])
            with col1:
                coluna_ordenacao = st.selectbox(
                    "Ordenar por:",
                    ['Var. Dia (%)', 'Var. 7 Dias (%)', 'Var. 30 Dias (%)', 
                     'Var. Trimestre (%)', 'Var. Semestre (%)', 'Var. 365 Dias (%)', 'Var. Ano (%)', 'Preço Atual'],
                    key="ordem_col"
                )
            with col2:
                ordem_crescente = st.checkbox("Crescente", value=False, key="ordem_cresc")
            
            # Ordenar dataframe
            df_ordenado = df.sort_values(by=coluna_ordenacao, ascending=ordem_crescente, na_position='last')
            
            # Estilizar dataframe
            def colorir_celulas(val):
                if pd.isna(val):
                    return ''
                if isinstance(val, (int, float)):
                    if val > 0:
                        return 'background-color: #90EE90; color: #006400'
                    elif val < 0:
                        return 'background-color: #FFB6C1; color: #8B0000'
                return ''
            
            # Função para formatar preço com símbolo correto
            def format_price(row):
                if pd.isna(row['Preço Atual']):
                    return 'N/A'
                symbol = 'R$' if '.SA' in str(row['Ação']).upper() else '$'
                return f'{symbol}{row["Preço Atual"]:.2f}'
            
            # Aplicar formatação de preço
            df_ordenado['Preço Formatado'] = df_ordenado.apply(format_price, axis=1)
            
            # Reordenar colunas para mostrar preço formatado
            cols = df_ordenado.columns.tolist()
            cols.remove('Preço Formatado')
            cols.insert(cols.index('Preço Atual'), 'Preço Formatado')
            df_display = df_ordenado[cols].copy()
            df_display = df_display.drop('Preço Atual', axis=1)
            df_display = df_display.rename(columns={'Preço Formatado': 'Preço Atual'})
            
            # Aplicar formatação
            df_styled = df_display.style.map(
                colorir_celulas, 
                subset=['Var. Dia (%)', 'Var. 7 Dias (%)', 'Var. 30 Dias (%)', 
                        'Var. Trimestre (%)', 'Var. Semestre (%)', 'Var. 365 Dias (%)', 'Var. Ano (%)']
            ).format({
                'Var. Dia (%)': '{:.2f}%',
                'Var. 7 Dias (%)': '{:.2f}%',
                'Var. 30 Dias (%)': '{:.2f}%',
                'Var. Trimestre (%)': '{:.2f}%',
                'Var. Semestre (%)': '{:.2f}%',
                'Var. 365 Dias (%)': '{:.2f}%',
                'Var. Ano (%)': '{:.2f}%'
            }, na_rep='N/A')
            
            st.dataframe(df_styled, use_container_width=True, height=400)
            
            # Estatísticas resumidas
            st.subheader("📊 Estatísticas do Relatório")
            
            # Estatísticas Gerais
            st.write("### 🏆 Melhores Performances (Geral)")
            col1, col2, col3, col4, col5, col6 = st.columns(6)
            
            with col1:
                melhor_dia = df.loc[df['Var. Dia (%)'].idxmax()] if not df['Var. Dia (%)'].isna().all() else None
                if melhor_dia is not None:
                    st.metric("Dia", melhor_dia['Ação'], f"+{melhor_dia['Var. Dia (%)']:.2f}%")
            
            with col2:
                melhor_semana = df.loc[df['Var. 7 Dias (%)'].idxmax()] if not df['Var. 7 Dias (%)'].isna().all() else None
                if melhor_semana is not None:
                    st.metric("Semana", melhor_semana['Ação'], f"+{melhor_semana['Var. 7 Dias (%)']:.2f}%")
            
            with col3:
                melhor_mes = df.loc[df['Var. 30 Dias (%)'].idxmax()] if not df['Var. 30 Dias (%)'].isna().all() else None
                if melhor_mes is not None:
                    st.metric("Mês", melhor_mes['Ação'], f"+{melhor_mes['Var. 30 Dias (%)']:.2f}%")
            
            with col4:
                melhor_trimestre = df.loc[df['Var. Trimestre (%)'].idxmax()] if not df['Var. Trimestre (%)'].isna().all() else None
                if melhor_trimestre is not None:
                    st.metric("Trimestre", melhor_trimestre['Ação'], f"+{melhor_trimestre['Var. Trimestre (%)']:.2f}%")
            
            with col5:
                melhor_semestre = df.loc[df['Var. Semestre (%)'].idxmax()] if not df['Var. Semestre (%)'].isna().all() else None
                if melhor_semestre is not None:
                    st.metric("Semestre", melhor_semestre['Ação'], f"+{melhor_semestre['Var. Semestre (%)']:.2f}%")
            
            with col6:
                melhor_ano = df.loc[df['Var. Ano (%)'].idxmax()] if not df['Var. Ano (%)'].isna().all() else None
                if melhor_ano is not None:
                    st.metric("Ano", melhor_ano['Ação'], f"+{melhor_ano['Var. Ano (%)']:.2f}%")
            
            st.markdown("---")
            
            # Piores Gerais
            st.write("### � Piores Performances (Geral)")
            col1, col2, col3, col4, col5, col6 = st.columns(6)
            
            with col1:
                pior_dia = df.loc[df['Var. Dia (%)'].idxmin()] if not df['Var. Dia (%)'].isna().all() else None
                if pior_dia is not None:
                    st.metric("Dia", pior_dia['Ação'], f"{pior_dia['Var. Dia (%)']:.2f}%")
            
            with col2:
                pior_semana = df.loc[df['Var. 7 Dias (%)'].idxmin()] if not df['Var. 7 Dias (%)'].isna().all() else None
                if pior_semana is not None:
                    st.metric("Semana", pior_semana['Ação'], f"{pior_semana['Var. 7 Dias (%)']:.2f}%")
            
            with col3:
                pior_mes = df.loc[df['Var. 30 Dias (%)'].idxmin()] if not df['Var. 30 Dias (%)'].isna().all() else None
                if pior_mes is not None:
                    st.metric("Mês", pior_mes['Ação'], f"{pior_mes['Var. 30 Dias (%)']:.2f}%")
            
            with col4:
                pior_trimestre = df.loc[df['Var. Trimestre (%)'].idxmin()] if not df['Var. Trimestre (%)'].isna().all() else None
                if pior_trimestre is not None:
                    st.metric("Trimestre", pior_trimestre['Ação'], f"{pior_trimestre['Var. Trimestre (%)']:.2f}%")
            
            with col5:
                pior_semestre = df.loc[df['Var. Semestre (%)'].idxmin()] if not df['Var. Semestre (%)'].isna().all() else None
                if pior_semestre is not None:
                    st.metric("Semestre", pior_semestre['Ação'], f"{pior_semestre['Var. Semestre (%)']:.2f}%")
            
            with col6:
                pior_ano = df.loc[df['Var. Ano (%)'].idxmin()] if not df['Var. Ano (%)'].isna().all() else None
                if pior_ano is not None:
                    st.metric("Ano", pior_ano['Ação'], f"{pior_ano['Var. Ano (%)']:.2f}%")
            
            # Estatísticas por Categoria
            if 'Categoria' in df.columns:
                categorias_unicas = df['Categoria'].unique()
                categorias_com_dados = [cat for cat in categorias_unicas if cat != 'Sem categoria']
                
                if categorias_com_dados:
                    st.markdown("---")
                    st.write("### 📂 Estatísticas por Categoria")
                    
                    for categoria in categorias_com_dados:
                        df_cat = df[df['Categoria'] == categoria]
                        
                        if len(df_cat) > 0:
                            st.write(f"#### 🏷️ {categoria}")
                            
                            col1, col2, col3 = st.columns(3)
                            
                            with col1:
                                st.write("**Melhores:**")
                                melhor_dia_cat = df_cat.loc[df_cat['Var. Dia (%)'].idxmax()] if not df_cat['Var. Dia (%)'].isna().all() else None
                                if melhor_dia_cat is not None:
                                    st.metric("Dia", melhor_dia_cat['Ação'], f"+{melhor_dia_cat['Var. Dia (%)']:.2f}%")
                                
                                melhor_semana_cat = df_cat.loc[df_cat['Var. 7 Dias (%)'].idxmax()] if not df_cat['Var. 7 Dias (%)'].isna().all() else None
                                if melhor_semana_cat is not None:
                                    st.metric("Semana", melhor_semana_cat['Ação'], f"+{melhor_semana_cat['Var. 7 Dias (%)']:.2f}%")
                            
                            with col2:
                                st.write("**Médias:**")
                                media_dia = df_cat['Var. Dia (%)'].mean()
                                if not pd.isna(media_dia):
                                    st.metric("Dia", "Média", f"{media_dia:.2f}%")
                                
                                media_semana = df_cat['Var. 7 Dias (%)'].mean()
                                if not pd.isna(media_semana):
                                    st.metric("Semana", "Média", f"{media_semana:.2f}%")
                            
                            with col3:
                                st.write("**Piores:**")
                                pior_dia_cat = df_cat.loc[df_cat['Var. Dia (%)'].idxmin()] if not df_cat['Var. Dia (%)'].isna().all() else None
                                if pior_dia_cat is not None:
                                    st.metric("Dia", pior_dia_cat['Ação'], f"{pior_dia_cat['Var. Dia (%)']:.2f}%")
                                
                                pior_semana_cat = df_cat.loc[df_cat['Var. 7 Dias (%)'].idxmin()] if not df_cat['Var. 7 Dias (%)'].isna().all() else None
                                if pior_semana_cat is not None:
                                    st.metric("Semana", pior_semana_cat['Ação'], f"{pior_semana_cat['Var. 7 Dias (%)']:.2f}%")
                            
                            st.markdown("---")
            
            # Download Excel
            from io import BytesIO
            buffer = BytesIO()
            
            # Preparar DataFrame para Excel com preços formatados
            df_excel = df_ordenado.copy()
            df_excel['Preço Atual'] = df_excel.apply(
                lambda row: f"{'R$' if '.SA' in str(row['Ação']).upper() else '$'}{row['Preço Atual']:.2f}" if not pd.isna(row['Preço Atual']) else 'N/A',
                axis=1
            )
            
            with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                df_excel.to_excel(writer, index=False, sheet_name='Relatório')
            buffer.seek(0)
            
            st.download_button(
                label="📥 Download Relatório (Excel)",
                data=buffer,
                file_name=f"relatorio_acoes_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
    
    with tab1:
        st.header("📊 Análise Individual de Ação")
        
        # Seleção de ação
        col1, col2 = st.columns([2, 1])
        
        with col1:
            selected_stock = st.selectbox(
                "Selecione uma Ação",
                st.session_state.data["stocks"],
                key="stock_selector"
            )
        
        with col2:
            selected_period = st.selectbox(
                "Período",
                list(PERIOD_OPTIONS.keys()),
                key="period_selector"
            )
        
        if selected_stock:
            # Mostrar preço atual primeiro (delay de 10 min)
            st.subheader(f"💹 Preço Atual - {selected_stock}")
            current_price_data = get_current_price(selected_stock)
            
            if current_price_data:
                col1, col2, col3, col4, col5 = st.columns(5)
                
                price_change = current_price_data['price'] - current_price_data['previous_close']
                price_change_pct = (price_change / current_price_data['previous_close']) * 100 if current_price_data['previous_close'] != 0 else 0
                
                currency_symbol = 'R$' if current_price_data.get('currency') == 'BRL' else '$'
                
                with col1:
                    st.metric(
                        "Preço", 
                        f"{currency_symbol}{current_price_data['price']:.2f}",
                        f"{price_change:+.2f} ({price_change_pct:+.2f}%)"
                    )
                
                with col2:
                    st.metric("Abertura", f"{currency_symbol}{current_price_data['open']:.2f}")
                
                with col3:
                    st.metric("Máxima do Dia", f"{currency_symbol}{current_price_data['day_high']:.2f}")
                
                with col4:
                    st.metric("Mínima do Dia", f"{currency_symbol}{current_price_data['day_low']:.2f}")
                
                with col5:
                    st.metric("Volume", f"{current_price_data['volume']:,.0f}")
                
                st.caption(f"⏰ Dados com delay de ~10 minutos | Atualizado: {current_price_data['timestamp'].strftime('%H:%M:%S')}")
            else:
                st.warning("⚠️ Não foi possível obter preço atual")
            
            # Variações padronizadas (mesmas do relatório comparativo)
            st.subheader("📊 Variações de Preço")
            with st.spinner("Calculando variações..."):
                var_1d, _ = calcular_variacao(selected_stock, 1)
                var_7d, _ = calcular_variacao(selected_stock, 7)
                var_30d, _ = calcular_variacao(selected_stock, 30)
                var_90d, _ = calcular_variacao(selected_stock, 90)
                var_180d, _ = calcular_variacao(selected_stock, 180)
                var_365d, _ = calcular_variacao(selected_stock, 365)
                var_ano, _ = calcular_variacao_ytd(selected_stock)
                
                col1, col2, col3, col4 = st.columns(4)
                
                with col1:
                    if var_1d is not None:
                        st.metric("Var. Dia", f"{var_1d:+.2f}%")
                    else:
                        st.metric("Var. Dia", "N/A")
                    
                    if var_90d is not None:
                        st.metric("Var. Trimestre", f"{var_90d:+.2f}%")
                    else:
                        st.metric("Var. Trimestre", "N/A")
                
                with col2:
                    if var_7d is not None:
                        st.metric("Var. 7 Dias", f"{var_7d:+.2f}%")
                    else:
                        st.metric("Var. 7 Dias", "N/A")
                    
                    if var_180d is not None:
                        st.metric("Var. Semestre", f"{var_180d:+.2f}%")
                    else:
                        st.metric("Var. Semestre", "N/A")
                
                with col3:
                    if var_30d is not None:
                        st.metric("Var. 30 Dias", f"{var_30d:+.2f}%")
                    else:
                        st.metric("Var. 30 Dias", "N/A")
                    
                    if var_365d is not None:
                        st.metric("Var. 365 Dias", f"{var_365d:+.2f}%")
                    else:
                        st.metric("Var. 365 Dias", "N/A")
                
                with col4:
                    st.write("")  # Espaçamento
                    if var_ano is not None:
                        st.metric("Var. Ano (YTD)", f"{var_ano:+.2f}%")
                    else:
                        st.metric("Var. Ano (YTD)", "N/A")
            
            st.caption("💡 Variações calculadas usando os mesmos critérios do Relatório Comparativo")
            
            st.markdown("---")
            
            try:
                period_value = PERIOD_OPTIONS[selected_period]
                cache_key = get_cache_key(selected_stock, period_value)
                
                # Verificar cache primeiro
                if cache_key in st.session_state.cache and is_cache_valid(st.session_state.cache[cache_key]):
                    st.info("📦 Carregando dados do cache (dados atualizados nos últimos 5 minutos)")
                    cached_data = st.session_state.cache[cache_key]
                    
                    # Reconstruir DataFrame do cache
                    df = pd.DataFrame(cached_data['data'])
                    if 'Date' in df.columns:
                        df['Date'] = pd.to_datetime(df['Date'])
                        df.set_index('Date', inplace=True)
                    
                    info = cached_data.get('info', {})
                else:
                    # Rate limiting - garantir intervalo mínimo entre requisições
                    time_since_last_request = time.time() - st.session_state.last_request_time
                    if time_since_last_request < REQUEST_DELAY:
                        wait_time = REQUEST_DELAY - time_since_last_request
                        st.info(f"⏳ Aguardando {wait_time:.1f}s para evitar limite de requisições...")
                        time.sleep(wait_time)
                    
                    # Buscar dados com retry
                    with st.spinner(f"🔄 Buscando dados de {selected_stock}..."):
                        ticker = yf.Ticker(selected_stock)
                        df, error = fetch_stock_data_with_retry(ticker, period_value)
                        st.session_state.last_request_time = time.time()
                        
                        if error:
                            st.error(f"❌ {error}")
                            if "429" in error or "Too Many" in error:
                                st.warning("⚠️ Limite de requisições do Yahoo Finance atingido.")
                                st.info("💡 Aguarde alguns minutos antes de tentar novamente, ou use dados do cache se disponíveis.")
                            elif "Crumb" in error or "Unauthorized" in error:
                                st.warning("⚠️ Erro de autenticação do Yahoo Finance.")
                                st.info("💡 Este erro é temporário. Tente novamente em alguns segundos.")
                            df = pd.DataFrame()
                        
                        # Buscar info apenas se df não estiver vazio
                        if not df.empty:
                            info = fetch_ticker_info_safe(ticker)
                            
                            # Salvar no cache - resetar índice para evitar erro com Timestamp
                            df_cache = df.reset_index()
                            df_cache['Date'] = df_cache['Date'].astype(str)
                            
                            st.session_state.cache[cache_key] = {
                                'data': df_cache.to_dict('records'),
                                'index': df.index.astype(str).tolist(),
                                'info': info,
                                'timestamp': time.time()
                            }
                            save_cache(st.session_state.cache)
                        else:
                            info = {}
                
                if df.empty:
                    st.error(f"❌ Não foi possível obter dados para {selected_stock} no período selecionado.")
                    st.info("💡 Dicas: Verifique se o ticker está correto (ex: AAPL, MSFT, PETR4.SA para ações brasileiras)")
                else:
                    # Garantir que as colunas necessárias existam
                    if 'Dividends' not in df.columns:
                        df['Dividends'] = 0
                    if 'Stock Splits' not in df.columns:
                        df['Stock Splits'] = 0
                    
                    # Informações principais
                    currency_symbol = 'R$' if '.SA' in selected_stock.upper() else '$'
                    
                    col1, col2, col3, col4 = st.columns(4)
                    
                    with col1:
                        st.metric(
                            "Preço Atual", 
                            f"{currency_symbol}{df['Close'].iloc[-1]:.2f}" if not df.empty else "N/A",
                            f"{((df['Close'].iloc[-1] - df['Close'].iloc[0]) / df['Close'].iloc[0] * 100):.2f}%" if len(df) > 1 else "0%"
                        )
                    
                    with col2:
                        st.metric("Máxima do Período", f"{currency_symbol}{df['High'].max():.2f}" if not df.empty else "N/A")
                    
                    with col3:
                        st.metric("Mínima do Período", f"{currency_symbol}{df['Low'].min():.2f}" if not df.empty else "N/A")
                
                with col4:
                    st.metric("Volume Médio", f"{df['Volume'].mean():,.0f}" if not df.empty else "N/A")
                
                st.markdown("---")
                
                # Gráfico de Candlestick
                st.subheader(f"📊 Gráfico de {selected_stock} - {selected_period}")
                
                fig = go.Figure(data=[go.Candlestick(
                    x=df.index,
                    open=df['Open'],
                    high=df['High'],
                    low=df['Low'],
                    close=df['Close'],
                    name=selected_stock
                )])
                
                currency_name = 'BRL' if '.SA' in selected_stock.upper() else 'USD'
                
                fig.update_layout(
                    title=f"{selected_stock} - {selected_period}",
                    yaxis_title=f"Preço ({currency_name})",
                    xaxis_title="Data",
                    height=500,
                    template="plotly_white",
                    xaxis_rangeslider_visible=False
                )
                
                st.plotly_chart(fig, use_container_width=True)
                
                # Gráfico de Volume
                st.subheader("📊 Volume de Negociação")
                
                fig_volume = go.Figure(data=[go.Bar(
                    x=df.index,
                    y=df['Volume'],
                    name="Volume",
                    marker_color='lightblue'
                )])
                
                fig_volume.update_layout(
                    title=f"Volume de {selected_stock}",
                    yaxis_title="Volume",
                    xaxis_title="Data",
                    height=300,
                    template="plotly_white"
                )
                
                st.plotly_chart(fig_volume, use_container_width=True)
                
                st.markdown("---")
                
                # Tabela de dados com colunas selecionadas
                st.subheader("📋 Dados Históricos")
                
                # Aplicar filtros
                df_filtered = df.copy()
                active_filters = []
                
                if st.session_state.data["filters"]:
                    st.write("**Filtros Ativos:**")
                    
                    for filter_name, filter_config in st.session_state.data["filters"].items():
                        if st.checkbox(filter_name, key=f"apply_{filter_name}"):
                            active_filters.append(filter_name)
                            field = filter_config["field"]
                            operator = filter_config["operator"]
                            value = filter_config["value"]
                            
                            if field in df_filtered.columns:
                                if operator == ">":
                                    df_filtered = df_filtered[df_filtered[field] > value]
                                elif operator == "<":
                                    df_filtered = df_filtered[df_filtered[field] < value]
                                elif operator == ">=":
                                    df_filtered = df_filtered[df_filtered[field] >= value]
                                elif operator == "<=":
                                    df_filtered = df_filtered[df_filtered[field] <= value]
                                elif operator == "==":
                                    df_filtered = df_filtered[df_filtered[field] == value]
                
                # Filtrar colunas selecionadas
                available_columns = [col for col in st.session_state.data["selected_columns"] if col in df_filtered.columns]
                
                if available_columns:
                    df_display = df_filtered[available_columns].copy()
                    df_display.index = df_display.index.strftime('%Y-%m-%d %H:%M:%S')
                    
                    st.write(f"**Total de registros:** {len(df_display)}")
                    if active_filters:
                        st.write(f"**Filtros aplicados:** {', '.join(active_filters)}")
                    
                    st.dataframe(
                        df_display.style.format("{:.2f}"),
                        use_container_width=True,
                        height=400
                    )
                    
                    # Download dos dados
                    csv = df_display.to_csv()
                    st.download_button(
                        label="📥 Download CSV",
                        data=csv,
                        file_name=f"{selected_stock}_{selected_period}_{datetime.now().strftime('%Y%m%d')}.csv",
                        mime="text/csv"
                    )
                else:
                    st.warning("⚠️ Selecione pelo menos uma coluna para exibir na tabela.")
                
                # Informações adicionais da empresa
                st.markdown("---")
                st.subheader("ℹ️ Informações da Empresa")
                
                col1, col2, col3 = st.columns(3)
                
                with col1:
                    st.write(f"**Nome:** {info.get('longName', 'N/A')}")
                    st.write(f"**Setor:** {info.get('sector', 'N/A')}")
                    st.write(f"**Indústria:** {info.get('industry', 'N/A')}")
                
                with col2:
                    st.write(f"**País:** {info.get('country', 'N/A')}")
                    st.write(f"**Moeda:** {info.get('currency', 'N/A')}")
                    st.write(f"**Exchange:** {info.get('exchange', 'N/A')}")
                
                with col3:
                    st.write(f"**Market Cap:** ${info.get('marketCap', 0):,.0f}" if info.get('marketCap') else "N/A")
                    st.write(f"**P/E Ratio:** {info.get('trailingPE', 'N/A')}")
                    st.write(f"**Dividend Yield:** {info.get('dividendYield', 'N/A')}")
            
            except Exception as e:
                st.error(f"❌ Erro ao carregar dados: {str(e)}")
            st.info("💡 Possíveis soluções:")
            st.markdown("""
            - Verifique se o ticker está correto
            - Para ações brasileiras, adicione .SA (ex: PETR4.SA)
            - Verifique sua conexão com a internet
            - Tente um período diferente
            - Alguns tickers podem ter mudado ou a empresa pode ter sido removida da bolsa
            """)
            
            # Mostrar sugestões de tickers populares
            with st.expander("📋 Exemplos de Tickers Válidos"):
                st.write("**🇺🇸 Ações Americanas:**")
                st.write("AAPL (Apple), MSFT (Microsoft), GOOGL (Google), AMZN (Amazon), TSLA (Tesla)")
                st.write("\n**🇧🇷 Ações Brasileiras:**")
                st.write("PETR4.SA (Petrobras), VALE3.SA (Vale), ITUB4.SA (Itaú), BBDC4.SA (Bradesco)")
                st.write("\n**ADRs (Empresas brasileiras na bolsa americana):**")
                st.write("PBR (Petrobras ADR), VALE (Vale ADR), ITUB (Itaú ADR)")
    
    with tab3:
        st.header("⚙️ Gerenciamento de Ações e Categorias")
        
        # Criar duas colunas para Ações e Categorias
        col_acoes, col_categorias = st.columns(2)
        
        # Coluna de Gerenciamento de Ações
        with col_acoes:
            st.subheader("📊 Gerenciamento de Ações")
            
            # Adicionar nova ação
            st.write("**➕ Adicionar Nova Ação:**")
            new_stock = st.text_input(
                "Ticker da Ação",
                placeholder="Ex: AAPL, MSFT, PETR4.SA",
                key="manage_new_stock"
            ).upper()
            
            if st.button("Adicionar Ação", type="primary", key="manage_add_stock"):
                if new_stock and new_stock not in st.session_state.data["stocks"]:
                    st.session_state.data["stocks"].append(new_stock)
                    if save_data(st.session_state.data):
                        st.success(f"✅ Ação {new_stock} adicionada!")
                        st.rerun()
                    else:
                        st.error("❌ Erro ao salvar a ação.")
                elif new_stock in st.session_state.data["stocks"]:
                    st.warning("⚠️ Ação já cadastrada!")
                else:
                    st.warning("⚠️ Digite um ticker válido!")
            
            st.markdown("---")
            
            # Lista de ações com edição
            if st.session_state.data["stocks"]:
                st.write(f"**Ações Cadastradas ({len(st.session_state.data['stocks'])}):**")
                
                # Criar DataFrame para exibição
                acoes_data = []
                for stock in st.session_state.data["stocks"]:
                    categoria = st.session_state.data.get("categories", {}).get(stock, "Sem categoria")
                    acoes_data.append({"Ticker": stock, "Categoria": categoria})
                
                df_acoes = pd.DataFrame(acoes_data)
                
                # Exibir tabela
                st.dataframe(df_acoes, use_container_width=True, hide_index=True)
                
                st.markdown("---")
                
                # Editar ação
                st.write("**✏️ Editar Ação:**")
                stock_to_edit = st.selectbox(
                    "Selecione a ação para editar",
                    st.session_state.data["stocks"],
                    key="edit_stock_select"
                )
                
                if stock_to_edit:
                    new_ticker = st.text_input(
                        "Novo Ticker",
                        value=stock_to_edit,
                        key="edit_stock_ticker"
                    ).upper()
                    
                    # Seleção de categoria
                    current_cat = st.session_state.data.get("categories", {}).get(stock_to_edit, "Sem categoria")
                    categories_options = ["Sem categoria"] + st.session_state.data.get("category_list", [])
                    cat_index = 0 if current_cat == "Sem categoria" else (categories_options.index(current_cat) if current_cat in categories_options else 0)
                    
                    new_category = st.selectbox(
                        "Categoria",
                        categories_options,
                        index=cat_index,
                        key="edit_stock_category"
                    )
                    
                    col1, col2 = st.columns(2)
                    
                    with col1:
                        if st.button("💾 Salvar Alterações", type="primary", key="save_edit_stock"):
                            # Atualizar ticker
                            if new_ticker != stock_to_edit:
                                if new_ticker not in st.session_state.data["stocks"]:
                                    idx = st.session_state.data["stocks"].index(stock_to_edit)
                                    st.session_state.data["stocks"][idx] = new_ticker
                                    
                                    # Atualizar categoria se existir
                                    if "categories" in st.session_state.data and stock_to_edit in st.session_state.data["categories"]:
                                        old_cat = st.session_state.data["categories"][stock_to_edit]
                                        del st.session_state.data["categories"][stock_to_edit]
                                        if new_category != "Sem categoria":
                                            st.session_state.data["categories"][new_ticker] = new_category
                                        elif old_cat != "Sem categoria":
                                            st.session_state.data["categories"][new_ticker] = old_cat
                                else:
                                    st.error(f"❌ Ticker {new_ticker} já existe!")
                                    st.stop()
                            
                            # Atualizar categoria
                            if "categories" not in st.session_state.data:
                                st.session_state.data["categories"] = {}
                            
                            if new_category == "Sem categoria":
                                if new_ticker in st.session_state.data["categories"]:
                                    del st.session_state.data["categories"][new_ticker]
                            else:
                                st.session_state.data["categories"][new_ticker] = new_category
                            
                            if save_data(st.session_state.data):
                                st.success("✅ Ação atualizada!")
                                st.rerun()
                            else:
                                st.error("❌ Erro ao salvar alterações.")
                    
                    with col2:
                        if st.button("🗑️ Excluir Ação", type="secondary", key="delete_edit_stock"):
                            st.session_state.data["stocks"].remove(stock_to_edit)
                            if "categories" in st.session_state.data and stock_to_edit in st.session_state.data["categories"]:
                                del st.session_state.data["categories"][stock_to_edit]
                            if save_data(st.session_state.data):
                                st.success(f"✅ {stock_to_edit} removida!")
                                st.rerun()
            else:
                st.info("Nenhuma ação cadastrada ainda.")
        
        # Coluna de Gerenciamento de Categorias
        with col_categorias:
            st.subheader("🏷️ Gerenciamento de Categorias")
            
            # Adicionar nova categoria
            st.write("**➕ Adicionar Nova Categoria:**")
            new_category = st.text_input(
                "Nome da Categoria",
                placeholder="Ex: Tecnologia, Financeiro, Energia",
                key="manage_new_category"
            )
            
            if st.button("Adicionar Categoria", type="primary", key="manage_add_category"):
                if new_category and new_category not in st.session_state.data.get("category_list", []):
                    if "category_list" not in st.session_state.data:
                        st.session_state.data["category_list"] = []
                    st.session_state.data["category_list"].append(new_category)
                    if save_data(st.session_state.data):
                        st.success(f"✅ Categoria '{new_category}' adicionada!")
                        st.rerun()
                    else:
                        st.error("❌ Erro ao salvar a categoria.")
                elif new_category in st.session_state.data.get("category_list", []):
                    st.warning("⚠️ Categoria já existe!")
                else:
                    st.warning("⚠️ Digite um nome para a categoria!")
            
            st.markdown("---")
            
            # Lista de categorias
            if st.session_state.data.get("category_list"):
                st.write(f"**Categorias Cadastradas ({len(st.session_state.data['category_list'])}):**")
                
                # Contar ações por categoria
                categorias_info = []
                for cat in st.session_state.data["category_list"]:
                    count = sum(1 for stock, stock_cat in st.session_state.data.get("categories", {}).items() if stock_cat == cat)
                    categorias_info.append({"Categoria": cat, "Ações": count})
                
                df_categorias = pd.DataFrame(categorias_info)
                st.dataframe(df_categorias, use_container_width=True, hide_index=True)
                
                st.markdown("---")
                
                # Editar categoria
                st.write("**✏️ Editar Categoria:**")
                category_to_edit = st.selectbox(
                    "Selecione a categoria para editar",
                    st.session_state.data["category_list"],
                    key="edit_category_select"
                )
                
                if category_to_edit:
                    new_cat_name = st.text_input(
                        "Novo Nome",
                        value=category_to_edit,
                        key="edit_category_name"
                    )
                    
                    col1, col2 = st.columns(2)
                    
                    with col1:
                        if st.button("💾 Salvar Alterações", type="primary", key="save_edit_category"):
                            if new_cat_name != category_to_edit:
                                if new_cat_name not in st.session_state.data["category_list"]:
                                    # Atualizar nome da categoria
                                    idx = st.session_state.data["category_list"].index(category_to_edit)
                                    st.session_state.data["category_list"][idx] = new_cat_name
                                    
                                    # Atualizar referências nas ações
                                    if "categories" in st.session_state.data:
                                        for stock, cat in st.session_state.data["categories"].items():
                                            if cat == category_to_edit:
                                                st.session_state.data["categories"][stock] = new_cat_name
                                    
                                    if save_data(st.session_state.data):
                                        st.success("✅ Categoria atualizada!")
                                        st.rerun()
                                    else:
                                        st.error("❌ Erro ao salvar alterações.")
                                else:
                                    st.error(f"❌ Categoria '{new_cat_name}' já existe!")
                            else:
                                st.info("ℹ️ Nenhuma alteração feita.")
                    
                    with col2:
                        if st.button("🗑️ Excluir Categoria", type="secondary", key="delete_edit_category"):
                            # Remover categoria
                            st.session_state.data["category_list"].remove(category_to_edit)
                            
                            # Remover associações
                            if "categories" in st.session_state.data:
                                stocks_to_update = [stock for stock, cat in st.session_state.data["categories"].items() if cat == category_to_edit]
                                for stock in stocks_to_update:
                                    del st.session_state.data["categories"][stock]
                            
                            if save_data(st.session_state.data):
                                st.success(f"✅ Categoria '{category_to_edit}' removida!")
                                st.rerun()
            else:
                st.info("Nenhuma categoria cadastrada ainda.")

# Rodapé
st.markdown("---")
st.caption("📊 Sistema de Análise de Ações | Dados fornecidos por Yahoo Finance")
