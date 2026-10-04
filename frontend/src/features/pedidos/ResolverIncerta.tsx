import { CheckCheck } from 'lucide-react';
import { useState } from 'react';
import { toApiError } from '../../api/client';
import { Button } from '../../components/Button';
import { Dialog } from '../../components/Dialog';
import { Field, TextArea } from '../../components/Field';
import { toast } from '../../store/toasts';
import { apiPedidos } from './api';
import { mensagemDoErro } from './modelo';
import { usePedidosStore } from './store';

/** O mesmo teto da nota da quarentena (29.24); o backend recusa acima disso com 422. */
const NOTA_MAXIMA = 500;

/**
 * "Marcar como resolvida" (28.21): a pessoa conferiu no aparelho o que a ocorrência `incerta` fez e a dá por resolvida.
 * Não reexecuta nada nem muda o estado da ocorrência: só a tira das decisões abertas, e então o Retomar do pedido vale.
 * A nota é obrigatória (é o que diz depois o que foi conferido): o botão de confirmar não age em branco e o erro aparece
 * junto do campo, não só num aviso que some.
 */
export function ResolverIncerta({ pedidoId, ocorrenciaId, onResolvida }: {
  pedidoId: string; ocorrenciaId: string; onResolvida: () => void;
}) {
  const [aberto, setAberto] = useState(false);
  const [nota, setNota] = useState('');
  const [erro, setErro] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);

  const fechar = () => {
    if (enviando) return;
    setAberto(false);
    setNota('');
    setErro(null);
  };

  const confirmar = async () => {
    const texto = nota.trim();
    if (!texto) {
      setErro('Diga o que você conferiu: a nota fica registrada com o seu nome.');
      return;
    }
    setEnviando(true);
    setErro(null);
    try {
      await apiPedidos.resolverIncerta(pedidoId, ocorrenciaId, texto);
      toast({ tone: 'success', title: 'Ocorrência marcada como resolvida', message: 'Já dá para retomar o pedido, se não restar outra decisão.' });
      setAberto(false);
      setNota('');
      usePedidosStore.getState().bater();
      onResolvida();
    } catch (e) {
      const err = toApiError(e);
      if (err.code === 'invalid_state' || err.code === 'not_found') {
        // a ocorrência mudou (ou sumiu) por outro caminho: relê a tela, que mostra o que vale agora
        toast({ tone: 'warning', title: 'A ocorrência mudou', message: mensagemDoErro(err) });
        setAberto(false);
        onResolvida();
      } else {
        setErro(err.code === 'nota_obrigatoria' ? 'Diga o que você conferiu: a nota fica registrada com o seu nome.' : mensagemDoErro(err));
      }
    } finally {
      setEnviando(false);
    }
  };

  return (
    <>
      <Button size="sm" icon={CheckCheck} onClick={() => setAberto(true)}>Marcar como resolvida</Button>
      {aberto ? (
        <Dialog open onClose={fechar} title="Marcar a ocorrência como resolvida" icon={CheckCheck}
                footer={(
                  <>
                    <Button variant="ghost" onClick={fechar} disabled={enviando}>Voltar</Button>
                    <Button variant="primary" loading={enviando} onClick={() => void confirmar()}>Confirmar</Button>
                  </>
                )}>
          <p>
            Isto só registra que você conferiu. A ocorrência continua marcada como incerta e nada é executado de novo;
            ela deixa de segurar o pedido, e aí você retoma.
          </p>
          <Field label="O que você conferiu" unit="obrigatório" error={erro}
                 hint={`${nota.trim().length}/${NOTA_MAXIMA} caracteres`}>
            {({ id, describedBy, invalid }) => (
              <TextArea id={id} aria-describedby={describedBy} invalid={invalid} rows={3} maxLength={NOTA_MAXIMA} value={nota}
                        placeholder="Ex.: a mensagem não saiu no aparelho; pode seguir."
                        onChange={(e) => { setNota(e.target.value); if (erro) setErro(null); }} />
            )}
          </Field>
        </Dialog>
      ) : null}
    </>
  );
}
