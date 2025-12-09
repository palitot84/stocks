import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime, timedelta
import json
import os
import time
from functools import lru_cache
import ssl
import certifi

# Configuração da página
st.set_page_config(page_title="Análise de Ações", layout="wide", page_icon="📈")

# Arquivo para persistência de dados
DATA_FILE = "stocks_data.json"
CACHE_FILE = "stocks_cache.json"
CACHE_DURATION = 300  # Cache duration in seconds (5 minutes)
REQUEST_DELAY = 2  # Delay between requests in seconds

# Configuração de certificado SSL para redes corporativas
CERT_FILE = "petrobras-ca-root.pem"

def setup_ssl_cert():
    """Configura certificado SSL customizado se disponível"""
    if os.path.exists(CERT_FILE):
        try:
            # Configurar variáveis de ambiente para requests/urllib
            os.environ['REQUESTS_CA_BUNDLE'] = os.path.abspath(CERT_FILE)
            os.environ['CURL_CA_BUNDLE'] = os.path.abspath(CERT_FILE)
            os.environ['SSL_CERT_FILE'] = os.path.abspath(CERT_FILE)
            return True
        except Exception as e:
            st.warning(f"⚠️ Não foi possível configurar certificado SSL: {e}")
            return False
    return False

# Tentar configurar SSL no início
ssl_configured = setup_ssl_cert()

# Inicializar dados
def load_data():
    """Carrega dados salvos do arquivo JSON"""
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {
        "stocks": [],
        "filters": {},
        "selected_columns": [
            "Open", "High", "Low", "Close", "Volume", 
            "Dividends", "Stock Splits"
        ]
    }

def save_data(data):
    """Salva dados no arquivo JSON"""
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

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
        
        # Reconfigurar SSL antes de cada requisição (para redes corporativas)
        if os.path.exists(CERT_FILE):
            os.environ['REQUESTS_CA_BUNDLE'] = os.path.abspath(CERT_FILE)
        
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
    except ssl.SSLError as e:
        return pd.DataFrame(), f"Erro SSL: {str(e)} - Verifique o certificado {CERT_FILE}"
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

# Carregar dados
if 'data' not in st.session_state:
    st.session_state.data = load_data()

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
    "1 Semana": "7d",
    "1 Mês": "1mo",
    "No Ano (YTD)": "ytd",
    "1 Ano": "1y",
    "3 Anos": "3y",
    "5 Anos": "5y"
}

# Título principal
st.title("📈 Sistema de Análise de Ações")

# Mostrar status do certificado SSL
if ssl_configured:
    st.success(f"🔒 Certificado SSL corporativo configurado: {CERT_FILE}")
else:
    if os.path.exists(CERT_FILE):
        st.warning("⚠️ Certificado encontrado mas não pôde ser configurado")
    else:
        st.info(f"ℹ️ Usando certificados padrão do sistema (não encontrado: {CERT_FILE})")

st.markdown("---")

# Sidebar para configurações
with st.sidebar:
    st.header("⚙️ Configurações")
    
    # Seção de Cadastro de Ações
    st.subheader("📊 Cadastro de Ações")
    
    with st.expander("➕ Adicionar Nova Ação"):
        new_stock = st.text_input(
            "Ticker da Ação", 
            placeholder="Ex: AAPL, MSFT, PETR4.SA",
            key="new_stock_input"
        ).upper()
        
        if st.button("Adicionar Ação", type="primary"):
            if new_stock and new_stock not in st.session_state.data["stocks"]:
                # Adicionar ação sem validação estrita
                st.session_state.data["stocks"].append(new_stock)
                save_data(st.session_state.data)
                st.success(f"✅ Ação {new_stock} adicionada!")
                st.info("💡 Os dados serão carregados ao selecionar a ação.")
                st.rerun()
            elif new_stock in st.session_state.data["stocks"]:
                st.warning("⚠️ Ação já cadastrada!")
            else:
                st.warning("⚠️ Digite um ticker válido!")
    
    # Lista de ações cadastradas
    if st.session_state.data["stocks"]:
        st.write("**Ações Cadastradas:**")
        for stock in st.session_state.data["stocks"]:
            col1, col2 = st.columns([3, 1])
            with col1:
                st.write(f"• {stock}")
            with col2:
                if st.button("🗑️", key=f"del_{stock}"):
                    st.session_state.data["stocks"].remove(stock)
                    save_data(st.session_state.data)
                    st.rerun()
    else:
        st.info("Nenhuma ação cadastrada ainda.")
    
    st.markdown("---")
    
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

# Área principal
if not st.session_state.data["stocks"]:
    st.info("👈 Comece cadastrando ações na barra lateral!")
else:
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
        try:
            period_value = PERIOD_OPTIONS[selected_period]
            cache_key = get_cache_key(selected_stock, period_value)
            
            # Verificar cache primeiro
            if cache_key in st.session_state.cache and is_cache_valid(st.session_state.cache[cache_key]):
                st.info("📦 Carregando dados do cache (dados atualizados nos últimos 5 minutos)")
                cached_data = st.session_state.cache[cache_key]
                df = pd.DataFrame(cached_data['data'])
                df.index = pd.to_datetime(df.index)
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
                        elif "SSL" in error or "certificate" in error.lower():
                            st.warning("⚠️ Erro de certificado SSL.")
                            if not os.path.exists(CERT_FILE):
                                st.error(f"🔒 Certificado não encontrado: {CERT_FILE}")
                                st.info("💡 Verifique se o arquivo petrobras-ca-root.pem está na mesma pasta do app.py")
                            else:
                                st.info("💡 Certificado encontrado mas pode estar inválido ou expirado.")
                        df = pd.DataFrame()
                    
                    # Buscar info apenas se df não estiver vazio
                    if not df.empty:
                        info = fetch_ticker_info_safe(ticker)
                        
                        # Salvar no cache
                        st.session_state.cache[cache_key] = {
                            'data': df.to_dict(),
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
                col1, col2, col3, col4 = st.columns(4)
                
                with col1:
                    st.metric(
                        "Preço Atual", 
                        f"${df['Close'].iloc[-1]:.2f}" if not df.empty else "N/A",
                        f"{((df['Close'].iloc[-1] - df['Close'].iloc[0]) / df['Close'].iloc[0] * 100):.2f}%" if len(df) > 1 else "0%"
                    )
                
                with col2:
                    st.metric("Máxima do Período", f"${df['High'].max():.2f}" if not df.empty else "N/A")
                
                with col3:
                    st.metric("Mínima do Período", f"${df['Low'].min():.2f}" if not df.empty else "N/A")
                
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
                
                fig.update_layout(
                    title=f"{selected_stock} - {selected_period}",
                    yaxis_title="Preço (USD)",
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

# Rodapé
st.markdown("---")
st.caption("📊 Sistema de Análise de Ações | Dados fornecidos por Yahoo Finance")
