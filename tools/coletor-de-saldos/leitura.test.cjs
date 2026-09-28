// node --test tools/coletor-de-saldos — o leitor contra o texto REAL das três telas (copiado em 28/09/2026).
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { extrair, numero, contaDoHost, topoValido } = require('./leitura.js');

const CLAUDE = 'Billing\nCredit balance\nCredits are used by the API, Claude Code, and playground. Buy credits as needed or set up auto-reload.\nUS$ 5,19\nSet up auto-reload\nBuy credits';
const OPENAI = 'Billing\nOverview\nPay as you go\nAPI credit balance\n$8.25\nAuto-reload is\nOFF';
const GOOGLE = 'Pagamentos\nSaldo de crédito\nR$ 29,37\nVer resumo de crédito\nPré-pago\nR$ 30,00 adicionado em 28 de set.';

test('lê o saldo das três telas reais', () => {
  assert.deepEqual(extrair('anthropic', CLAUDE), { balance: 5.19, currency: 'USD' });
  assert.deepEqual(extrair('openai', OPENAI), { balance: 8.25, currency: 'USD' });
  assert.deepEqual(extrair('gemini', GOOGLE), { balance: 29.37, currency: 'BRL' });
});

test('com o cartão ainda carregando, não pega o próximo dinheiro da página', () => {
  const carregando = 'Credit balance\nCredits are used by the API, Claude Code, and playground. Buy credits as needed or set up auto-reload.\nLoading\nSet up auto-reload\nBuy credits\nAuto-reload is off.\nSpend limits\n$50.81 spent';
  assert.equal(extrair('anthropic', carregando), null);
  // O rótulo aparece antes num menu, sem valor; a segunda ocorrência é a do cartão.
  assert.deepEqual(extrair('openai', 'API credit balance\nhelp\n' + 'x'.repeat(80) + '\nAPI credit balance\n$8.25'), { balance: 8.25, currency: 'USD' });
});

test('página carregando, deslogada ou sem o cartão: nada', () => {
  assert.equal(extrair('anthropic', 'Billing\nCredit balance\nCredits are used by the API'), null);
  assert.equal(extrair('openai', 'Log in\nWelcome back'), null);
  assert.equal(extrair('gemini', ''), null);
  assert.equal(extrair('xpto', CLAUDE), null);
});

test('números em pt-BR e en, com milhar', () => {
  assert.equal(numero('5,19'), 5.19);
  assert.equal(numero('8.25'), 8.25);
  assert.equal(numero('1.467,58'), 1467.58);
  assert.equal(numero('1,467.58'), 1467.58);
  assert.equal(numero('1.467'), 1467);
  assert.equal(numero('29'), 29);
  assert.equal(numero('-0,50'), -0.5);
  assert.deepEqual(extrair('gemini', 'Credit balance\nUS$ 1,234.50'), { balance: 1234.5, currency: 'USD' });
});

test('só aceita o número quando o topo da aba é o console daquela conta', () => {
  assert.equal(contaDoHost('payments.google.com'), 'gemini');
  assert.equal(contaDoHost('example.com'), null);
  assert.equal(topoValido('gemini', 'https://aistudio.google.com/billing?billing=x'), true);
  assert.equal(topoValido('gemini', 'https://play.google.com/store'), false);    // iframe de pagamento em outro lugar
  assert.equal(topoValido('openai', 'https://platform.claude.com/settings/billing'), false);
  assert.equal(topoValido('anthropic', 'nao-e-url'), false);
});
