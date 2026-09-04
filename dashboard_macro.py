import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd
import numpy as np
import requests
from datetime import datetime
from enum import Enum

try:
    from fredapi import Fred
    FRED_OK = True
except ImportError:
    FRED_OK = False

st.set_page_config(page_title="Sistema Macro IA", page_icon="📊", layout="wide")

st.markdown("""
<style>
    .scenario-badge { display:inline-block; padding:0.8rem 1.5rem; border-radius:25px;
        font-weight:bold; font-size:1.3rem; margin:0.5rem 0; color:white; }
    .alert-box { background-color:#fff3e0; color:#333333; border-left:5px solid #ff6f00;
        padding:1rem; margin:1rem 0; border-radius:5px; }
    .alert-box-crit { background-color:#ffebee; color:#333333; border-left:5px solid #c62828;
        padding:1rem; margin:1rem 0; border-radius:5px; }
    .fonte-badge { display:inline-block; padding:0.4rem 1rem; border-radius:15px;
        font-size:0.9rem; font-weight:bold; }
</style>
""", unsafe_allow_html=True)

class CenarioMacro(Enum):
    EXPANSAO_FORTE = "🚀 Expansão Forte"
    CRESCIMENTO_MODERADO = "📈 Crescimento Moderado"
    DESACELERACAO = "📉 Desaceleração"
    RECESSAO = "⚠️ Recessão"
    ESTAGFLACAO = "🔥 Estagflação"

CONFIG = {
    'PIB': {'ref': 2.0, 'ref_label': 'tendência ~2%'},
    'PCE': {'ref': 2.0, 'ref_label': 'meta do Fed (2%)'},
    'CPI': {'ref': 2.0, 'ref_label': 'meta (2%)'},
    'Payroll': {'ref': 0.0, 'ref_label': 'zero'},
    'PMI': {'ref': 50.0, 'ref_label': 'expansão/contração (50)'},
}

# ================= FONTES DE DADOS =================
def dados_simulados():
    np.random.seed(42)
    meses = 24
    datas = pd.date_range(end=datetime.now(), periods=meses, freq='ME')
    return {
        'PIB': pd.Series(np.linspace(2.0, 2.8, meses) + np.random.normal(0, 0.3, meses), index=datas),
        'PCE': pd.Series(np.linspace(2.5, 4.1, meses) + np.random.normal(0, 0.2, meses), index=datas),
        'Payroll': pd.Series(np.random.normal(180, 60, meses), index=datas),
        'CPI': pd.Series(np.linspace(3.0, 4.6, meses) + np.random.normal(0, 0.3, meses), index=datas),
        'PMI': pd.Series(np.linspace(52, 59, meses) + np.random.normal(0, 2, meses), index=datas),
    }

@st.cache_data(ttl=3600)
def buscar_fred(api_key):
    fred = Fred(api_key=api_key)
    inicio = '2015-01-01'
    dados = {}
    try: dados['PIB'] = fred.get_series('A191RL1Q225SBEA', observation_start=inicio).dropna()
    except Exception: pass
    try: dados['CPI'] = (fred.get_series('CPIAUCSL', observation_start=inicio).pct_change(12) * 100).dropna()
    except Exception: pass
    try: dados['PCE'] = (fred.get_series('PCEPI', observation_start=inicio).pct_change(12) * 100).dropna()
    except Exception: pass
    try: dados['Payroll'] = fred.get_series('PAYEMS', observation_start=inicio).diff().dropna()
    except Exception: pass
    try: dados['PMI'] = fred.get_series('GACDFSA066MSFRBPHI', observation_start=inicio).dropna()
    except Exception: pass
    try: dados['SP500'] = fred.get_series('SP500', observation_start=inicio).dropna()
    except Exception: pass
    return dados

@st.cache_data(ttl=3600)
def buscar_sgs(codigo):
    fim = datetime.now().strftime('%d/%m/%Y')
    url = (f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo}/dados"
           f"?formato=json&dataInicial=01/01/2015&dataFinal={fim}")
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    lista = r.json()
    datas = [pd.to_datetime(i['data'], format='%d/%m/%Y') for i in lista]
    vals = [float(str(i['valor']).replace(',', '.')) for i in lista]
    return pd.Series(vals, index=datas)

