/**
 * 31.90-E: o que a pessoa pode corrigir na proposta da IA além do texto: o que cada etapa CONFERE (a pós-condição) e os
 * exemplos e descrições dos parâmetros. O `save` já valida a proposta editada (31.83); aqui só se edita e a recusa
 * dele aparece no botão Salvar, como as outras. O nome do parâmetro não se edita: ele está no comando.
 */
import { Disclosure } from '../../components/Disclosure';
import { Select, TextInput } from '../../components/Field';
import type { TrainingProposal, TrainingStep } from '../../api/types';
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
export function EditorDaPosCondicao({ indice, etapa, onChange }: {
  indice: number;
  etapa: TrainingStep;
  onChange: (pos: PosCondicao) => void;
}) {
  const pos = etapa.postcondition ?? { kind: 'model_judged', value: '', description: '' };
  const n = indice + 1;
  const tipo = TIPOS.find((t) => t.kind === pos.kind);
  // `items_collected` (coleta) é lida pelo executor: aparece como está, e quem edita escolhe outro tipo se quiser trocar.
  const opcoes = tipo ? TIPOS : [{ kind: pos.kind, rotulo: pos.kind, valor: 'Valor' }, ...TIPOS];
  const mudar = (parcial: Partial<PosCondicao>) => onChange({ ...pos, ...parcial });
  return (
    <Disclosure bare summary="Editar o que a etapa confere" openWhen={semComprovacao(etapa)}>
      {() => (
        <div className={styles.editor}>
          <Select aria-label={`Tipo de conferência da etapa ${n}`} value={pos.kind} onChange={(e) => mudar({ kind: e.target.value as Tipo })}>
            {opcoes.map((t) => <option key={t.kind} value={t.kind}>{t.rotulo}</option>)}
          </Select>
          {(tipo ?? opcoes[0])?.valor ? (
            <TextInput aria-label={`${(tipo ?? opcoes[0])!.valor} (etapa ${n})`} value={pos.value} placeholder={(tipo ?? opcoes[0])!.valor ?? undefined}
                       onChange={(e) => mudar({ value: e.target.value })} />
          ) : null}
          <TextInput aria-label={`O que a tela mostra depois (etapa ${n})`} value={pos.description} placeholder="O que a tela mostra quando deu certo"
                     onChange={(e) => mudar({ description: e.target.value })} />
        </div>
      )}
    </Disclosure>
  );
}

/** "Editar exemplos e descrições": o exemplo é o que a prova automática repete; a descrição é o que a IA lê. */
export function EditorDosParametros({ parametros, onChange }: {
  parametros: TrainingProposal['parameters'];
  onChange: (lista: TrainingProposal['parameters']) => void;
}) {
  const mudar = (i: number, parcial: Partial<TrainingProposal['parameters'][number]>) =>
    onChange(parametros.map((p, k) => (k === i ? { ...p, ...parcial } : p)));
  return (
    <Disclosure bare summary="Editar exemplos e descrições dos parâmetros">
      {() => (
        <div className={styles.editor}>
          {parametros.map((p, i) => (
            <div key={p.name} className={styles.parametro}>
              <strong>{`{${p.name}}`}</strong>
              <TextInput aria-label={`Exemplo de {${p.name}}`} value={p.example} placeholder="Um valor de exemplo"
                         onChange={(e) => mudar(i, { example: e.target.value })} />
              <TextInput aria-label={`Descrição de {${p.name}}`} value={p.description} placeholder="O que este parâmetro é"
                         onChange={(e) => mudar(i, { description: e.target.value })} />
            </div>
          ))}
        </div>
      )}
    </Disclosure>
  );
}
