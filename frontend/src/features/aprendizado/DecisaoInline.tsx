import { type ReactNode, useState } from 'react';
import { Button } from '../../components/Button';
import { Field, TextInput } from '../../components/Field';
import { type AcaoDoItem, MOTIVO_MAX, erroDoMotivo } from './model';
import styles from './Aprendizado.module.css';

/**
 * O motivo que toda decisão sobre o livro exige (fica na trilha, `learning_transitions.reason`). Em linha, nunca
 * modal: quem decide vê o item ao lado do que está escrevendo. Mora à parte da linha do item porque o detalhe (o
 * parecer da IA, 30.17) também decide, e a linha importa o detalhe.
 */
export function DecisaoInline({ acao, rotulo, dica = 'Fica na trilha do item, com o seu nome.', semMotivo, motivoOpcional, motivoMax = MOTIVO_MAX, resumo, onConfirmar, onCancelar }: {
  acao: Pick<AcaoDoItem, 'confirmar' | 'perigo'>;
  rotulo?: string;
  dica?: string;
  /** 30.54: o que a decisão vai fazer, acima do campo (no aceite em lote, o que o curador sugere para cada item). */
  resumo?: ReactNode;
  /** O aviso do botão enquanto falta o motivo, quando ele não vai para a trilha (o parecer da IA guarda o seu à parte). */
  semMotivo?: string;
  /** 30.24: "Confirmar que fica" vale sem motivo (só o limite de tamanho). */
  motivoOpcional?: boolean;
  /** O limite da rota que recebe o motivo: cada rota tem o seu (o desfazer do "Decidido sozinho" aceita 300, o livro 500). */
  motivoMax?: number;
  /** Devolve a mensagem de erro, ou `null` quando deu certo. */
  onConfirmar: (motivo: string) => Promise<string | null>;
  onCancelar: () => void;
}) {
  const [motivo, setMotivo] = useState('');
  const [enviando, setEnviando] = useState(false);
  const [erro, setErro] = useState<string | null>(null);
  const invalido = motivoOpcional ? (motivo.trim() ? erroDoMotivo(motivo, motivoMax) : null)
    : semMotivo && !motivo.trim() ? semMotivo : erroDoMotivo(motivo, motivoMax);
  const legenda = rotulo ?? (motivoOpcional ? 'Motivo (opcional)' : 'Motivo');

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
      {resumo ? <div className={styles.decisaoResumo}>{resumo}</div> : null}
      <Field label={legenda} hint={dica} error={erro} className={styles.decisaoCampo}>
        {({ id, describedBy, invalid }) => (
          // 30.54: os botões na linha do campo, não na da dica (o dono via o "Aceitar" apagado e desalinhado embaixo).
          <div className={styles.decisaoLinha}>
            <TextInput id={id} aria-describedby={describedBy} invalid={invalid} value={motivo} maxLength={motivoMax}
                       autoFocus placeholder="Ex.: conferi a evidência e o alvo está certo" className={styles.decisaoTexto}
                       onChange={(e) => setMotivo(e.target.value)} />
            <div className={styles.decisaoAcoes}>
              <Button type="submit" size="sm" variant={acao.perigo ? 'danger' : 'primary'} loading={enviando} disabledReason={invalido}>
                {acao.confirmar}
              </Button>
              <Button size="sm" variant="ghost" onClick={onCancelar} disabled={enviando}>Cancelar</Button>
            </div>
          </div>
        )}
      </Field>
      {/* 30.54: por que o botão está apagado, à vista (o `disabledReason` só chegava ao leitor de tela). */}
      {invalido && !enviando ? <p className={styles.decisaoPendente} data-por-que-apagado>{invalido}</p> : null}
    </form>
  );
}