def buscar_br(candidatos, vmin, vmax):
    for cod in candidatos:
        try:
            s = buscar_sgs(cod)
            if vmin <= float(s.iloc[-1]) <= vmax:
                return s
        except Exception:
            continue
    return None

@st.cache_data(ttl=1800)
def buscar_bitcoin():
    url = ("https://api.coingecko.com/api/v3/coins/bitcoin/market_chart"
           "?vs_currency=usd&days=365&interval=daily")
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    df = pd.DataFrame(r.json()['prices'], columns=['ts', 'preco'])
    df['data'] = pd.to_datetime(df['ts'], unit='ms')
    return df.set_index('data')['preco']

def enviar_telegram(token, chat_id, texto):
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={'chat_id': chat_id, 'text': texto, 'parse_mode': 'HTML'},
                          timeout=15)
        return r.ok
    except Exception:
        return False

# ================= ANÁLISE =================
def analisar_indicador(serie):
    atual, anterior = serie.iloc[-1], serie.iloc[-2]
    m3, m6 = serie.iloc[-3:].mean(), serie.iloc[-6:].mean()
    var = ((atual - anterior) / abs(anterior)) * 100 if anterior != 0 else 0
    tend = '📈 Alta' if var > 0.5 else ('📉 Queda' if var < -0.5 else '➡️ Estável')
    mom = '🚀 Acelerando' if atual > m3 > m6 else ('⚠️ Desacelerando' if atual < m3 < m6 else '↔️ Misto')
    return {'valor': atual, 'variacao': var, 'tendencia': tend, 'momento': mom}

def detectar_cenario(a):
    pib, cpi = a['PIB']['valor'], a['CPI']['valor']
    if pib < 0 and cpi > 4: return CenarioMacro.ESTAGFLACAO, 0.85
    elif pib < 0: return CenarioMacro.RECESSAO, 0.80
    elif pib > 3.5: return CenarioMacro.EXPANSAO_FORTE, 0.90
    elif pib > 2: return CenarioMacro.CRESCIMENTO_MODERADO, 0.70
    else: return CenarioMacro.DESACELERACAO, 0.65

def gerar_alertas(a):
    al = []
    v = {k: x['valor'] for k, x in a.items()}
    if v['CPI'] > 5: al.append(('CRIT', 'CPI', f"Inflação MUITO alta ({v['CPI']:.1f}%)", 'Risco de juros agressivos'))
    elif v['CPI'] > 3: al.append(('AVISO', 'CPI', f"Inflação acima da meta ({v['CPI']:.1f}% vs 2%)", 'Monitorar o Fed'))
    if v['PCE'] > 4: al.append(('CRIT', 'PCE', f"PCE elevado ({v['PCE']:.1f}%)", 'Indicador preferido do Fed'))
    elif v['PCE'] > 2.5: al.append(('AVISO', 'PCE', f"PCE acima da meta ({v['PCE']:.1f}%)", 'Pressão inflacionária'))
    if v['PMI'] < 45: al.append(('CRIT', 'PMI', f"Indústria em forte contração ({v['PMI']:.0f})", 'Sinal de recessão'))
    elif v['PMI'] < 50: al.append(('AVISO', 'PMI', f"Indústria em contração ({v['PMI']:.0f} < 50)", 'Atividade esfriando'))
    if v['Payroll'] < 0: al.append(('CRIT', 'Payroll', f"Perdendo empregos ({v['Payroll']:.0f} mil)", 'Recessão à vista'))
    elif v['Payroll'] < 100: al.append(('AVISO', 'Payroll', f"Mercado de trabalho fraco ({v['Payroll']:.0f} mil)", 'Desaceleração'))
    if v['PIB'] < 0: al.append(('CRIT', 'PIB', f"PIB negativo ({v['PIB']:.1f}%)", 'Recessão técnica'))
    return al

def prever_serie(serie, h=3):
    y = serie.tail(12).values.astype(float)
    coef = np.polyfit(np.arange(len(y)), y, 1)
    xf = np.arange(len(y), len(y) + h)
    return np.polyval(coef, xf), coef[0]

