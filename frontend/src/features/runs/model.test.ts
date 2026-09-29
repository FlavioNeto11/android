import { describe, expect, it } from 'vitest';
import { appLabel } from './model';

const CATALOGO = [{ id: 'qa', name: 'QA Messenger' }, { id: 'notes', name: 'Notas' }];

/** Item 24.6: o nome do app pelo id, para o Plano e a Execução mostrarem o app de cada etapa. */
describe('appLabel', () => {
  it('resolve pelo catálogo', () => {
    expect(appLabel(CATALOGO, 'notes')).toBe('Notas');
  });

  it('app fora do catálogo (removido, ou ainda não carregado): o próprio id, nunca inventa nome', () => {
    expect(appLabel(CATALOGO, 'tiktok')).toBe('tiktok');
    expect(appLabel([], 'qa')).toBe('qa');
  });

  it('sem id (nulo, ausente ou vazio): null', () => {
    expect(appLabel(CATALOGO, null)).toBeNull();
    expect(appLabel(CATALOGO, undefined)).toBeNull();
    expect(appLabel(CATALOGO, '')).toBeNull();
  });
});
