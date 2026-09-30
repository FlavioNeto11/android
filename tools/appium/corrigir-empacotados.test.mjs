// `node --test` (sem dependência): árvores falsas de node_modules numa pasta temporária.
import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';

import { CORRECOES, corrigir, executar, menor } from './corrigir-empacotados.mjs';

const C = CORRECOES[0];

function pacote(pasta, versao, extra = {}) {
  mkdirSync(pasta, { recursive: true });
  writeFileSync(join(pasta, 'package.json'), JSON.stringify({ name: C.pacote, version: versao, ...extra }));
  writeFileSync(join(pasta, 'index.js'), `module.exports = ${JSON.stringify(versao)};\n`);
}

function arvore({ empacotado, raiz, driver = true }) {
  const base = mkdtempSync(join(tmpdir(), 'empacotados-'));
  const modulos = join(base, 'node_modules');
  if (driver) mkdirSync(join(modulos, C.dentro_de, 'node_modules'), { recursive: true });
  if (empacotado) pacote(join(modulos, C.dentro_de, 'node_modules', C.pacote), empacotado);
  if (raiz) pacote(join(modulos, C.pacote), raiz);
  return base;
}

const lido = (base) => JSON.parse(readFileSync(
  join(base, 'node_modules', C.dentro_de, 'node_modules', C.pacote, 'package.json'), 'utf8')).version;
const calado = { log() {} };

test('compara versões por número, não por texto', () => {
  assert.equal(menor('5.0.9', '5.0.12'), true);
  assert.equal(menor('5.0.12', '5.0.12'), false);
  assert.equal(menor('5.1.0', '5.0.12'), false);
  assert.throws(() => menor('5.0', '5.0.12'));
});

test('troca a cópia empacotada vulnerável pela da raiz e o disco passa a dizer a corrigida', () => {
  const base = arvore({ empacotado: '5.0.9', raiz: '5.0.12' });
  try {
    const r = corrigir(base, C);
    assert.equal(r.estado, 'trocado');
    assert.equal(lido(base), '5.0.12');
    assert.equal(executar(['--raiz', base, '--conferir'], calado), 0);
  } finally { rmSync(base, { recursive: true, force: true }); }
});

test('--conferir só lê: reprova a vulnerável e não mexe no disco', () => {
  const base = arvore({ empacotado: '5.0.9', raiz: '5.0.12' });
  try {
    assert.equal(executar(['--raiz', base, '--conferir'], calado), 1);
    assert.equal(lido(base), '5.0.9');
  } finally { rmSync(base, { recursive: true, force: true }); }
});

test('sem a corrigida na raiz (ausente, ainda vulnerável ou de outra versão maior), recusa e não apaga nada', () => {
  for (const raiz of [null, '5.0.11', '6.0.0']) {
    const base = arvore({ empacotado: '5.0.9', raiz });
    try {
      assert.equal(corrigir(base, C).estado, 'impossivel');
      assert.equal(lido(base), '5.0.9');
      assert.equal(executar(['--raiz', base], calado), 1);
    } finally { rmSync(base, { recursive: true, force: true }); }
  }
});

test('já corrigida, ou o driver deixou de empacotar: nada a fazer', () => {
  for (const empacotado of ['5.0.12', '5.1.3', null]) {
    const base = arvore({ empacotado, raiz: '5.0.12' });
    try {
      assert.equal(corrigir(base, C).estado, 'ok');
      assert.equal(executar(['--raiz', base, '--conferir'], calado), 0);
    } finally { rmSync(base, { recursive: true, force: true }); }
  }
});

test('sem o driver instalado é erro (rodou antes do npm ci), não sucesso', () => {
  const base = arvore({ empacotado: null, raiz: '5.0.12', driver: false });
  try {
    assert.equal(corrigir(base, C).estado, 'impossivel');
    assert.equal(executar(['--raiz', base, '--conferir'], calado), 1);
  } finally { rmSync(base, { recursive: true, force: true }); }
});