def fmt(ind, v):
    return f"{v:.0f} mil" if ind == 'Payroll' else f"{v:.2f}"

# ================= INTERFACE =================
st.markdown("# 🤖 Sistema de Análise Macroeconômica")

st.sidebar.header("⚙️ Configurações")
st.sidebar.markdown("### 🔑 Chave FRED (EUA)")
if not FRED_OK:
    st.sidebar.error("Instale: `python -m pip install fredapi`")
api_key = st.sidebar.text_input("Chave FRED:", type="password")

st.sidebar.markdown("### 📱 Telegram")
tg_token = st.sidebar.text_input("Token do Bot:", type="password")
tg_chat = st.sidebar.text_input("Seu Chat ID:")

# Carrega dados EUA
fonte = 'SIM'
dados = dados_simulados()
if FRED_OK and api_key:
    try:
        df_ = buscar_fred(api_key)
        if len(df_) >= 4:
            dados, fonte = df_, 'FRED'
    except Exception:
        st.sidebar.error("Chave FRED inválida. Usando simulação.")

if fonte == 'FRED':
    st.markdown('<span class="fonte-badge" style="background:#1b5e20; color:#c8e6c9;">'
                '🟢 DADOS REAIS — Federal Reserve</span>', unsafe_allow_html=True)
else:
    st.markdown('<span class="fonte-badge" style="background:#5d4037; color:#ffe0b2;">'
                '🟡 MODO SIMULAÇÃO — cole a chave FRED na lateral</span>', unsafe_allow_html=True)

analises = {k: analisar_indicador(dados[k]) for k in ['PIB', 'PCE', 'Payroll', 'CPI', 'PMI']}
cenario, confianca = detectar_cenario(analises)
alertas = gerar_alertas(analises)

# Botões Telegram (CORRIGIDO: if/else clássico)
if tg_token and tg_chat:
    if st.sidebar.button("📨 Testar Telegram"):
        ok = enviar_telegram(tg_token, tg_chat, "✅ <b>Sistema Macro IA</b> conectado com sucesso!")
        if ok:
            st.sidebar.success("✅ Enviado!")
        else:
            st.sidebar.error("❌ Falhou — confira token e chat ID")

    if st.sidebar.button("📊 Enviar Relatório"):
        txt = "📊 <b>RELATÓRIO MACRO IA</b>\n"
        txt += f"🎯 Cenário: {cenario.value}\n"
        for nivel, ind, msg, acao in alertas:
            icone = '🔴' if nivel == 'CRIT' else '🟡'
            txt += f"{icone} {ind}: {msg}\n"
        ok = enviar_telegram(tg_token, tg_chat, txt)
        if ok:
            st.sidebar.success("✅ Relatório enviado!")
        else:
            st.sidebar.error("❌ Falhou — confira token e chat ID")

# ================= ABAS =================
tab_us, tab_crypto, tab_br, tab_ia = st.tabs(["🇺 EUA", "₿ Macro × Crypto", "🇧🇷 Brasil", "🤖 IA Preditiva"])

