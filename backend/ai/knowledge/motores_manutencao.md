---
source: Boas práticas de fabricantes de motores e transformadores (WEG, Schneider Electric, ABB) — síntese técnica do Sentinela TO
category: Guia de manutenção
---

## Subtensão em motores de indução
ref: ABNT NBR 17094-1 / IEC 60034-1 — zonas de tensão e frequência
tags: SAG OVERCURRENT subtensão motor torque corrente aquecimento compressor
Os motores de indução são projetados para operar continuamente com variação de tensão de até ±5% e de frequência de até ±2% (zona A); na zona B, até ±10% de tensão, a operação é admitida com aquecimento maior. O torque varia com o quadrado da tensão: com 80% da tensão nominal, o motor entrega cerca de 64% do torque. Para manter a carga, a corrente sobe, o que eleva as perdas no cobre e a temperatura dos enrolamentos.
causa: Subtensão prolongada faz os motores absorverem corrente acima da nominal para manter a carga, com risco de sobreaquecimento e atuação térmica em compressores e bombas.
imediata: Reduzir a carga mecânica dos motores e verificar se os relés térmicos estão ajustados para a corrente nominal de placa.
preventiva: Usar partida suave (soft-starter) ou inversor de frequência nos motores de maior potência para reduzir os afundamentos provocados pela própria planta.

## Sobretensão em motores e inversores
ref: Boas práticas de fabricantes (WEG, Schneider, ABB) — sobretensão
tags: SWELL sobretensão isolamento inversor barramento CC
Sobretensão aumenta a corrente de magnetização e as perdas no ferro dos motores e acelera o envelhecimento do isolamento. Em inversores de frequência, eleva a tensão do barramento CC e provoca desligamentos por sobretensão ou danos aos capacitores do barramento.
imediata: Verificar alarmes de sobretensão no barramento CC dos inversores e manter desligados os equipamentos que já registraram falha.
preventiva: Instalar relé de sobretensão (função 59) e solicitar à distribuidora a verificação do tap do transformador que alimenta a planta.

## Falta de fase (monofasamento)
ref: Boas práticas de fabricantes (WEG, Schneider, ABB) — falta de fase
tags: PHASE_LOSS falta de fase monofasamento fusível queima motor
Com a perda de uma fase, um motor trifásico em operação continua girando em regime monofásico, com correntes nas fases remanescentes que podem passar de 170% da nominal. Um motor parado não consegue partir. Sem proteção específica, a queima dos enrolamentos pode ocorrer em poucos minutos. As causas mais comuns são fusível queimado (na distribuidora ou no próprio quadro), cabo ou conexão rompida e contato danificado em contator.
causa: Perda de uma fase compatível com fusível queimado, cabo ou conexão rompida ou contato danificado, levando os motores trifásicos à operação monofásica com sobrecorrente.
imediata: Desligar imediatamente os motores trifásicos ou transferir para a fonte alternativa, e inspecionar fusíveis, conexões e contatores do circuito afetado.
preventiva: Instalar relés de falta e sequência de fase (função 47) em todos os motores críticos e incluir termografia dos fusíveis no plano de manutenção.

## Desequilíbrio de tensão em motores
ref: NEMA MG-1 — redução de potência por desequilíbrio de tensão
tags: IMBALANCE desequilíbrio motor aquecimento sequência negativa
Pequenos desequilíbrios de tensão provocam desequilíbrios de corrente várias vezes maiores. Como regra prática, a elevação adicional de temperatura em porcentagem é cerca de duas vezes o quadrado do desequilíbrio percentual: 3% de desequilíbrio resulta em cerca de 18% a mais de aquecimento. A NEMA MG-1 recomenda reduzir a potência do motor acima de 1% de desequilíbrio e não operar acima de 5%.
imediata: Reduzir a carga dos motores trifásicos para compensar o aquecimento adicional enquanto o desequilíbrio persistir.
preventiva: Redistribuir as cargas monofásicas entre as fases e reapertar conexões identificadas com aquecimento na termografia.

