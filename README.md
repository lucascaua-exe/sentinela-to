# Sentinela TO

**Plataforma de Telemetria IoT e Inteligência Preditiva para Proteção Energética da Indústria Tocantinense**

MVP (TRL 4–5) que monitora em tempo real os parâmetros trifásicos de entrada de uma fábrica, detecta anomalias com Machine Learning e aciona automaticamente o protocolo de isolamento e comutação para autoprodução (solar + baterias) antes que a falha da rede queime motores ou pare a produção.

## Arquitetura

```
┌─────────────────────────────┐   HTTP POST / WebSocket   ┌──────────────────────────────┐
│   Simulador IoT (Python)    │ ────────────────────────► │     Backend API (FastAPI)    │
│ - Medidor trifásico 220 V   │ ◄──── cenário comandado ─ │ - Isolation Forest (sklearn) │
│ - 7 cenários de distúrbio   │                           │ - Regras PRODIST Módulo 8    │
└─────────────────────────────┘                           │ - Chave de Transferência     │
                                                          └──────────────┬───────────────┘
                                                                 WebSocket (ao vivo)
                                                                         ▼
                                                          ┌──────────────────────────────┐
                                                          │   Dashboard (React)          │
                                                          │ - Tensão por fase            │
                                                          │ - Oscilograma (ECharts)      │
                                                          │ - Sinal da IA + comutação    │
                                                          └──────────────────────────────┘
```

| Módulo | Arquivo | Responsabilidade |
| --- | --- | --- |
| Simulador IoT | `simulator/iot_emulator.py` | Emula o medidor do QGD, enviando um pacote a cada 500 ms |
| Motor de IA | `backend/ai/anomaly_detector.py` | Isolation Forest, janela móvel de confirmação e diagnóstico regulatório |
| Chave de transferência | `backend/core/transfer_switch.py` | Máquina de estados: Rede → Armada → Solar + BESS → Sincronismo → Rede |
| Copilot de diagnóstico (RAG) | `backend/ai/knowledge_base.py`, `backend/ai/copilot.py`, `backend/ai/knowledge/` | Recupera trechos da base técnica e monta causa raiz, ações e resumo falado |
| Histórico e relatórios | `backend/core/event_log.py`, `backend/core/settings.py`, `backend/reports/report.py` | Ocorrências persistentes, preferências por usuário e relatórios PDF (ReportLab) e CSV |
| API | `backend/main.py` | Ingestão, alertas, comandos do operador e transmissão via WebSocket |
| Dashboard | `frontend/` | Interface minimalista do operador: `constants.jsx` (fases e faixas PRODIST), `components/Header.jsx`, `SiteSelector.jsx`, `ScenarioBar.jsx`, `TransferBar.jsx`, `DiagnosisCard.jsx`, `MetricCards.jsx`, `RealtimeChart.jsx`, `SettingsDrawer.jsx`, `HistoryView.jsx` e `speech.jsx` (voz e avisos) (React, ECharts e Anime.js via CDN, sem etapa de build) |

## Como funciona a detecção

1. **IA não supervisionada**: um Isolation Forest é treinado com o perfil de operação normal da planta (9 variáveis: tensões, corrente, frequência, FP, THD, desequilíbrio e desvio máximo). Ele detecta desvios multivariados sutis, como a degradação lenta que antecede uma falha, antes que qualquer limite isolado seja violado.
2. **Janela móvel de confirmação**: a severidade só sobe quando a anomalia persiste na janela, o que evita disparos por ruído.
3. **Diagnóstico regulatório**: as faixas da ANEEL (PRODIST Módulo 8: tensão adequada de 202 a 231 V, desequilíbrio até 3%, THD até 10%) explicam a causa ao operador e definem a ação de proteção.
4. **Ação automática**: um evento crítico arma a chave de transferência, que comuta a planta para autoprodução em 5 s (3 s em queda de fase), a menos que a rede se normalize (transitório filtrado) ou que o operador cancele. A faixa que aparece no painel mostra a contagem e permite cancelar ou comutar na hora; depois de um cancelamento, a chave só volta a armar quando a rede normalizar. Depois de 10 s de rede estável, a planta volta à concessionária com sincronismo. Se a bateria cair a 15% em autoprodução, as cargas não críticas são desligadas.