# ---------- ABA EUA ----------
with tab_us:
    st.markdown("## 🎯 Cenário Atual")
    c1, c2 = st.columns([2, 1])
    with c1:
        cores = {'ESTAGFLACAO': '#d32f2f', 'RECESSAO': '#f44336', 'EXPANSAO_FORTE': '#4caf50',
                 'CRESCIMENTO_MODERADO': '#8bc34a', 'DESACELERACAO': '#ff9800'}
        st.markdown(f'<div class="scenario-badge" style="background-color:{cores.get(cenario.name, "#888")};">'
                    f'{cenario.value}</div>', unsafe_allow_html=True)
        st.markdown(f"**Confiança:** {confianca*100:.0f}%")
    with c2:
        st.metric("Risco", "ALTO" if confianca > 0.7 else "MÉDIO")

    st.markdown("## 🚨 Alertas")
    if alertas:
        for nivel, ind, msg, acao in alertas:
            cl = 'alert-box-crit' if nivel == 'CRIT' else 'alert-box'
            ic = '🔴' if nivel == 'CRIT' else '🟡'
            st.markdown(f'<div class="{cl}">{ic} <b>{nivel} — {ind}</b><br>{msg}<br>'
                        f'<b>Ação:</b> {acao}</div>', unsafe_allow_html=True)
    else:
        st.success("✅ Sem alertas ativos")

    st.markdown("## 📊 Métricas")
    cols = st.columns(5)
    for i, ind in enumerate(['PIB', 'PCE', 'Payroll', 'CPI', 'PMI']):
        with cols[i]:
            a = analises[ind]
            st.metric(f"{ind} {a['tendencia']}", fmt(ind, a['valor']), f"{a['variacao']:+.2f}%")
            st.caption(CONFIG[ind]['ref_label'])

    st.markdown("## 📈 Evolução")
    inds = ['PIB', 'PCE', 'Payroll', 'CPI', 'PMI']
    fig = make_subplots(rows=len(inds), cols=1, subplot_titles=inds, vertical_spacing=0.12)
    cg = {'PIB': '#667eea', 'PCE': '#764ba2', 'Payroll': '#f093fb', 'CPI': '#ff6b6b', 'PMI': '#4ecdc4'}
    for i, ind in enumerate(inds, 1):
        s = dados[ind].tail(24)
        fig.add_trace(go.Scatter(x=s.index, y=s.values, name=ind,
                                 line=dict(color=cg[ind], width=3)), row=i, col=1)
        fig.add_hline(y=CONFIG[ind]['ref'], line_dash="dash", line_color="gray",
                      annotation_text=CONFIG[ind]['ref_label'], row=i, col=1)
    fig.update_layout(height=1100, showlegend=True, hovermode='x unified')
    st.plotly_chart(fig)

# ---------- ABA CRYPTO ----------
with tab_crypto:
    st.markdown("## ₿ Bitcoin × Macro")
    try:
        btc = buscar_bitcoin()
        preco = btc.iloc[-1]
        var30 = (btc.iloc[-1] / btc.iloc[-31] - 1) * 100 if len(btc) > 31 else 0

        m1, m2, m3 = st.columns(3)
        m1.metric("Bitcoin", f"US$ {preco:,.0f}", f"{var30:+.1f}% (30d)")

        corr_sp = None
        corr_cpi = None
        if 'SP500' in dados:
            bm = btc.resample('ME').last().pct_change().dropna()
            sm = dados['SP500'].resample('ME').last().pct_change().dropna()
            cm = dados['CPI'].diff().dropna()
            dfc = pd.concat([bm, sm], axis=1, sort=False).dropna().tail(12)
            if len(dfc) > 5:
                corr_sp = dfc.iloc[:, 0].corr(dfc.iloc[:, 1])
            dfc2 = pd.concat([bm, cm], axis=1, sort=False).dropna().tail(12)
            if len(dfc2) > 5:
                corr_cpi = dfc2.iloc[:, 0].corr(dfc2.iloc[:, 1])

        if corr_sp is not None:
            m2.metric("Correlação BTC × S&P500", f"{corr_sp:.2f}")
        if corr_cpi is not None:
            m3.metric("Correlação BTC × Surpresa CPI", f"{corr_cpi:.2f}")

        figb = go.Figure()
        figb.add_trace(go.Scatter(x=btc.index, y=btc.values, name="BTC",
                                  line=dict(color='#f7931a', width=3)))
        figb.add_trace(go.Scatter(x=btc.index, y=btc.rolling(30).mean().values,
                                  name="Média 30d", line=dict(color='gray', dash='dash')))
        figb.update_layout(height=450, hovermode='x unified',
                           title="Bitcoin — últimos 12 meses")
        st.plotly_chart(figb)

        st.markdown("""
        **📚 Como usar a macro no crypto:**
        - 🔥 **CPI/PCE acima do esperado** → Fed hawkish → liquidez cai → pressão de **queda** no BTC
        - 💪 **Payroll muito forte** → juros altos por mais tempo → vento contrário p/ risco
        - 📉 **PMI < 50 + Payroll fraco** → Fed corta juros → liquidez sobe → **ventos favoráveis** p/ BTC
        - Correlação alta com S&P500 = BTC se comportando como ativo de risco
        """)
    except Exception:
        st.warning("Não foi possível carregar dados do Bitcoin (limite da API CoinGecko ou sem internet). Tente novamente em 1 minuto.")

