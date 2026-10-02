import { describe, expect, it } from 'vitest';
import { descreverTentativa, rotuloDoBacklog, traduzirErro } from './falhasTexto';
import { aparelhoDaEtapa, frasesDaReceita } from './ResumoParaDecidir';
import { diaDoSinal, quemRegistrou } from './SinaisTab';

describe('texto de "O que mais falha" para quem opera', () => {
  it('o erro cru do provedor vira frase; o texto da execução passa como está', () => {
    // O texto que apareceu na tela do dono (validação de 02/10, captura 16).
    const cru = "IA indisponível: Requisição rejeitada pelo provedor: Error code: 400 - {'type': 'error', 'error': "
      + "{'type': 'invalid_request_error', 'message': 'Your credit balance is too low to access the Anthropic API'}}";
    expect(traduzirErro(cru)).toBe('Saldo da conta de IA esgotado: o provedor recusou a chamada.');
    expect(traduzirErro("Error code: 418 - {'type': 'error'}")).toBe('O provedor de IA recusou a chamada (código 418).');
    expect(traduzirErro('O app pede autenticação (campo de senha).')).toBe('O app pede autenticação (campo de senha).');
    expect(traduzirErro(null)).toBeNull();
  });

  it('a tentativa vira "aparelho · etapa · tentativa N"; fora do formato, nulo', () => {
    expect(descreverTentativa('r-20261002175257-0e9362:android-05:v1:open_app:a1')).toBe('android-05 · open_app · tentativa 1');
    expect(descreverTentativa('r-x')).toBeNull();
    expect(descreverTentativa(null)).toBeNull();
  });

  it('o estado do backlog em português; estado novo passa como veio', () => {
    expect(rotuloDoBacklog('open')).toBe('aberto');
    expect(rotuloDoBacklog('fixed_pending_proof')).toBe('corrigido, falta prova');
    expect(rotuloDoBacklog('outro')).toBe('outro');
  });
});

describe('o que a pessoa vê antes de aprovar', () => {
  it('os passos em frase, pelo texto do alvo, e o passo com efeito marcado', () => {
    // A receita 108 do QA Messenger (validação de 02/10): tocar em Enviar.
    const frases = frasesDaReceita([{
      indice: 0, ferramenta: 'tap', commit: true, parametros: [], segredo: false,
      alvo: [{ tipo: 'rid', rid: 'com.pocqa.messenger:id/send_button' }, { tipo: 'text', texto: 'Enviar' }],
    } as never]);
    expect(frases).toEqual([{ texto: 'Toca em “Enviar”', efeito: true }]);
    expect(frasesDaReceita([{ indice: 0, ferramenta: 'tap', commit: false, parametros: [], segredo: false,
      alvo: [{ tipo: 'rid', rid: 'com.x:id/botao' }] } as never])[0]?.texto).toBe('Toca em botao');
  });

  it('o aparelho sai da etapa de origem', () => {
    expect(aparelhoDaEtapa('r-20261002202101-eab933:android-05:v2:send_message_i1')).toBe('android-05');
    expect(aparelhoDaEtapa('lixo')).toBeNull();
  });
});

describe('sinais', () => {
  it('quem registrou em português e o dia como cabeçalho', () => {
    expect(quemRegistrou('panel')).toBe('pelo painel');
    expect(quemRegistrou('sistema')).toBe('pelo sistema');
    expect(quemRegistrou('flavio')).toBe('por flavio');
    const agora = new Date(2026, 9, 2, 12, 0, 0).getTime();
    expect(diaDoSinal(new Date(2026, 9, 2, 9, 0).toISOString(), agora)).toBe('Hoje');
    expect(diaDoSinal(new Date(2026, 9, 1, 9, 0).toISOString(), agora)).toBe('Ontem');
    expect(diaDoSinal(new Date(2026, 8, 29, 9, 0).toISOString(), agora)).toBe('29/09');
    expect(diaDoSinal(null, agora)).toBeNull();
  });
});
