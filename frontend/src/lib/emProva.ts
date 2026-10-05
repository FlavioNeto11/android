import type { EnsinadoEmProva } from '../api/types';

// 30.81 (adendo v1.65): o fluxo ensinado no modo treinamento nasce ativo, mas só vale para a persona que ensinou até a
// prova. O painel só diz isso; quem decide é o backend. O id da persona e o código do motivo ficam fora da frase.

/** O selo curto, ao lado do nome do fluxo. */
export const ROTULO_EM_PROVA = 'em prova';

/** O que "em prova" quer dizer, para o `title` do selo e para a nota do relatório. */
export function explicacaoEmProva(e: Pick<EnsinadoEmProva, 'persona'>): string {
  return e.persona
    ? 'Ensinado e ainda sem prova: só vale para a persona que ensinou. Passa a valer para todos quando uma prova real der certo ou uma pessoa confirmar que fica.'
    : 'Ensinado e ainda sem prova, e a gravação não tinha persona: não vale em aparelho nenhum até uma prova real dar certo ou uma pessoa confirmar que fica.';
}

/** Por que a prova automática não decide e o "Confirmar que fica" é da pessoa (`espera_a_pessoa` do Livro). Um texto por
 *  motivo do contrato; o código fica no `title`, nunca sai cru na tela. */
const ESPERA_DA_PESSOA: Readonly<Record<string, string>> = {
  classe_c: 'O que foi ensinado é de risco alto (ou sem avaliação de agora): a prova automática não o cobre.',
  tentativas_esgotadas: 'A prova do que foi ensinado tentou 3 vezes sem veredito; a decisão fica com a pessoa.',
  efeito_real: 'O comando tem efeito num app real; por enquanto só a leitura se prova sozinha.',
  sessao_ou_autenticacao: 'O comando entra numa conta: isso fica com a pessoa.',
  credencial: 'O comando parece ter uma credencial; a prova automática não o repete.',
  sem_origem: 'Falta um exemplo de cada parâmetro para a prova automática repetir o comando.',
  sem_caminho: 'O fluxo de agora não passa mais pela etapa que a prova repetiria.',
};

const ESPERA_DESCONHECIDA = 'A prova automática não decide este fluxo; a decisão fica com a pessoa.';

/** O motivo de `espera_a_pessoa` em português; um código que o painel ainda não conhece cai na frase geral. */
export function textoDaEsperaDaPessoa(codigo: string): string {
  return ESPERA_DA_PESSOA[codigo] ?? ESPERA_DESCONHECIDA;
}
