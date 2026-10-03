import { describe, expect, it } from 'vitest';
import { cpuDoEmulador } from './FocusInfoSections';

// Validação do deploy 4 (03/10): "CPU 108%" sem referência lia como "acima do máximo"; o % é de UM núcleo do host.
describe('cpuDoEmulador', () => {
  it('mostra o % com quantos núcleos do host ele ocupa', () => {
    expect(cpuDoEmulador(108)).toBe('108% (≈1,1 núcleo)');
    expect(cpuDoEmulador(250)).toBe('250% (≈2,5 núcleos)');
    expect(cpuDoEmulador(7.5)).toMatch(/\(≈0,1 núcleo\)$/);
  });

  it('sem medição fica o traço', () => {
    expect(cpuDoEmulador(null)).toBe('—');
    expect(cpuDoEmulador(undefined)).toBe('—');
  });
});
