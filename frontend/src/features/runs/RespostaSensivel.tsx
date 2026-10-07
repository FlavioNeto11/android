/**
 * 29.52: a pergunta que pede senha ou código não tem caixa de resposta. A resposta viraria comando: iria ao provedor de
 * IA e ficaria no histórico.
 */
import { KeyRound, Smartphone } from 'lucide-react';
import { Button } from '../../components/Button';
import { useUiStore } from '../../store/ui';
import type { PerguntaDaExecucao } from './model';
import styles from './Runs.module.css';

export function RespostaSensivel({ tipo, perguntas, instanceId }: {
  tipo: string;
  perguntas: readonly PerguntaDaExecucao[];
  instanceId: string | null;
}) {
  const setView = useUiStore((s) => s.setView);
  const openFocus = useUiStore((s) => s.openFocus);
  const codigo = tipo === 'codigo' || tipo === '2fa';
  return (
    <div className={styles.respostaSensivel} role="note" aria-label="Pergunta que pede credencial">
      <ul className={styles.questionList}>
        {perguntas.map((m, i) => (
          <li key={`${m.field}-${i}`} className={styles.question}>
            <span>{m.question}</span>
          </li>
        ))}
      </ul>
      <p className={styles.respostaSensivelTexto}>
        {codigo ? (
          <>
            <strong>O código não se responde aqui.</strong> Ele iria ao provedor de IA e ficaria no histórico. Digite-o
            você mesmo no aparelho, pelo controle manual, e depois faça o pedido de novo.
          </>
        ) : (
          <>
            <strong>A senha não se responde aqui.</strong> Ela iria ao provedor de IA e ficaria no histórico. Guarde-a na
            conta da persona (Personas → a pessoa → “Contas e acesso”), com o consentimento, e faça o pedido de novo: a
            automação a digita de lá, sem passar pela IA.
          </>
        )}
      </p>
      <div className={styles.respostaSensivelAcoes}>
        {codigo ? (
          <Button size="sm" icon={Smartphone} disabled={!instanceId}
                  disabledReason={instanceId ? null : 'Esta execução ainda não tem aparelho.'}
                  onClick={() => { if (instanceId) openFocus(instanceId); }}>
            Abrir o aparelho
          </Button>
        ) : (
          <Button size="sm" icon={KeyRound} onClick={() => setView('personas')}>
            Abrir Personas
          </Button>
        )}
      </div>
    </div>
  );
}
