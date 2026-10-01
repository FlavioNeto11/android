# Pedido de esclarecimento de privacidade à TypeSafe — RASCUNHO, NÃO ENVIADO

> **Estado: rascunho para revisão do dono. Nada foi enviado** (nem e-mail, nem formulário). Enviar é decisão do dono; depois do envio o
> `PRIVATE_CODE_GATE` passa de `BLOCKED` para `WAITING_VENDOR` (ver [`jev-pilot-closure.md`](jev-pilot-closure.md) §5 e
> `experiments/jev/private_gate.json`). Antes de enviar: trocar os campos entre colchetes, decidir se identifica a organização e o
> volume, e **não anexar código nem chave**. Destinatários oficiais: `sales@typesafe.ai` (ZDR/enterprise, segundo `docs.typesafe.ai/legal`)
> e `privacy@typesafe.ai` (Trust Center). Respostas só valem como evidência se vierem **por escrito** e, de preferência, em
> DPA/termo contratual.

---

**To:** sales@typesafe.ai, privacy@typesafe.ai
**Subject:** Written answers requested: data retention, telemetry and Zero Data Retention for POST /v1/systemone

Hello,

We are evaluating Jev (`jev-1.13.0`) through `POST /v1/systemone` as a *context-selection* step for a coding assistant: Choice
questions over a repository map and over code chunks. We have run small technical tests on public open-source repositories only. Before
we would ever send non-public source code, we need **written** answers to the questions below. Please answer for the **standard API
account** and, separately, for **Zero Data Retention (ZDR)** if it can be enabled for us. Our volume would be small (tens of requests).

1. What is the exact retention period of the `state`/Input sent to `POST /v1/systemone` on the standard API?
2. Does the whole or partial Input appear in: request logs; abuse/safety logs; traces; backups; telemetry; diagnostics?
3. What is the retention period of each of those categories?
4. What exactly are the "learnings" in "Telemetry" (MCA §4.3)? Can they contain: excerpts of the Input; embeddings; derived features;
   reversible or identifying hashes; semantic representations of the code?
5. Is there human review of requests or content in: abuse monitoring; support; debugging; incident response?
6. Can Zero Data Retention be requested for this account/API? Under what conditions and lead time?
7. If ZDR is enabled, does it explicitly cover: Input/state; Output; logs; telemetry; abuse monitoring; caches; backups; subprocessors?
8. With ZDR, do AWS, Modal, Nebius or CoreWeave keep any copy, log, cache or derived representation of the Input?
9. Does ZDR also prevent the Input or anything derived from it from appearing in the MCA §4.3 "Telemetry learnings", and does it limit the
   perpetual right in MCA §4.1(c)?
10. Is there a DPA or contract term that makes these guarantees binding? (The published DPA covers "Customer Personal Data"; source code is
    generally not personal data.)
11. Can you provide: the SOC 2 Type II report (the Trust Center lists "SOC 2 Type II - 2026"); the current subprocessor list; documentation of
    data deletion; the processing architecture and region?
12. Is there a processing region outside the United States?

A short written reply, or a pointer to the exact contract language, per question is enough. If a question cannot be answered, please say so
explicitly. Thank you.

[Name, role, organization, contact]

---

## Referência interna (não vai na mensagem)

O que os documentos oficiais **já** dizem sobre cada pergunta (lidos em 2026-10-01) está em
[`jev-pilot-closure.md`](jev-pilot-closure.md) §4; a mensagem só pergunta o que o texto oficial deixa em aberto ou ambíguo.
