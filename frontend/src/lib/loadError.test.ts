import { describe, expect, it } from 'vitest';
import { ApiError } from '../api/client';
import { SEM_RESPOSTA, toLoadError } from './loadError';

/** P5 da validação do deploy 3: o estado de erro não mostra o texto do navegador em inglês. Prova `simulated`. */
describe('toLoadError', () => {
  it('sem resposta HTTP, a tela diz "Sem resposta do servidor." e o texto do navegador fica em `tecnico`', () => {
    for (const cru of ['Failed to fetch', 'Load failed', 'NetworkError when attempting to fetch resource.']) {
      const e = toLoadError(new TypeError(cru));
      expect(e.message).toBe(SEM_RESPOSTA);
      expect(e.tecnico).toBe(cru);
      expect(e.hint).toMatch(/backend não respondeu/);
    }
  });

  it('com resposta HTTP, a mensagem do backend segue como veio, mesmo com o código "network"', () => {
    expect(toLoadError(new ApiError(503, 'not_ready', 'O aprendizado ainda não foi composto.')))
      .toEqual({ message: 'O aprendizado ainda não foi composto.', hint: expect.any(String) });
    expect(toLoadError(new ApiError(409, 'network', 'A rede do aparelho está em uso.')).message)
      .toBe('A rede do aparelho está em uso.');
  });

  it('o tempo esgotado continua com a própria frase', () => {
    expect(toLoadError(new DOMException('x', 'AbortError')).message).toMatch(/demorou demais/);
  });
});
