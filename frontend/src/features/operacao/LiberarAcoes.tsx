import { ShieldCheck } from 'lucide-react';
import { useMemo, useState } from 'react';
import { Button } from '../../components/Button';
import { Dialog } from '../../components/Dialog';
import { Checkbox } from '../../components/Field';
import { toast, toastError } from '../../store/toasts';
import { apiOperacoes } from './api';
import styles from './Operacao.module.css';
import { type AlvoPreparado } from './modelo';

/** Os motivos de recusa do servidor em palavras (o que o painel não conhece segue como veio). */
const MOTIVO_DA_RECUSA: Record<string, string> = {
  texto_divergente: 'o texto mudou depois que você o leu',
  'limite de ações executadas': 'o limite de ações executadas já foi atingido',
  'sem ação preparada': 'não há mais ação preparada',
};

/**
 * "Liberar" (regra do 31.49: aprovação sem o texto lido não libera escrita): a pessoa vê o texto de cada agente parado em "ação
 * preparada", marca os que libera (até o limite que ainda sobra) e o painel envia EXATAMENTE os textos exibidos; se o texto
 * mudou no servidor (409 `texto_divergente`), nada é liberado e a lista recarrega. Nenhuma aprovação automática: começa tudo
 * desmarcado.
 */
export function LiberarAcoes({ operacaoId, preparados, vagas, onFechar, onLiberado }: {
  operacaoId: string; preparados: AlvoPreparado[]; vagas: number; onFechar: () => void; onLiberado: () => void;
}) {
  const [marcados, setMarcados] = useState<ReadonlySet<string>>(new Set());
  const [enviando, setEnviando] = useState(false);
  const cheio = marcados.size >= vagas;
  const escolhidos = useMemo(() => preparados.filter((p) => marcados.has(p.profile_id)), [preparados, marcados]);

  async function enviar() {
    setEnviando(true);
    try {
      const r = await apiOperacoes.liberar(operacaoId, escolhidos.map((p) => ({ profile_id: p.profile_id, texto: p.texto })));
      if (r.liberados.length) {
        toast({ tone: 'success', title: 'Ações liberadas', message: `${r.liberados.length} ${r.liberados.length === 1 ? 'agente segue' : 'agentes seguem'} até a ação final.` });
      }
      if (r.recusados.length) {
        // A decisão é item a item: o que mudou ou estourou o limite fica parado e aparece na leitura nova.
        const motivos = [...new Set(r.recusados.map((x) => MOTIVO_DA_RECUSA[x.motivo] ?? x.motivo))].join('; ');
        toast({ tone: 'warning', title: r.liberados.length ? 'Alguns ficaram de fora' : 'Nada foi liberado', message: `${r.recusados.length} ${r.recusados.length === 1 ? 'agente' : 'agentes'}: ${motivos}.` });
      }
      onLiberado();
    } catch (e) {
      toastError('Não foi possível liberar as ações', e);
    } finally {
      setEnviando(false);
    }
  }

  return (
    <Dialog open onClose={onFechar} title="Liberar as ações paradas?" icon={ShieldCheck} size="lg"
            closeBlockedReason={enviando ? 'Enviando a liberação.' : null}
            footer={(
              <>
                <Button variant="ghost" onClick={onFechar}>Voltar</Button>
                <Button variant="primary" loading={enviando} disabledReason={escolhidos.length === 0 ? 'Marque ao menos um agente.' : null} onClick={() => void enviar()}>
                  Liberar {escolhidos.length || ''}
                </Button>
              </>
            )}>
      <p>
        Leia o texto de cada agente. Os marcados seguem até a ação final e a verificação, com exatamente este texto; se ele mudar, nada é
        liberado. Ainda cabem <strong>{vagas}</strong> {vagas === 1 ? 'conta' : 'contas'} no limite configurado.
      </p>
      <ul className={styles.liberar}>
        {preparados.map((p) => {
          const marcado = marcados.has(p.profile_id);
          return (
            <li key={p.profile_id}>
              <Checkbox
                checked={marcado}
                disabled={!marcado && cheio} aria-label={`Liberar o texto de ${p.persona}`}
                onChange={(e) => setMarcados((s) => { const n = new Set(s); if (e.target.checked) n.add(p.profile_id); else n.delete(p.profile_id); return n; })}
                label={<><strong>{p.persona}</strong>{p.conta ? <span className={styles.mudo}> · {p.conta}</span> : null}</>}
              />
              <p className={styles.resposta}>{p.texto}</p>
            </li>
          );
        })}
      </ul>
    </Dialog>
  );
}
