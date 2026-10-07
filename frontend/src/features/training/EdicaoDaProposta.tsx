/**
 * 31.90-E: o que a pessoa pode corrigir na proposta da IA além do texto: o que cada etapa CONFERE (a pós-condição) e o
 * exemplo de cada parâmetro. O `save` já valida a proposta editada (31.83); aqui só se edita e a recusa dele aparece no
 * botão Salvar, como as outras. O nome do parâmetro não se edita: ele está no comando.
 *
 * Só se edita o que o salvar PRESERVA (`TrainingSkills._preparar`): a etapa com ação do catálogo é refeita pelo catálogo,
 * com a conferência dele (por isso não tem editor), e do parâmetro só o exemplo entra no fluxo e na prova automática.
 */
import { Disclosure } from '../../components/Disclosure';
import { Select } from '../../components/Field';
import type { TrainingProposal, TrainingStep } from '../../api/types';
import { CampoMascarado } from './CampoMascarado';
import { refs, semExibicao, type Exibicao } from './exibicao';
import styles from './Training.module.css';

type PosCondicao = TrainingStep['postcondition'];
type Tipo = PosCondicao['kind'];

const TIPOS: { kind: Tipo; rotulo: string; valor: string | null }[] = [
  { kind: 'text_visible', rotulo: 'Aparece um texto', valor: 'Texto que aparece' },
  { kind: 'element_present', rotulo: 'Existe um elemento', valor: 'Elemento (id ou texto)' },
  { kind: 'app_foreground', rotulo: 'O app está na frente', valor: 'Pacote do app' },
  { kind: 'model_judged', rotulo: 'A IA julga pela tela', valor: null },
];

/** A etapa que muda algo fora do aparelho e ainda não diz como comprovar: o salvar a recusa (`pos_condicao_vazia`). */
export const semComprovacao = (s: Pick<TrainingStep, 'side_effect' | 'postcondition'>): boolean =>
  s.side_effect && !(s.postcondition?.value ?? '').trim() && !(s.postcondition?.description ?? '').trim();

/** "Editar o que a etapa confere": tipo, valor e descrição. Abre sozinho na etapa com efeito que ainda não se comprova. */
export function EditorDaPosCondicao({ indice, etapa, exibicao = semExibicao(), onChange }: {
  indice: number;
  etapa: TrainingStep;
  /** 31.189: a cópia mascarada da proposta; os dois textos abaixo mostram a cópia até a pessoa pedir para editar. */
  exibicao?: Exibicao;
  onChange: (pos: PosCondicao) => void;
}) {
  const pos = etapa.postcondition ?? { kind: 'model_judged', value: '', description: '' };
  const n = indice + 1;
  const tipo = TIPOS.find((t) => t.kind === pos.kind);
  // `items_collected` (coleta) é lida pelo executor: aparece como está, e quem edita escolhe outro tipo se quiser trocar.
  const opcoes = tipo ? TIPOS : [{ kind: pos.kind, rotulo: pos.kind, valor: 'Valor' }, ...TIPOS];
  const mudar = (parcial: Partial<PosCondicao>) => onChange({ ...pos, ...parcial });
  // A etapa com ação do catálogo sai do salvar com a conferência do catálogo: uma edição aqui seria mostrada e jogada fora.
  if (etapa.capability) {
    return <p className={styles.muted}>O que esta etapa confere vem da ação do catálogo “{etapa.capability}” e não muda por aqui.</p>;
  }
  return (
    <Disclosure bare summary="Editar o que a etapa confere" openWhen={semComprovacao(etapa)}>
      {() => (
        <div className={styles.editor}>
          <Select aria-label={`Tipo de conferência da etapa ${n}`} value={pos.kind} onChange={(e) => mudar({ kind: e.target.value as Tipo })}>
            {opcoes.map((t) => <option key={t.kind} value={t.kind}>{t.rotulo}</option>)}
          </Select>
          {(tipo ?? opcoes[0])?.valor ? (
            <CampoMascarado rotulo={`${(tipo ?? opcoes[0])!.valor} (etapa ${n})`} valor={pos.value} placeholder={(tipo ?? opcoes[0])!.valor ?? undefined}
                            referencia={refs.conferenciaValor(exibicao, etapa.key)} onChange={(v) => mudar({ value: v })} />
          ) : null}
          <CampoMascarado rotulo={`O que a tela mostra depois (etapa ${n})`} valor={pos.description} placeholder="O que a tela mostra quando deu certo"
                          referencia={refs.conferenciaDescricao(exibicao, etapa.key)} onChange={(v) => mudar({ description: v })} />
        </div>
      )}
    </Disclosure>
  );
}

/** "Editar os exemplos": o exemplo é o que a prova automática repete; a descrição do parâmetro não entra no fluxo salvo. */
export function EditorDosParametros({ parametros, exibicao = semExibicao(), onChange }: {
  parametros: TrainingProposal['parameters'];
  exibicao?: Exibicao;
  onChange: (lista: TrainingProposal['parameters']) => void;
}) {
  const mudar = (i: number, parcial: Partial<TrainingProposal['parameters'][number]>) =>
    onChange(parametros.map((p, k) => (k === i ? { ...p, ...parcial } : p)));
  return (
    <Disclosure bare summary="Editar os exemplos dos parâmetros">
      {() => (
        <div className={styles.editor}>
          {parametros.map((p, i) => (
            <div key={p.name} className={styles.parametro}>
              <strong>{`{${p.name}}`}</strong>
              <CampoMascarado rotulo={`Exemplo de {${p.name}}`} valor={p.example} placeholder="Um valor de exemplo"
                              referencia={refs.exemplo(exibicao, p.name)} onChange={(v) => mudar(i, { example: v })} />
            </div>
          ))}
        </div>
      )}
    </Disclosure>
  );
}
