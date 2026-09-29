# Pedidos persistentes: pesquisa e desenho (esqueleto)

Pedido do dono de 29/09/2026: estruturar objetivos que continuem ativos ao longo do tempo — imediatos, agendados,
recorrentes, acionados por evento ou condição, e acompanhamentos em que a persona decide quando e como continuar,
dentro do objetivo e dos limites definidos pela pessoa. As tarefas pertencem ao produto: estado e execução no backend
e nos workers, com continuidade entre reinícios, sem depender da sessão da IDE.

**Estado: esqueleto.** A pesquisa e o desenho são os itens 26.1–26.8 de [../plano-100.md](../plano-100.md) (Fase 26);
a decisão de partida é o ADR-059 (proposto) em [../decisoes.md](../decisoes.md). Esta página separa o que **existe**
(com arquivo e linha, lidos em 29/09 sobre `a962edb`) do que é **proposta**, ainda sem implementação.

## 1. O que existe hoje

Não há agendamento, recorrência nem gatilho por evento no produto (busca por `cron`, `schedule`, `recorr`, `agend`,
`next_run` em `backend/app`, 29/09). As peças reaproveitáveis:

| Peça | Onde | Uso possível |
|---|---|---|
| Espera com hora marcada: `retry_wait` + `next_retry_at`, `_hold` e `promote` | `backend/app/taskqueue/scheduler.py` (`_hold`), `taskqueue/repository.py` (`promote`) | "voltar depois" dentro de uma execução |
| Idempotência de execução: `runs.idempotency_key` único | `backend/migrations/001_init.sql` | identidade da ocorrência |
| Foto dos alvos (`runs.targets`) e sucessora (ADR-047) | `taskqueue/service.py`, `taskqueue/assistente.py` | repetir com os mesmos alvos |
| Gancho de fim de execução (`on_run_settled`) | `backend/app/state.py` | disparar a próxima decisão do pedido |
| Eventos gravados com id crescente | `backend/app/events.py` | fonte para gatilhos |
| Posse por prazo com CAS (`hosted_by`, `planned_by`) | `taskqueue/repository.py`, `taskqueue/scheduler.py` | evitar execução dupla com vários backends |
| Laços de fundo (saldos 600 s, curadoria 900 s, loja 60 s) | `state.py` (`AppState.start`), `vitrine.py` | molde de laço tolerante a falha; **sem trava de líder** |
| Orçamento por execução e por dia; bloqueio por saldo (ADR-051) | `backend/app/config.py`, `taskqueue/executor.py` | orçamento por pedido |
| Memória da persona, política, aprovações, aprendizado e preferências | `social/memory.py`, `social/policy.py`, `social/approvals.py`, `modules/learning/` | continuidade e limites |

Lacunas verificadas: entidade de pedido, ocorrência e gatilho; trava de líder para laços periódicos; teto de 4 h por
objetivo (`objective_timeout_s`); dependência e consolidação entre objetivos e entre personas; valor lido no relatório
(o 24.3 resolve dentro de uma execução); canal de aviso ao dono; assinante interno do `EventBus`; prioridade na fila.

## 2. Questões que a proposta precisa resolver

Cada uma vira seção deste documento, com alternativas, trade-offs, recomendação e aceite.

1. **Modelo do pedido** (26.2): objetivo, contexto, critérios de sucesso, duração, frequência, gatilhos, encerramento e
   grau de autonomia; pedido → ocorrência → execução.
2. **Memória e continuidade** (26.4): descobertas, decisões, progresso e pendências entre execuções.
3. **Agendamento confiável** (26.3): fuso horário, atrasos, disparos perdidos, sobreposição, retomada, cancelamento,
   tentativas e efeito duplicado.
4. **Planejamento adaptativo** (26.4): quando pesquisar de novo, aguardar, trocar de app ou pedir colaboração.
5. **Colaboração entre personas** (26.5): seleção por capacidade e contexto, divisão de tarefas, dependências,
   consolidação, prevenção de conflito e de ciclo de delegação. Para fora, uma conta por alvo (ADR-055), aprovação e
   nada de simular apoio de pessoas independentes.
6. **Recursos e custos** (26.6): orçamento de IA, frequência de consulta, concorrência, workers e aparelhos, saúde da
   rede (Fase 25), hibernação e reaproveitamento de resultado.
7. **Experiência** (26.7): acompanhar objetivo, estado, histórico, próximas execuções, evidências e relatórios; editar,
   pausar, retomar e cancelar.
8. **Integração** (26.8): como comandos, tarefas, skills, fila, scheduler, eventos e aprendizado evoluem, e o que
   justifica componente novo.

## 3. Casos que orientam (não limitam) a pesquisa

- **Acompanhamento de políticos:** como escolher ou esclarecer os perfis, período, frequência de coleta e de relatório;
  preservar as fontes e separar o que a amostra mostra de conclusões sobre o público em geral.
- **Pesquisa de produto:** consulta pontual e acompanhamento de preço — produto exato, custo total, disponibilidade,
  condições, confiabilidade da loja e momento da consulta.
- **Monitoramento de reputação:** detecção, evidência, crítica × alegação factual, verificação, aviso e resposta dentro
  das autorizações; personas com papéis (pesquisa, checagem, preparo de resposta), atuação transparente.

## 4. Fontes já consultadas (29/09/2026)

| Fonte | O que traz |
|---|---|
| [Temporal — Schedules](https://docs.temporal.io/schedule) | Política de sobreposição (Skip, BufferOne, BufferAll, CancelOther, AllowAll), janela de recuperação de disparos perdidos, idempotência das atividades |
| [APScheduler — guia do usuário](https://apscheduler.readthedocs.io/en/master/userguide.html) | Armazenamento persistente, `misfire_grace_time`, `coalesce` |

A lista completa, com data de acesso e o que cada fonte sustenta, é o entregável do 26.1 (RFC 5545 para recorrência,
base IANA para fuso, filas em banco, orquestração de agentes).

## 5. Ponto de partida (ADR-059, proposto)

Estado no backend e nos workers; um pedido gera ocorrências e cada ocorrência gera execuções comuns, com a identidade
da ocorrência como chave de idempotência; vocabulário de agendamento de sistemas consolidados; adotar biblioteca é
resultado da pesquisa, não premissa.