## Sobrecorrente e proteção térmica
ref: Boas práticas de fabricantes (WEG, Schneider, ABB) — proteção térmica
tags: OVERCURRENT sobrecorrente relé térmico disjuntor-motor
Sobrecorrente persistente em motores costuma resultar de subtensão, desequilíbrio, falta de fase ou sobrecarga mecânica. A proteção deve usar relé térmico ou disjuntor-motor ajustado para a corrente de placa, e preferencialmente relé de proteção multifunção com sensores de temperatura (PTC) nos enrolamentos.
causa: Sobrecorrente nos motores provocada pela perturbação de tensão, com risco de atuação da proteção térmica e de dano aos enrolamentos.
imediata: Conferir a corrente de cada fase com alicate amperímetro e comparar com a corrente nominal de placa dos motores.
preventiva: Adotar relés de proteção multifunção com sensores PTC nos motores críticos.

## Harmônicas em transformadores, motores e automação
ref: IEEE 519 — limites de distorção harmônica no ponto de acoplamento
tags: HARMONIC harmônicas transformador fator K filtro inversor CLP
A IEEE 519 recomenda distorção harmônica total de tensão de até 8% no ponto de acoplamento comum para sistemas até 1 kV. Harmônicas elevam as perdas em transformadores e motores, sobrecarregam o neutro e causam mau funcionamento de CLPs e inversores. As fontes típicas são inversores, retificadores, fontes chaveadas e fornos.
imediata: Identificar as cargas não lineares em operação e, se possível, reduzir temporariamente as de maior potência.
preventiva: Instalar reatâncias de linha nos inversores e avaliar filtros harmônicos passivos ou ativos; usar transformadores com fator K adequado.

## Variação de frequência em motores
ref: Boas práticas de fabricantes (WEG, Schneider, ABB) — frequência
tags: FREQUENCY frequência velocidade motor relé 81
A velocidade dos motores de indução é proporcional à frequência. Subfrequência reduz a ventilação dos motores autoventilados e aumenta a corrente de magnetização, elevando a temperatura. Variações de frequência na rede indicam distúrbio sistêmico e costumam preceder desligamentos maiores.
imediata: Preparar a comutação para autoprodução e evitar novas partidas de motores até a frequência normalizar.
preventiva: Instalar relé de frequência (função 81) para desacoplar a planta automaticamente em subfrequência persistente.

## Fator de potência baixo
ref: Boas práticas de fabricantes (WEG, Schneider, ABB) — correção do fator de potência
tags: POWER_FACTOR fator de potência motor a vazio capacitor
Fator de potência baixo é causado principalmente por motores operando a vazio ou subcarregados e por bancos de capacitores desligados ou com células danificadas. Além do faturamento de reativo excedente, eleva a corrente nos cabos e transformadores.
causa: Fator de potência baixo compatível com motores subcarregados e banco de capacitores desligado ou com células danificadas.
preventiva: Inspecionar as células e os contatores do banco de capacitores e redimensionar motores sobredimensionados.

## Padrão de degradação pré-falha
ref: Boas práticas de manutenção preditiva — análise de tendência
tags: AI_PATTERN pré-falha degradação tendência termografia manutenção preditiva
Uma queda gradual de tensão acompanhada de aumento da corrente e da distorção harmônica é típica de conexões em deterioração (oxidação, afrouxamento), sobrecarga progressiva de alimentadores e envelhecimento do isolamento. Esses processos raramente violam limites de imediato, mas evoluem para falha se não forem tratados.
causa: Assinatura de degradação gradual, compatível com conexões oxidadas ou frouxas, sobrecarga progressiva do alimentador ou envelhecimento do isolamento.
imediata: Programar inspeção termográfica do QGD e dos barramentos nas próximas horas.
preventiva: Implantar plano de manutenção preditiva com análise de tendência das variáveis elétricas e termografia periódica.
