import { useState } from 'react';
import { Button } from '../../components/Button';
import { Field, TextInput } from '../../components/Field';
import { type AcaoDoItem, MOTIVO_MAX, erroDoMotivo } from './model';
import styles from './Aprendizado.module.css';

/**
 * O motivo que toda decisão sobre o livro exige (fica na trilha, `learning_transitions.reason`). Em linha, nunca
 * modal: quem decide vê o item ao lado do que está escrevendo. Mora à parte da linha do item porque o detalhe (o
 * parecer da IA, 30.17) também decide, e a linha importa o detalhe.
 */
export function DecisaoInline({ acao, rotulo = 'Motivo', dica = 'Fica na trilha do item, com o seu nome.', onConfirmar, onCancelar }: {
  acao: Pick<AcaoDoItem, 'confirmar' | 'perigo'>;
  rotulo?: string;
  dica?: string;
  /** Devolve a mensagem de erro, ou `null` quando deu certo. */
  onConfirmar: (motivo: string) => Promise<string | null>;
  onCancelar: () => void;
}) {
  const [motivo, setMotivo] = useState('');
  const [enviando, setEnviando] = useState(false);
  const [erro, setErro] = useState<string | null>(null);
  const invalido = erroDoMotivo(motivo);

  const enviar = async () => {
    if (invalido || enviando) return;
    setEnviando(true);
    setErro(null);
    const falha = await onConfirmar(motivo.trim());
    setEnviando(false);
    if (falha) setErro(falha);
  };

  return (
    <form className={styles.decisao} onSubmit={(e) => { e.preventDefault(); void enviar(); }}>
      <Field label={rotulo} hint={dica} error={erro} className={styles.decisaoCampo}>
        {({ id, describedBy, invalid }) => (
          <TextInput id={id} aria-describedby={describedBy} invalid={invalid} value={motivo} maxLength={MOTIVO_MAX}
                     autoFocus placeholder="Ex.: conferi a evidência e o alvo está certo"
                     onChange={(e) => setMotivo(e.target.value)} />
        )}
      </Field>
      <div className={styles.decisaoAcoes}>
        <Button type="submit" size="sm" variant={acao.perigo ? 'danger' : 'primary'} loading={enviando} disabledReason={invalido}>
          {acao.confirmar}
        </Button>
        <Button size="sm" variant="ghost" onClick={onCancelar} disabled={enviando}>Cancelar</Button>
      </div>
    </form>
  );
}
