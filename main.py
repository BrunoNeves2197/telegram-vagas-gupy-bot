import os
import re
import time
import html
import sqlite3
import requests
from datetime import datetime, timedelta

def esc(texto):
    return html.escape(str(texto)) if texto else ""

try:
    from bs4 import BeautifulSoup
    BS4_DISPONIVEL = True
except ImportError:
    BS4_DISPONIVEL = False
    print("⚠️  beautifulsoup4 não instalado — LinkedIn desativado. Rode: pip install beautifulsoup4")

# --- 1. CONFIG ---
DIRETORIO_ATUAL = os.path.dirname(os.path.abspath(__file__))
CAMINHO_BANCO   = os.path.join(DIRETORIO_ATUAL, 'vagas_gupy.db')

def carregar_env():
    caminho = os.path.join(DIRETORIO_ATUAL, '.env')
    if not os.path.exists(caminho):
        return
    with open(caminho) as f:
        for linha in f:
            linha = linha.strip()
            if linha and not linha.startswith('#') and '=' in linha:
                chave, valor = linha.split('=', 1)
                os.environ.setdefault(chave.strip(), valor.strip())

carregar_env()

TOKEN   = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID_GRUPO")

# --- 2. PERFIL DE PAULO ---

PALAVRAS_SENIOR = [
    "sênior", "senior", "sênio", " sr ", " sr.", "(sr)", "sr|",
    "especialista", "staff", "lead", "principal", "head", "arquiteto",
    "architect", "manager", "gerente", "coordenador", "diretor",
]

GAPS_ELIMINATORIOS = [
    "flutter", "dart",
    ".net", "c#", "dotnet", "asp.net",
    "kafka", "kubernetes", "k8s",
    "inglês fluente", "english fluent", "fluent english", "english required",
    "kotlin", "swift",
]

LOCAIS_ESTRANGEIROS = [
    "united states", "united kingdom", " usa", "u.s.a", " us,", " us ",
    "u.k.", "canada", "argentina", "méxico", "mexico", "españa", "spain",
    "worldwide", "anywhere in the world", "global remote", "europe",
    "colombia", "chile", "peru", "uruguay", "paraguay",
]

STACK_AVANCADO = [
    "react", "vue", "angular", "typescript", "javascript",
    "vtex", "graphql", "tailwind", "sass", "scss", "vite",
    "next.js", "nextjs", "next", "node", "express", "nestjs",
    "python", "django", "laravel", "php",
]

STACK_INTERMEDIARIO = [
    "postgresql", "mysql", "mongodb", "redis", "docker",
    "jest", "react native", "expo", "spring boot", "java",
]

# Set em memória para evitar duplicatas na mesma execução (mesma vaga, fontes/buscas diferentes)
_enviados_sessao: set = set()

def _chave_sessao(titulo: str, empresa: str) -> str:
    normalizar = lambda s: re.sub(r'[^a-z0-9]', '', s.lower())
    return normalizar(titulo)[:60] + "|" + normalizar(empresa)[:30]

def is_senior(titulo):
    t = titulo.lower()
    return any(p in t for p in PALAVRAS_SENIOR)

def tem_gap_eliminatorio(titulo):
    t = titulo.lower()
    return any(g in t for g in GAPS_ELIMINATORIOS)

def calcular_match(titulo):
    t = titulo.lower()
    techs_av  = [s for s in STACK_AVANCADO      if s in t]
    techs_int = [s for s in STACK_INTERMEDIARIO  if s in t]
    score = len(techs_av) * 2 + len(techs_int)
    techs = techs_av + techs_int
    if score >= 4:
        nivel = "🟢 Alto"
    elif score >= 2:
        nivel = "🟡 Médio"
    else:
        nivel = "🔵 Padrão"
    return nivel, techs

# --- 3. BANCO E TELEGRAM ---

TRADUCAO_MODELO = {
    "on-site": "Presencial",
    "hybrid":  "Híbrido",
    "remote":  "Remoto",
}

TRADUCAO_TIPO_VAGA = {
    "vacancy_type_effective":   "Efetivo",
    "vacancy_type_apprentice":  "Jovem Aprendiz",
    "vacancy_type_internship":  "Estágio",
    "vacancy_type_temporary":   "Temporário",
    "vacancy_type_freelancer":  "Freelancer",
}