## Diagnóstico Copilot IA (RAG + voz)

Quando um alerta sobe de nível ou muda de causa, o backend gera um diagnóstico e o envia ao painel pelo WebSocket. O card "Diagnóstico Copilot IA" mostra a causa provável, as ações (Imediata/Preventiva), as normas citadas e a confiança. O botão **Ouvir diagnóstico** lê o resumo em português pela Web Speech API do navegador, e a opção "Voz automática em alerta crítico" narra sozinha os diagnósticos de severidade CRÍTICA. Quando a rede normaliza e o episódio se encerra, o card some.

1. **Base de conhecimento** (`backend/ai/knowledge/*.md`): trechos sobre IEEE 1159, PRODIST Módulo 8, ABNT NBR 5410, boas práticas de fabricantes de motores (WEG, Schneider, ABB) e notas regionais do Tocantins. Os textos são uma síntese técnica escrita para o projeto, não a cópia das normas. As notas regionais precisam ser validadas com a distribuidora.
2. **Recuperação**: índice TF-IDF (scikit-learn) com busca separada para cada distúrbio detectado, do mais grave ao menos grave. Sintomas derivados (por exemplo, o desequilíbrio causado por um afundamento) não viram causa raiz.
3. **Geração**: sem chave de LLM, um compositor local monta a resposta usando só os trechos recuperados. Com `OPENAI_API_KEY` definida (e, se quiser, `OPENAI_BASE_URL` e `SENTINELA_LLM_MODEL`, padrão `gpt-4o-mini`), o texto é gerado por um LLM compatível com a API da OpenAI. Mesmo assim, a severidade vem do detector, só são aceitas referências presentes nos trechos recuperados, e qualquer falha volta ao compositor local.
4. **Índice de confiabilidade (CS)**: `CS = 0,7 × S_vetor + 0,3 × S_densidade`.
   - `S_vetor` é a fração, ponderada pelo cosseno, dos 3 vizinhos mais próximos no espaço vetorial que são evidência marcada para cada distúrbio. A causa raiz pesa 60% e os demais distúrbios dividem o restante.
   - `S_densidade` conta as fontes independentes da base com evidência relevante para a causa raiz; três fontes valem 100%.
   - Uma ocorrência validada pela engenharia vale por duas fontes bibliográficas, tanto no voto vetorial quanto na densidade.
   - O cosseno TF-IDF bruto depende do vocabulário da consulta, por isso as duas medidas são relativas. O CS mede quanto a base sustenta o diagnóstico; não é uma probabilidade calibrada.

### Pipeline auto-adaptativo (confiança abaixo de 90%)

Hoje os cenários documentados ficam entre 95% e 99,9%. Ficam abaixo do limiar o padrão atípico que só a IA percebe (cenário **Pré-falha**, cerca de 77%) e a sobrecorrente sem perturbação de tensão (cerca de 75%), porque a base tem pouca evidência para eles. Quando isso acontece:

1. **Contingência segura**: o diagnóstico é marcado como hipótese (`low_confidence`, `caveat`), ganha um passo inicial de isolamento preventivo e um passo final de acionamento da engenharia, e a fala avisa a confiança.
2. **Fila de curadoria** (tabelas `knowledge_gaps` e `validated_resolutions` em `data/sentinela.db`): o evento é registrado com a telemetria bruta, os trechos recuperados, a hipótese e os componentes do CS. Lacunas abertas com a mesma causa raiz são agrupadas e só somam ocorrências.
3. **Busca no repositório**: a pasta `backend/ai/knowledge/` é verificada; se houver documento novo ou alterado, a base é reindexada e o diagnóstico refeito.
4. **Validação humana**: no botão **Curadoria** do cabeçalho, o engenheiro confirma ou corrige a causa, as ações e a norma. A resolução vira um trecho da base, o índice é reconstruído em segundo plano e trocado de uma vez (sem reiniciar o backend), e as unidades que ainda exibiam aquela hipótese recebem o diagnóstico retreinado na hora.