# ---------- ABA BRASIL ----------
with tab_br:
    st.markdown("## 🇧🇷 Brasil (Banco Central)")
    st.caption("Fonte: API SGS do Banco Central do Brasil")

    ipca12 = buscar_br([432], -5, 30)
    ipcam = buscar_br([433], -2, 3)
    selic = buscar_br([11, 12, 4189], 2, 40)
    dolar = buscar_br([1], 1, 10)

    c1, c2, c3, c4 = st.columns(4)
    if ipca12 is not None:
        c1.metric("IPCA (12 meses)", f"{ipca12.iloc[-1]:.2f}%")
    if ipcam is not None:
        c2.metric("IPCA (mês)", f"{ipcam.iloc[-1]:.2f}%")
    if selic is not None:
        c3.metric("SELIC", f"{selic.iloc[-1]:.2f}% a.a.")
    if dolar is not None:
        c4.metric("Dólar", f"R$ {dolar.iloc[-1]:.2f}")

    series_br = {'IPCA 12m': ipca12, 'IPCA mensal': ipcam, 'SELIC': selic, 'Dólar': dolar}
    series_br = {k: v for k, v in series_br.items() if v is not None}

    if series_br:
        figbr = make_subplots(rows=len(series_br), cols=1, subplot_titles=list(series_br.keys()),
                              vertical_spacing=0.15)
        cb = {'IPCA 12m': '#ff6b6b', 'IPCA mensal': '#ffa07a', 'SELIC': '#667eea', 'Dólar': '#4ecdc4'}
        for i, (nome, s) in enumerate(series_br.items(), 1):
            s = s.tail(24)
            figbr.add_trace(go.Scatter(x=s.index, y=s.values, name=nome,
                                       line=dict(color=cb.get(nome, '#888'), width=3)), row=i, col=1)
        figbr.update_layout(height=220 * len(series_br), showlegend=False, hovermode='x unified')
        st.plotly_chart(figbr)
    else:
        st.warning("Não foi possível conectar à API do Banco Central. Verifique sua internet.")

# ---------- ABA IA ----------
with tab_ia:
    st.markdown("## 🤖 IA Preditiva")
    st.caption("Modelo explicável: tendência linear ajustada aos últimos 12 meses")

    c1, c2 = st.columns(2)
    for col, ind in zip([c1, c2], ['CPI', 'PCE']):
        with col:
            s = dados[ind].tail(24)
            pred, incl = prever_serie(dados[ind])
            datas_fut = pd.date_range(start=s.index[-1], periods=4, freq='ME')[1:]

            figp = go.Figure()
            figp.add_trace(go.Scatter(x=s.index, y=s.values, name=f"{ind} histórico",
                                      line=dict(color='#ff6b6b', width=3)))
            figp.add_trace(go.Scatter(x=datas_fut, y=pred, name="Previsão IA",
                                      mode='lines+markers', line=dict(color='#9c27b0', dash='dot', width=3)))
            figp.add_hline(y=2.0, line_dash="dash", line_color="gray", annotation_text="meta 2%")
            figp.update_layout(height=400, title=f"Previsão {ind} (próximos 3 meses)")
            st.plotly_chart(figp)

            if incl > 0.02:
                direcao = "SUBINDO 📈"
            elif incl < -0.02:
                direcao = "CAINDO 📉"
            else:
                direcao = "ESTÁVEL ➡️"
            st.markdown(f"**Tendência da IA:** {ind} {direcao} — projeção de "
                        f"**{pred[-1]:.2f}%** em 3 meses ({incl:+.3f} p.p./mês)")

    st.info("⚠️ A IA é uma ferramenta de apoio. Decisões de investimento exigem análise completa.")

st.markdown("---")
st.markdown("<div style='text-align:center; color:#888;'>Sistema Macro IA v4.1 — EUA (FRED) • "
            "Brasil (BCB) • Bitcoin (CoinGecko) • IA Preditiva • Telegram</div>",
            unsafe_allow_html=True)