def iniciar_banco():
    conn   = sqlite3.connect(CAMINHO_BANCO)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS vagas_enviadas (
            link TEXT PRIMARY KEY,
            data_publicacao TEXT,
            titulo TEXT
        )
    ''')
    conn.commit()
    return conn, cursor

def ja_enviada(cursor, link):
    cursor.execute('SELECT 1 FROM vagas_enviadas WHERE link = ?', (link,))
    return cursor.fetchone() is not None

def enviar_telegram(mensagem):
    payload = {
        "chat_id":                  CHAT_ID,
        "text":                     mensagem,
        "parse_mode":               "HTML",
        "disable_web_page_preview": True,
    }
    try:
        r = requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", json=payload, timeout=10)
        if r.status_code != 200:
            print(f"⚠️  Telegram recusou: {r.text}")
    except Exception as e:
        print(f"❌ Erro Telegram: {e}")

def registrar_e_enviar(conn, cursor, link, titulo, empresa, data_f, mensagem, fonte, nivel_match):
    chave = _chave_sessao(titulo, empresa)
    if chave in _enviados_sessao:
        print(f"   🔁 Duplicata (sessão): {titulo[:50]}")
        return
    _enviados_sessao.add(chave)
    cursor.execute('INSERT OR IGNORE INTO vagas_enviadas VALUES (?, ?, ?)', (link, data_f, titulo))
    conn.commit()
    enviar_telegram(mensagem)
    print(f"   ✅ [{nivel_match}] {titulo[:50]}...")
    time.sleep(2)

def local_estrangeiro(local):
    l = local.lower()
    return any(p in l for p in LOCAIS_ESTRANGEIROS)

def filtros_basicos(titulo):
    """Retorna (bloqueada, motivo) com os filtros de perfil."""
    if is_senior(titulo):
        return True, f"⏭️  Sênior: {titulo[:55]}"
    if tem_gap_eliminatorio(titulo):
        return True, f"🚫 Gap: {titulo[:55]}"
    return False, ""

# --- 4. GUPY ---

def buscar_vagas_gupy(conn, cursor):
    print("\n🟣 GUPY — iniciando varredura...")

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept':     'application/json, text/plain, */*',
        'Origin':     'https://portal.gupy.io',
    }
    url_api = "https://employability-portal.gupy.io/api/v1/jobs"

    filtros = [
        {"nome": "FRONT END · SP",      "params": {'state': 'São Paulo',       'jobName': 'front end',  'limit': 10}},
        {"nome": "FULL STACK · SP",     "params": {'state': 'São Paulo',       'jobName': 'full stack', 'limit': 10}},
        {"nome": "FRONT END · REMOTO",  "params": {'workplaceTypes': 'remote', 'jobName': 'front end',  'limit': 10}},
        {"nome": "FULL STACK · REMOTO", "params": {'workplaceTypes': 'remote', 'jobName': 'full stack', 'limit': 10}},
    ]

    for filtro in filtros:
        print(f"\n   🔎 {filtro['nome']}...")
        vagas_velhas  = 0
        LIMITE_VELHAS = 20

        for pagina in range(1, 36):
            params = filtro['params'].copy()
            params['offset'] = (pagina - 1) * 10

            try:
                resp = requests.get(url_api, headers=headers, params=params, timeout=15)
                if resp.status_code != 200:
                    print(f"   🛑 HTTP {resp.status_code}")
                    break

                dados = resp.json().get('data', [])
                if not dados:
                    print("   🔚 Sem mais vagas.")
                    break

                for vaga in dados:
                    link   = vaga.get('jobUrl', '')
                    if not link:
                        continue

                    titulo  = vaga.get('name', 'Título Indisponível')
                    empresa = vaga.get('careerPageName', 'Empresa não informada')
                    local   = "Qualquer lugar (Remoto)" if 'REMOTO' in filtro['nome'] else f"{vaga.get('city', 'SP')} - {vaga.get('state', 'SP')}"
                    modelo  = TRADUCAO_MODELO.get(vaga.get('workplaceType', ''), "Não informado")
                    tipo    = TRADUCAO_TIPO_VAGA.get(vaga.get('type', ''), "Outros")
                    pcd     = "Sim" if vaga.get('disabilities') else "Não informado"

                    data_iso = vaga.get('publishedDate', '')
                    try:
                        data_utc = datetime.strptime(data_iso.split('.')[0], "%Y-%m-%dT%H:%M:%S")
                        data_brt = data_utc - timedelta(hours=3)
                        data_f   = data_brt.strftime("%d/%m/%Y")
                        hora_f   = data_brt.strftime("%H:%M")
                        if datetime.now() - data_brt > timedelta(days=3):
                            print(f"   📅 Vaga antiga ({data_f}). Encerrando busca.")
                            vagas_velhas = LIMITE_VELHAS
                            break
                    except Exception:
                        data_f, hora_f = "Sem data", "--:--"

                    bloqueada, motivo = filtros_basicos(titulo)
                    if bloqueada:
                        print(f"   {motivo}")
                        continue

                    if ja_enviada(cursor, link):
                        vagas_velhas += 1
                        if vagas_velhas >= LIMITE_VELHAS:
                            break
                        continue

                    vagas_velhas = 0
                    nivel_match, techs = calcular_match(titulo)
                    techs_str = " · ".join(t.upper() for t in techs[:4]) if techs else "Verificar descrição"

                    mensagem = (
                        f"🟣 <b>GUPY — {filtro['nome']}</b>\n\n"
                        f"💼 <b>Vaga:</b> {esc(titulo)}\n"
                        f"🏢 <b>Empresa:</b> {esc(empresa)}\n"
                        f"📍 <b>Local:</b> {esc(local)}\n"
                        f"💻 <b>Modelo:</b> {modelo}\n"
                        f"📄 <b>Tipo:</b> {tipo}\n"
                        f"♿ <b>PCD:</b> {pcd}\n"
                        f"📅 <b>Data:</b> {data_f} às {hora_f}\n"
                        f"📊 <b>Match:</b> {nivel_match} · <i>{techs_str}</i>\n\n"
                        f"🔗 <a href='{esc(link)}'>Aplicar na Gupy</a>"
                    )
                    registrar_e_enviar(conn, cursor, link, titulo, empresa, data_f, mensagem, "GUPY", nivel_match)

                if vagas_velhas >= LIMITE_VELHAS:
                    print("   🛑 Encerrando paginação.")
                    break

            except Exception as e:
                print(f"   ⚠️  Erro: {e}")
                break

# --- 5. PROGRAMATHOR ---

NIVEL_SENIOR_PT = ['sênior', 'senior', 'sr', 'especialista', 'staff', 'lead', 'principal', 'head']

def buscar_vagas_programathor(conn, cursor):
    if not BS4_DISPONIVEL:
        print("\n⚠️  ProgramaThor desativado: instale beautifulsoup4")
        return

    print("\n🟤 PROGRAMATHOR — iniciando varredura...")

    headers = {
        'User-Agent':      'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept':          'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'pt-BR,pt;q=0.9',
    }

    filtros = [
        {"nome": "FRONT END · SP",      "termo": "front end",  "local_filtro": "sp"},
        {"nome": "FULL STACK · SP",     "termo": "full stack", "local_filtro": "sp"},
        {"nome": "FRONT END · REMOTO",  "termo": "front end",  "local_filtro": "remoto"},
        {"nome": "FULL STACK · REMOTO", "termo": "full stack", "local_filtro": "remoto"},
    ]

    for filtro in filtros:
        print(f"\n   🔎 {filtro['nome']}...")

        for pagina in range(1, 6):
            params = {"search": filtro["termo"]}
            if pagina > 1:
                params["page"] = pagina

            try:
                resp = requests.get("https://programathor.com.br/jobs", params=params, headers=headers, timeout=15)
                if resp.status_code != 200:
                    print(f"   🛑 HTTP {resp.status_code}")
                    break

                soup  = BeautifulSoup(resp.text, 'html.parser')
                cards = soup.find_all('div', class_='cell-list')

                if not cards:
                    print("   🔚 Sem mais vagas.")
                    break

                novos_na_pagina = 0

                for card in cards:
                    link_el = card.find('a', href=lambda h: h and '/jobs/' in h)
                    if not link_el:
                        continue

                    link = "https://programathor.com.br" + link_el['href']

                    titulo_el = card.find('h3')
                    titulo_raw = titulo_el.get_text(strip=True) if titulo_el else ""
                    if 'Vencida' in titulo_raw or 'vencida' in titulo_raw:
                        continue
                    titulo = titulo_raw.replace('NOVA', '').strip() or "Título Indisponível"

                    spans   = card.select('.cell-list-content-icon span')
                    empresa = spans[0].get_text(strip=True) if len(spans) > 0 else "Empresa não informada"
                    local   = spans[1].get_text(strip=True) if len(spans) > 1 else ""
                    salario = spans[3].get_text(strip=True) if len(spans) > 3 else ""
                    nivel   = spans[4].get_text(strip=True) if len(spans) > 4 else ""
                    tipo    = spans[5].get_text(strip=True) if len(spans) > 5 else ""
                    tags    = [t.get_text(strip=True) for t in card.select('span.tag-list')]

                    # Filtro de local (SP ou Remoto)
                    local_lower = local.lower()
                    if filtro["local_filtro"] == "sp" and "são paulo" not in local_lower and "sp" not in local_lower and "híbrido" not in local_lower:
                        continue
                    if filtro["local_filtro"] == "remoto" and "remoto" not in local_lower:
                        continue

                    # Filtro de senioridade: campo explícito do card + título
                    if any(s in nivel.lower() for s in NIVEL_SENIOR_PT) or is_senior(titulo):
                        print(f"   ⏭️  Sênior: {titulo[:55]}")
                        continue

                    # Gaps nos tags de tecnologia
                    if tem_gap_eliminatorio(titulo) or any(tem_gap_eliminatorio(t) for t in tags):
                        print(f"   🚫 Gap: {titulo[:55]}")
                        continue

                    if ja_enviada(cursor, link):
                        continue

                    novos_na_pagina += 1

                    # Match scoring usa título + stack explícita do card
                    texto_match = titulo + " " + " ".join(tags)
                    nivel_match, techs = calcular_match(texto_match)
                    techs_str = " · ".join(t.upper() for t in techs[:4]) if techs else (", ".join(tags[:4]) or "Verificar descrição")
                    tags_str  = ", ".join(tags[:6]) if tags else ""

                    mensagem = (
                        f"🟤 <b>PROGRAMATHOR — {filtro['nome']}</b>\n\n"
                        f"💼 <b>Vaga:</b> {esc(titulo)}\n"
                        f"🏢 <b>Empresa:</b> {esc(empresa)}\n"
                        f"📍 <b>Local:</b> {esc(local)}\n"
                        f"📄 <b>Nível:</b> {esc(nivel)}"
                        + (f" · {esc(tipo)}" if tipo else "") + "\n"
                        + (f"💰 <b>Salário:</b> {esc(salario)}\n" if salario else "")
                        + (f"🛠️  <b>Stack:</b> <i>{esc(tags_str)}</i>\n" if tags_str else "")
                        + f"📊 <b>Match:</b> {nivel_match} · <i>{esc(techs_str)}</i>\n\n"
                        f"🔗 <a href='{esc(link)}'>Aplicar no ProgramaThor</a>"
                    )
                    registrar_e_enviar(conn, cursor, link, titulo, empresa, datetime.now().strftime("%d/%m/%Y"), mensagem, "PROGRAMATHOR", nivel_match)

                if novos_na_pagina == 0:
                    break

                time.sleep(1)

            except Exception as e:
                print(f"   ⚠️  Erro: {e}")
                break

# --- 6. LINKEDIN ---

def buscar_vagas_linkedin(conn, cursor):
    if not BS4_DISPONIVEL:
        print("\n⚠️  LinkedIn desativado: instale beautifulsoup4")
        return

    print("\n🔷 LINKEDIN — iniciando varredura...")

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    }
    url = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"

    # f_TPR=r259200 → últimos 3 dias | f_WT=2 → remoto
    filtros = [
        {"nome": "FRONT END · SP",      "params": {"keywords": "desenvolvedor front end", "location": "São Paulo, Brazil", "f_TPR": "r259200", "start": 0}},
        {"nome": "FULL STACK · SP",     "params": {"keywords": "desenvolvedor full stack", "location": "São Paulo, Brazil", "f_TPR": "r259200", "start": 0}},
        {"nome": "FRONT END · REMOTO",  "params": {"keywords": "desenvolvedor front end",  "location": "Brazil", "f_WT": "2", "f_TPR": "r259200", "start": 0}},
        {"nome": "FULL STACK · REMOTO", "params": {"keywords": "desenvolvedor full stack",  "location": "Brazil", "f_WT": "2", "f_TPR": "r259200", "start": 0}},
    ]

    for filtro in filtros:
        print(f"\n   🔎 {filtro['nome']}...")
        try:
            resp = requests.get(url, params=filtro["params"], headers=headers, timeout=15)
            if resp.status_code != 200:
                print(f"   🛑 HTTP {resp.status_code}")
                continue

            soup  = BeautifulSoup(resp.text, 'html.parser')
            cards = soup.find_all('div', class_='base-card')

            if not cards:
                print("   🔚 Nenhuma vaga ou resposta bloqueada.")
                continue

            for card in cards:
                titulo_el  = card.find(class_=lambda c: c and 'title' in c)
                empresa_el = card.find(class_=lambda c: c and 'subtitle' in c)
                local_el   = card.find(class_=lambda c: c and 'location' in c)
                link_el    = card.find('a', href=True)
                data_el    = card.find('time')

                titulo  = titulo_el.get_text(strip=True)  if titulo_el  else "Título Indisponível"
                empresa = empresa_el.get_text(strip=True) if empresa_el else "Empresa não informada"
                local   = local_el.get_text(strip=True)   if local_el   else ""
                link    = link_el['href'].split('?')[0]   if link_el    else ''

                if not link:
                    continue

                if local_estrangeiro(local):
                    print(f"   🌍 Estrangeiro: {titulo[:45]} ({local})")
                    continue

                try:
                    data_iso = data_el.get('datetime', '') if data_el else ''
                    data_pub = datetime.strptime(data_iso, "%Y-%m-%d")
                    data_f   = data_pub.strftime("%d/%m/%Y")
                    hora_f   = "--:--"
                except Exception:
                    data_f, hora_f = "Sem data", "--:--"

                bloqueada, motivo = filtros_basicos(titulo)
                if bloqueada:
                    print(f"   {motivo}")
                    continue

                if ja_enviada(cursor, link):
                    continue

                nivel_match, techs = calcular_match(titulo)
                techs_str = " · ".join(t.upper() for t in techs[:4]) if techs else "Verificar descrição"

                mensagem = (
                    f"🔷 <b>LINKEDIN — {filtro['nome']}</b>\n\n"
                    f"💼 <b>Vaga:</b> {esc(titulo)}\n"
                    f"🏢 <b>Empresa:</b> {esc(empresa)}\n"
                    + (f"📍 <b>Local:</b> {esc(local)}\n" if local else "")
                    + f"📅 <b>Data:</b> {data_f}\n"
                    f"📊 <b>Match:</b> {nivel_match} · <i>{techs_str}</i>\n\n"
                    f"🔗 <a href='{esc(link)}'>Aplicar no LinkedIn</a>"
                )
                registrar_e_enviar(conn, cursor, link, titulo, empresa, data_f, mensagem, "LINKEDIN", nivel_match)

        except Exception as e:
            print(f"   ⚠️  Erro: {e}")

# --- MAIN ---

def main():
    if not TOKEN or not CHAT_ID:
        print("❌ ERRO: Token do Telegram ou Chat ID não encontrados no arquivo .env!")
        return

    conn, cursor = iniciar_banco()

    buscar_vagas_gupy(conn, cursor)
    buscar_vagas_programathor(conn, cursor)
    buscar_vagas_linkedin(conn, cursor)

    conn.close()
    print("\n✅ Varredura completa de todas as fontes!")

if __name__ == '__main__':
    main()
