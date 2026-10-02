# Changelog

Todas as mudanças relevantes deste projeto são registradas aqui. O formato segue o [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/) e o versionamento segue o [SemVer](https://semver.org/lang/pt-BR/).

## [1.0.0] - 2026-10-01

### Adicionado

- Detecção de anomalias com Isolation Forest e regras do PRODIST Módulo 8, incluindo a identificação de pré-falha.
- Chave de transferência automática para autoprodução (solar + BESS), com filtro de transitórios e comandos do operador.
- Copiloto de diagnóstico RAG com índice de confiança, citação de normas e narração por voz em português.
- Aprendizado ativo: fila de curadoria para diagnósticos abaixo de 90% e retreinamento sem reinício.
- Histórico de ocorrências com linha do tempo, filtros e indicadores.
- Relatórios em PDF, com identidade visual, e em CSV.
- Painel de configurações por usuário: voz, alertas, unidade padrão e dados do relatório.
- Monitoramento das unidades de Palmas, Araguaína, Gurupi e Porto Nacional.
- Autenticação com sessões, limite de tentativas e token por dispositivo IoT.
- Implantação automatizada em VPS com nginx e systemd.

[1.0.0]: https://github.com/lucascaua-exe/sentinela-to/releases/tag/v1.0.0
