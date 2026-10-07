/**
 * 31.189 (adendo v1.109): a proposta do ensino para EXIBIR. O central manda, ao lado da `proposal`, a `proposal_exibicao`: a mesma
 * proposta com todo dado da persona trocado por `{nome}`. A tela mostra a cópia; o que o painel DEVOLVE na prévia e no salvar é a
 * `proposal` (com as edições da pessoa), nunca a cópia. Aqui mora a regra de qual texto aparece:
 *  - texto que a pessoa NÃO mexeu: a cópia mascarada;
 *  - texto que a pessoa mexeu: o que ela escreveu (é dela; mostrar a cópia seria mostrar outro texto).
 * Os identificadores (`name`, `capability`, `inputs`, `seq`…) vêm iguais nas duas, MENOS a `key` da etapa: na cópia ela também é mascarada
 * (o `_` conta como separador). A etapa da cópia é por isso a que ocupa a MESMA posição da etapa original de mesma `key`; o parâmetro
 * casa por `name`. O painel não cria, tira nem reordena etapas, então a posição da original é estável.
 */
import type { TrainingProposal, TrainingStep } from '../../api/types';

/** A `proposal` como o central a mandou (sem edição) e a cópia para exibir; `copia` nula = o central não mandou (backend anterior). */
export interface Exibicao { original: TrainingProposal | null; copia: TrainingProposal | null | undefined }

/** Os dois textos de um campo editável: o que o central mandou e a cópia mascarada. */
export interface CampoRef { original?: string; exibido?: string }

const SEM_EXIBICAO: Exibicao = { original: null, copia: null };
export const semExibicao = (): Exibicao => SEM_EXIBICAO;

/** O texto que aparece: a cópia mascarada se a pessoa não mexeu, o dela se mexeu; sem cópia, o de sempre. */
export function textoNaTela(atual: string, original: string | undefined, exibido: string | undefined): string {
  return original !== undefined && exibido !== undefined && atual === original ? exibido : atual;
}

export function etapaDe(p: TrainingProposal | null | undefined, key: string): TrainingStep | undefined {
  return p?.steps.find((s) => s.key === key);
}

/** A etapa da CÓPIA que corresponde à etapa `key` da original: a da mesma posição (a `key` da cópia vem mascarada). */
export function etapaDaCopia(e: Exibicao, key: string): TrainingStep | undefined {
  const i = e.original?.steps.findIndex((s) => s.key === key) ?? -1;
  return i < 0 ? undefined : e.copia?.steps[i];
}

/** A referência (original e exibido) do texto de um campo da proposta. */
export const refs = {
  comando: (e: Exibicao): CampoRef => ({ original: e.original?.command_template, exibido: e.copia?.command_template }),
  titulo: (e: Exibicao, key: string): CampoRef => ({ original: etapaDe(e.original, key)?.title, exibido: etapaDaCopia(e, key)?.title }),
  objetivo: (e: Exibicao, key: string): CampoRef => ({ original: etapaDe(e.original, key)?.goal, exibido: etapaDaCopia(e, key)?.goal }),
  conferenciaValor: (e: Exibicao, key: string): CampoRef => ({ original: etapaDe(e.original, key)?.postcondition?.value, exibido: etapaDaCopia(e, key)?.postcondition?.value }),
  conferenciaDescricao: (e: Exibicao, key: string): CampoRef => ({ original: etapaDe(e.original, key)?.postcondition?.description, exibido: etapaDaCopia(e, key)?.postcondition?.description }),
  exemplo: (e: Exibicao, nome: string): CampoRef => ({
    original: e.original?.parameters.find((p) => p.name === nome)?.example, exibido: e.copia?.parameters.find((p) => p.name === nome)?.example,
  }),
};

/** O campo está mascarado na tela: o valor é o que o central mandou e a cópia é outra coisa. */
export const estaMascarado = (valor: string, r: CampoRef): boolean => r.original !== undefined && r.exibido !== undefined && r.exibido !== r.original && valor === r.original;

/**
 * A proposta como ela aparece nas partes que só se LEEM (resumo, comando, exemplos, perguntas, respostas, descartes, etapas): cada texto
 * pela regra de `textoNaTela`. A estrutura e os identificadores são os da proposta que a pessoa está editando.
 */
export function paraExibir(p: TrainingProposal, e: Exibicao): TrainingProposal {
  const o = e.original;
  const c = e.copia;
  if (!o || !c) return p;
  const t = textoNaTela;
  return {
    ...p,
    summary: t(p.summary, o.summary, c.summary),
    command_template: t(p.command_template, o.command_template, c.command_template),
    parameters: p.parameters.map((x) => {
      const po = o.parameters.find((y) => y.name === x.name);
      const pc = c.parameters.find((y) => y.name === x.name);
      return { ...x, example: t(x.example, po?.example, pc?.example), description: t(x.description, po?.description, pc?.description) };
    }),
    questions: p.questions.map((q, i) => t(q, o.questions[i], c.questions[i])),
    answers: p.answers?.map((a, i) => ({ question: t(a.question, o.answers?.[i]?.question, c.answers?.[i]?.question), answer: t(a.answer, o.answers?.[i]?.answer, c.answers?.[i]?.answer) })),
    discarded: p.discarded.map((d) => ({ ...d, why: t(d.why, o.discarded.find((x) => x.seq === d.seq)?.why, c.discarded.find((x) => x.seq === d.seq)?.why) })),
    steps: p.steps.map((s) => {
      const so = etapaDe(o, s.key);
      const sc = etapaDaCopia(e, s.key);
      return {
        ...s,
        title: t(s.title, so?.title, sc?.title),
        goal: t(s.goal, so?.goal, sc?.goal),
        postcondition: {
          ...s.postcondition,
          value: t(s.postcondition.value, so?.postcondition?.value, sc?.postcondition?.value),
          description: t(s.postcondition.description, so?.postcondition?.description, sc?.postcondition?.description),
        },
      };
    }),
  };
}