Endpoints, todos com sessão: `POST /api/v1/feedback`, `GET /api/v1/knowledge/gaps?status=OPEN|RESOLVED|DISMISSED|ALL`, `POST /api/v1/knowledge/gaps/{id}/dismiss` e `POST /api/v1/knowledge/reindex`. O índice continua sendo TF-IDF local, sem ChromaDB, LangChain ou embeddings pagos; a troca para embeddings fica restrita a `knowledge_base.py`.

Também há diagnóstico sob demanda em `POST /api/v1/diagnose`, que exige sessão e recebe uma leitura no mesmo formato da telemetria. A resposta segue o esquema `cause_analysis`, `severity_level` (BAIXA, MÉDIA, ALTA ou CRÍTICA), `confidence_score`, `norm_references`, `recommended_solutions` e `speech_summary`. Para ver o diagnóstico de cada cenário no terminal, rode `python -X utf8 -m scripts.demo_copilot`.

## Configurações, histórico e relatórios

**Configurações** (ícone de engrenagem no cabeçalho): as preferências ficam salvas por usuário na tabela `user_settings` de `data/sentinela.db` e valem em qualquer computador. Cada alteração é gravada sozinha, sem botão de salvar.

- **Alerta por voz**: narração automática dos diagnósticos críticos (e, se quiser, também os de atenção), escolha da voz em português, velocidade, volume e botão de teste.
- **Avisos**: som curto e notificação do navegador quando a IA detecta uma falha crítica. A notificação aparece quando o painel está em outra aba ou minimizado.
- **Painel**: unidade aberta por padrão (um link com `?site=` tem prioridade) e exibição da barra de simulação de cenários.
- **Relatórios**: empresa e responsável técnico, impressos no cabeçalho do PDF.

**Histórico e relatórios** (aba no cabeçalho, `#/historico`): cada ocorrência vira um episódio em `backend/core/event_log.py`. O episódio começa no primeiro alerta de atenção ou crítico e termina quando a rede normaliza e a planta está de volta à concessionária. Ele guarda:

- severidade de pico e todos os distúrbios;
- extremos de tensão, corrente, FP e THD;
- se houve comutação para backup;
- o diagnóstico da IA, com causa, confiança, ações e normas;
- a sequência de eventos da IA e da chave de transferência.

Na página você encontra:

- filtros por período (hoje, 7 dias, 30 dias, tudo ou datas personalizadas), unidade, severidade, distúrbio, comutação e busca livre;
- indicadores do período e um gráfico de ocorrências por dia;
- uma linha do tempo agrupada por dia, em que cada ocorrência abre para mostrar a análise completa.

O botão **Relatório PDF** gera um documento empresarial com a logo do Sentinela TO e os mesmos filtros da tela. Ele traz:

- cabeçalho e rodapé com paginação;
- resumo executivo com a perda evitada;
- gráfico diário e distribuição por unidade e por distúrbio;
- tabela detalhada;
- análise das ocorrências críticas;
- nota PRODIST.

O botão **Exportar planilha (CSV)** gera um arquivo com `;` e UTF-8, pronto para o Excel em português.

Endpoints, todos com sessão:

- `GET` e `PUT /api/v1/settings`;
- `GET /api/v1/history/episodes`, com os filtros `start`, `end` (segundos Unix), `sites`, `severities`, `codes` (listas separadas por vírgula), `transferred` e `q`, além de `limit` e `offset`;
- `GET /api/v1/history/episodes/{id}`;
- `GET /api/v1/reports/occurrences.pdf` e `.csv`, com os mesmos filtros.

Para gerar o relatório de desempenho do detector em todos os cenários, rode `python -m scripts.benchmark_detector`. Resultado atual: nenhum falso alarme em 1.000 amostras normais, todos os distúrbios detectados e latência média de cerca de 1,6 ms por pacote.

## Executando

Requisitos: Python 3.10 ou superior. O dashboard carrega o React por CDN, então o navegador precisa de acesso à internet.

```powershell
# Tudo de uma vez (cria o venv na primeira execução)
.\run_demo.ps1
```

Ou manualmente:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# Terminal 1 - backend + dashboard
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --port 8000

