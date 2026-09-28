# Coletor de saldos da Central (ADR-051)

Extensão do Chrome que lê o saldo das contas de IA onde ele existe, na página de faturamento de cada console,
na sessão deste Chrome:

- Anthropic: Claude Console, Billing;
- OpenAI: Billing overview;
- Google AI Studio: Faturamento, dentro do iframe de `payments.google.com`.

A extensão manda só o número para a plataforma (`POST /api/ai/balances/{conta}`, `source: "coletor"`). Nenhuma
senha, cookie ou dado de pagamento sai do navegador. Nenhum dos três provedores publica o saldo pré-pago por API
(pesquisado em 28/09/2026), por isso ela lê a tela.

## Instalar (uma vez)

1. No Chrome logado nas três contas, abra `chrome://extensions` e ligue o **Modo do desenvolvedor**.
2. Clique em **Carregar sem compactação** e escolha a pasta `C:\git\android\tools\coletor-de-saldos`.
3. O ID tem de ser `mnkjgogdfdmednilcgicegnfepelpbia`, fixado pela chave no `manifest.json`. É esse ID que o backend
   aceita, e só para registrar leitura de saldo.
4. Em **Detalhes → Opções da extensão**, confira o endereço da plataforma (padrão `http://127.0.0.1:8000`) e clique
   em **Coletar agora**. A tabela mostra o resultado de cada conta.

## Como funciona

- A cada 60 minutos (configurável, mínimo 15), a extensão pede à plataforma a lista de consoles
  (`GET /api/ai/balances`, campo `console`). Depois abre os três numa janela minimizada, lê o saldo e fecha a
  janela.
- Se uma página só desenha com a janela visível, a próxima coleta usa uma janela normal sem foco.
- Quando você abre um console por conta própria, a leitura também vale, no máximo uma vez a cada 10 minutos por
  conta.
- Se o console estiver deslogado ou a tela tiver mudado, nada é enviado. A plataforma avisa pela leitura antiga.

## Testar o leitor

```bash
node --test tools/coletor-de-saldos/leitura.test.cjs
```
