/**
 * O JSON FIXO da tela Operação enquanto a rota `GET /api/operacoes` não existe no central (rascunho do adendo v1.94 da Jev,
 * commit 9da5017d). Dado inventado, de propósito genérico (persona 01…30, contas `@exemplo_NN`): não é nenhuma persona nem
 * conta real, e a tela avisa que é exemplo. Segue o formato do contrato e conta o cenário que o dono pediu para NÃO esconder:
 * 30 solicitados, poucas contas com sessão, o resto parado no estágio "Conta" com o motivo.
 */
import { ESTAGIOS, MOTIVO_DO_LIMITE } from './modelo';

const N = 30;

const doisDigitos = (n: number) => String(n).padStart(2, '0');
const ate = (n: number) => ESTAGIOS.slice(0, n).map((e, i) => ({ estagio: e.id, em: `2026-10-06T16:4${Math.min(i, 9)}:00Z` }));

const base = (n: number) => ({
  profile_id: `ig-${doisDigitos(n)}`, persona_nome: `Persona ${doisDigitos(n)}`, app_id: 'com.instagram.android', account_id: null as string | null,
  conta: null as string | null, instance_id: null as string | null, run_id: null as string | null,
  estagio: 'persona', estagios: ate(1), estado: 'bloqueado', motivo: 'sem conta', resultado: null as unknown,
});

const concluido = (n: number, aparelho: string, texto: string, verificada: boolean) => ({
  ...base(n), account_id: `acc-${doisDigitos(n)}`, conta: `@exemplo_${doisDigitos(n)}`, instance_id: aparelho, run_id: `r-exemplo-${doisDigitos(n)}`,
  estagio: verificada ? 'resultado_verificado' : 'acao_executada', estagios: ate(verificada ? 14 : 13), estado: 'concluido', motivo: null,
  resultado: {
    texto, conhecimento_ids: ['fluxo:comentar-no-post', 'licao:voz-da-persona'], evidencia_id: 1000 + n,
    acao_final: { tipo: 'CREATE_COMMENT', verificada, evidencia_id: verificada ? 2000 + n : null },
  },
});

const alvos: Record<string, unknown>[] = Array.from({ length: N }, (_, i) => base(i + 1));
alvos[0] = concluido(1, 'android-04', 'Ficou ótimo, parabéns pelo lançamento!', true);
alvos[1] = concluido(2, 'android-05', 'Adorei o acabamento, já quero o meu.', true);
alvos[2] = concluido(3, 'android-07', 'Muito bom ver a novidade chegando.', false);
alvos[3] = {
  ...base(4), account_id: 'acc-04', conta: '@exemplo_04', instance_id: 'android-08', run_id: 'r-exemplo-04',
  estagio: 'acao_preparada', estagios: ate(12), estado: 'bloqueado', motivo: MOTIVO_DO_LIMITE,
  resultado: { texto: 'Gostei do detalhe da embalagem.', conhecimento_ids: ['fluxo:comentar-no-post'], evidencia_id: 1004, acao_final: null },
};
alvos[4] = { ...base(5), account_id: 'acc-05', conta: '@exemplo_05', estagio: 'conta', estagios: ate(2), motivo: 'sem sessão' };

export const OPERACAO_DE_EXEMPLO = {
  id: 'op-exemplo',
  command: 'Ler o post da loja, recuperar o conhecimento do assunto e comentar com a voz de cada persona, verificando o resultado.',
  app_id: 'com.instagram.android',
  acao_final: 'preparar',
  status: 'concluida_com_bloqueios',
  created_at: '2026-10-06T16:40:00Z',
  finished_at: null,
  capacidade: {
    solicitados: N, contas_existentes: 5, sessoes_validas: 4, contas_disponiveis: 4, concluidas: 3, bloqueadas: 27, em_curso: 0,
    motivos: { 'sem conta': 25, 'sem sessão': 1, [MOTIVO_DO_LIMITE]: 1 },
  },
  alvos,
  custo_usd: 0.31,
} as const;
