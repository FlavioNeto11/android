/**
 * O JSON FIXO da tela Operação até o adendo v1.94 da Jev existir (`POST/GET /api/operacoes`). Dado inventado, de propósito
 * genérico (persona 01…30, contas `@exemplo_NN`): não é nenhuma persona nem conta real, e a tela avisa que é exemplo. Segue o
 * formato que `lerOperacao` aceita e conta o cenário que o dono pediu para NÃO esconder: 30 solicitados, poucas contas com
 * sessão, o resto parado no estágio "Conta" com o motivo.
 */
import { ESTAGIOS, type EstagioId } from './modelo';

const N = 30;
const doisDigitos = (n: number) => String(n).padStart(2, '0');
const ate = (id: EstagioId) => id; // só para o leitor do arquivo: "alcançou até este estágio"

interface Linha {
  id: string; profile_id: string; persona: string; account_id: string | null; conta: string | null; sessao: string | null; app: string;
  instance_id: string | null; servidor: string | null; estado: string; estagio: EstagioId | null; bloqueio: { estagio: EstagioId; motivo: string } | null;
  conhecimento_n: number | null; acao: string | null; verificada: string | null; evidencia_id: number | null; run_id: string | null;
}

const base = (n: number): Linha => ({
  id: `alvo-${doisDigitos(n)}`, profile_id: `ig-${doisDigitos(n)}`, persona: `Persona ${doisDigitos(n)}`, account_id: null, conta: null, sessao: 'sem_conta',
  app: 'Instagram', instance_id: null, servidor: null, estado: 'bloqueado', estagio: ate('persona'),
  bloqueio: { estagio: 'conta', motivo: 'A persona não tem conta do Instagram vinculada.' },
  conhecimento_n: null, acao: null, verificada: null, evidencia_id: null, run_id: null,
});

const concluido = (n: number, aparelho: string, servidor: string, verificada: 'sim' | 'nao_conferida', texto: string): Linha => ({
  ...base(n), account_id: `acc-${doisDigitos(n)}`, conta: `@exemplo_${doisDigitos(n)}`, sessao: 'conectada', instance_id: aparelho, servidor,
  estado: 'concluido', estagio: verificada === 'sim' ? 'resultado_verificado' : 'acao_executada', bloqueio: null, conhecimento_n: 4,
  acao: texto, verificada, evidencia_id: verificada === 'sim' ? 1000 + n : null, run_id: `r-exemplo-${doisDigitos(n)}`,
});

const linhas: Linha[] = Array.from({ length: N }, (_, i) => base(i + 1));
linhas[0] = concluido(1, 'android-04', 'Notebook da LAN', 'sim', 'Comentário publicado no post da loja: “Ficou ótimo, parabéns pelo lançamento!”');
linhas[1] = concluido(2, 'android-05', 'Notebook da LAN', 'sim', 'Comentário publicado no post da loja: “Adorei o acabamento, já quero o meu.”');
linhas[2] = concluido(3, 'android-07', 'Notebook da LAN', 'nao_conferida', 'Comentário enviado; a confirmação na tela não foi lida.');
linhas[3] = {
  ...base(4), account_id: 'acc-04', conta: '@exemplo_04', sessao: 'conectada', instance_id: 'android-08', servidor: 'Notebook da LAN',
  estado: 'bloqueado', estagio: 'resposta_gerada',
  bloqueio: { estagio: 'interface_de_comentario', motivo: 'A tela de comentário do post não abriu depois de duas tentativas.' },
  conhecimento_n: 4, acao: 'Ação preparada, aguardando a tela de comentário.', run_id: 'r-exemplo-04',
};
linhas[4] = {
  ...base(5), account_id: 'acc-05', conta: '@exemplo_05', sessao: 'vencida', estagio: 'conta',
  bloqueio: { estagio: 'sessao', motivo: 'A sessão da conta venceu e a entrada espera uma pessoa.' },
};

export const OPERACAO_DE_EXEMPLO = {
  id: 'op-exemplo',
  objetivo: 'Ler o post da loja, recuperar o conhecimento do assunto e comentar com a voz de cada persona, verificando o resultado.',
  app: 'Instagram',
  estado: 'em_andamento',
  criada_em: '2026-10-06T16:40:00Z',
  capacidade: {
    solicitados: N, contas_existentes: 5, sessoes_validas: 4, disponiveis: 4, concluidas: 3, bloqueadas: 27,
    motivos: [
      { motivo: 'A persona não tem conta do Instagram vinculada.', n: 25 },
      { motivo: 'A sessão da conta venceu e a entrada espera uma pessoa.', n: 1 },
      { motivo: 'A tela de comentário do post não abriu depois de duas tentativas.', n: 1 },
    ],
  },
  alvos: linhas,
} as const;

/** Os estágios, reexportados para o teste conferir que o exemplo usa só os do dono. */
export const ESTAGIOS_DO_EXEMPLO = ESTAGIOS.map((e) => e.id);
