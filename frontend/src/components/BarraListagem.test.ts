import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

// Lido do disco: no vitest o `?raw` de um CSS Module não chega como texto.
const css = readFileSync(new URL('./BarraListagem.module.css', import.meta.url), 'utf-8');

/**
 * RF-43 (prova simulada 13): "Limpar filtros" tinha 24 px de alvo (104x24), abaixo dos 32 px do `--hit-min`. Ele só
 * aparece com filtro ativo, por isso a medição da tarefa 07 (sem filtro) não o pegou. O jsdom não mede pixels: o teste
 * lê a regra da classe e confere a receita da tarefa 07 (área mínima pelo token, com margem negativa que devolve à
 * linha a mesma altura de antes). A medida na tela fica para o navegador.
 */
function regra(seletor: string): string {
  const m = css.match(new RegExp(`(^|\\n)${seletor.replace('.', '\\.')}\\s*\\{([^}]*)\\}`));
  if (!m) throw new Error(`regra ${seletor} ausente`);
  return m[2]!;
}

describe('BarraListagem — alvo de "Limpar filtros" (RF-43)', () => {
  it('a área de clique mínima vem do token --hit-min', () => {
    expect(regra('.limpar')).toMatch(/min-height:\s*var\(--hit-min\)/);
  });

  it('a margem vertical negativa devolve à linha a altura visual de antes (24 px = 32 − 2 × 4)', () => {
    const r = regra('.limpar');
    expect(r).toMatch(/margin:\s*-4px\s+0/);
    // O respiro horizontal e o visual do link continuam os de antes.
    expect(r).toMatch(/padding:\s*0\s+6px/);
    expect(r).toMatch(/background:\s*transparent/);
  });
});
