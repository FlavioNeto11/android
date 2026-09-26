# Relatório de desempenho e capacidade

Linha de base, cenários, resultados e decisões da evolução de desempenho pedida pelo dono em 26/09/2026. Os
contratos estão em [`api-contract.md`](api-contract.md) (adendo v0.20), e a coordenação e a retomada, em
[`handoffs/evolucao-desempenho.md`](handoffs/evolucao-desempenho.md). Este relatório não repete os documentos de
cada área; aponta para eles.

**Prova.** Cada número traz o nível dele, e os três não se misturam:

- `real`: data, máquina e commit;
- `simulated`: `arquivo::teste` ou comando, com aparelho ou provedor falso;
- `not_run`.

Contagem simulada não é tempo real, nem US$ real, nem densidade de emuladores.

## 1. Linha de base

A preencher na integração da F1:

- a leitura real, somente GET, da produção;
- o benchmark simulado sobre `a0f251a`.

## 2. Resultados por frente

A preencher na integração.

## 3. Decisões sobre alternativas

A preencher com a F7:

- Docker nos serviços;
- emulador em contêiner;
- Redroid;
- executores por API ou web;
- NATS;
- Kubernetes.

## 4. Pendências externas

Ver [`handoffs/evolucao-desempenho.md`](handoffs/evolucao-desempenho.md) § Autorizações pendentes.