# Terminal 2 - simulador IoT
.\.venv\Scripts\python.exe simulator\iot_emulator.py
```

Abra http://localhost:8000. A documentação interativa da API fica em http://localhost:8000/docs.

### Acesso ao painel

O painel exige login. Usuários e sessões ficam em SQLite (`data/sentinela.db`, criado automaticamente); as senhas são armazenadas com PBKDF2-SHA256 e a sessão usa cookie HttpOnly válido por 12 horas. Após 5 tentativas erradas em 5 minutos, o login é bloqueado temporariamente.

Na primeira execução é criado o usuário `admin`, com a senha exibida uma única vez no terminal do backend (ou a definida em `SENTINELA_ADMIN_PASSWORD`). Para criar usuários ou trocar senhas:

```powershell
.\.venv\Scripts\python.exe -m scripts.create_user admin
```

A ingestão de telemetria (`/api/telemetry` e `/ws/telemetry`) não usa sessão de usuário, mas exige o cabeçalho `X-Device-Token`. O token vem da variável `SENTINELA_DEVICE_TOKEN` ou é gerado na primeira execução em `data/device.token`, que o simulador lê automaticamente quando roda na mesma máquina. Pacotes com valores fora da faixa física (tensão negativa, fator de potência acima de 1, `NaN`) são rejeitados.

### Unidades monitoradas

O painel acompanha quatro unidades: Palmas (QGD 01), Araguaína (QGD 02), Gurupi (QGD 03) e Porto Nacional (QGD 04). Cada uma tem análise de IA, chave de transferência, histórico e cenário próprios; o menu suspenso no cabeçalho alterna entre elas e mostra o status de todas. O cadastro fica só em `SITES` (`backend/main.py`); o simulador obtém a lista em `/api/telemetry/sites` e alimenta todas por padrão (`--sites palmas,gurupi` limita as unidades).

### Cenários de demonstração

Os cenários podem ser trocados ao vivo pela barra de cenários do dashboard (atalhos: teclas **1** a **5**) ou com `--mode` no simulador:

| Cenário | O que simula | Resposta esperada |
| --- | --- | --- |
| `NORMAL` | Rede estável em 220 V | Normal |
| `PRE_FAULT` | Degradação lenta e sutil | Atenção (somente a IA percebe no início) |
| `SAG_PALMAS` | Queda de ~22% nas fases A e B com sobrecorrente | Crítico e comutação automática |
| `SWELL` | Sobretensão de ~15% | Crítico e comutação automática |
| `PHASE_LOSS` | Fase C em 0 V (fusível queimado) | Crítico e comutação em 3 s |
| `PHASE_IMBALANCE` | Falta de fase parcial | Crítico e comutação automática |
| `HARMONIC` | THD acima de 10% | Atenção |
| `BLACKOUT` | Interrupção total | Crítico e comutação automática |

## Testes

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Os testes cobrem: ausência de falsos alarmes, detecção de cada distúrbio, mensagem de alerta ao operador, detecção pré-falha pela IA, latência, ciclo completo da chave de transferência, descarte de transitórios, cancelamento pelo operador, corte de cargas por bateria baixa, token e validação da telemetria, login, sessões e limite de tentativas, isolamento entre unidades, API/WebSocket, controle de excesso de alertas e o copiloto RAG (fundamentação nas fontes recuperadas, causa raiz, restrição de citações e fallback do LLM, publicação e limpeza do diagnóstico, endpoint `/api/v1/diagnose`) e o loop de autotreinamento (fórmula do CS, contingência abaixo de 90%, agrupamento de lacunas, retreinamento por validação, reindexação de documentos novos e endpoint de feedback).

## Parâmetros econômicos

A estimativa de perdas evitadas no dashboard usa os parâmetros de `ECONOMICS` em `backend/main.py` (custo de hora parada, horas evitadas por evento e custo de substituição de motor). Eles são valores de referência e devem ser ajustados para cada planta.

## Próximos passos (rumo ao TRL 6–7)

- Substituir o emulador por um medidor real (ex.: multimedidor com Modbus RTU/TCP ligado a um gateway ESP32 ou Raspberry Pi).
- Acionar um CLP/relé real da chave de transferência via Modbus TCP ou MQTT.
- Retreinar o modelo com as primeiras semanas de medição da própria planta.
- Persistência das séries temporais (TimescaleDB/InfluxDB) e relatórios de DEC/FEC para embasar pleitos junto à concessionária.
