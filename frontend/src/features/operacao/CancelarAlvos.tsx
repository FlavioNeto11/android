import { Ban } from 'lucide-react';
import { useMemo, useState } from 'react';
import { Button } from '../../components/Button';
import { Dialog } from '../../components/Dialog';
import { Checkbox } from '../../components/Field';
import { plural } from '../../lib/format';
import { toastError } from '../../store/toasts';
import { apiOperacoes } from './api';
import {
  FILTRO_VAZIO, MOTIVO_DO_IGNORADO, alvosDoFiltro, filtroVazio, motivoProvavelDeIgnorar, type FiltroDeCancelamento, type ResultadoDoCancelamento,
} from './filtroDeCancelamento';
import { ESTADOS_DO_ALVO, ESTAGIOS, ROTULO_DO_ESTADO, rotuloDoEstagio, type Alvo } from './modelo';
import styles from './Operacao.module.css';

const alterna = <T,>(lista: readonly T[], x: T): T[] => (lista.includes(x) ? lista.filter((y) => y !== x) : [...lista, x]);
const rotuloDoAlvo = (a: Alvo): string => a.persona ?? a.profile_id ?? a.id;

type Passo = 'filtro' | 'confirmar' | 'resultado';

/**
 * 31.199: "Cancelar alvos" (adendo v1.112): a pessoa escolhe por persona, estado, estágio ou aparelho (os filtros se somam), VÊ a lista dos
 * alvos que o filtro atinge, confirma, e o painel ecoa o que o central fez (cancelados e ignorados, com o motivo). A operação segue com
 * os outros alvos; para cancelá-la inteira há o botão da página. Sem filtro nenhum não há o que confirmar (o central também recusa).
 */
export function CancelarAlvos({ operacaoId, alvos, onFechar, onFeito }: {
  operacaoId: string; alvos: readonly Alvo[]; onFechar: () => void; onFeito: () => void;
}) {
  const [filtro, setFiltro] = useState<FiltroDeCancelamento>(FILTRO_VAZIO);
  const [passo, setPasso] = useState<Passo>('filtro');
  const [enviando, setEnviando] = useState(false);
  const [resultado, setResultado] = useState<ResultadoDoCancelamento | null>(null);

  const atingidos = useMemo(() => alvosDoFiltro(alvos, filtro), [alvos, filtro]);
  const valem = atingidos.filter((a) => motivoProvavelDeIgnorar(a) === null);
  const estagiosPresentes = useMemo(() => ESTAGIOS.filter((e) => alvos.some((a) => a.estagio === e.id)), [alvos]);
  const aparelhos = useMemo(() => [...new Set(alvos.flatMap((a) => (a.instance_id ? [a.instance_id] : [])))].sort(), [alvos]);
  const personas = useMemo(() => alvos.flatMap((a) => (a.profile_id ? [{ id: a.profile_id, rotulo: rotuloDoAlvo(a) }] : [])), [alvos]);
  const nomeDe = (id: string): string => personas.find((p) => p.id === id)?.rotulo ?? 'uma persona';

  const motivoSemRevisar = filtroVazio(filtro) ? 'Marque ao menos um filtro: cancelar a operação inteira é o botão da página.'
    : atingidos.length === 0 ? 'Nenhum alvo casa com o filtro.' : valem.length === 0 ? 'Todos os alvos do filtro já terminaram ou não têm execução: não há o que cancelar.' : null;

  async function enviar() {
    setEnviando(true);
    try {
      setResultado(await apiOperacoes.cancelarAlvos(operacaoId, filtro));
      setPasso('resultado');
    } catch (e) {
      toastError('Não foi possível cancelar os alvos', e);
      setPasso('filtro');
    } finally {
      setEnviando(false);
    }
  }

  const fechar = () => { if (passo === 'resultado') onFeito(); else onFechar(); };

  const lista = (itens: readonly Alvo[]) => (
    <ul className={styles.liberar} aria-label="Alvos atingidos">
      {itens.map((a) => {
        const ignora = motivoProvavelDeIgnorar(a);
        return (
          <li key={a.id} data-alvo-atingido={a.profile_id ?? a.id}>
            <strong>{rotuloDoAlvo(a)}</strong>
            <span className={styles.mudo}>
              {' · '}{a.estado ? ROTULO_DO_ESTADO[a.estado] : 'estado não informado'}
              {a.estagio ? ` · ${rotuloDoEstagio(a.estagio)}` : ''}{a.instance_id ? ` · ${a.instance_id}` : ''}
              {ignora ? ` · ${ignora === 'sem_execucao' ? 'sem execução' : 'já terminou'}: o central o ignora` : ''}
            </span>
          </li>
        );
      })}
    </ul>
  );

  return (
    <Dialog open onClose={fechar} title={passo === 'resultado' ? 'Alvos cancelados' : 'Cancelar alguns alvos?'} icon={Ban} size="lg"
            closeBlockedReason={enviando ? 'Enviando o cancelamento.' : null}
            footer={passo === 'resultado' ? <Button variant="primary" onClick={fechar}>Fechar</Button> : passo === 'confirmar' ? (
              <>
                <Button variant="ghost" disabledReason={enviando ? 'Enviando o cancelamento.' : null} onClick={() => setPasso('filtro')}>Voltar</Button>
                <Button variant="danger" loading={enviando} onClick={() => void enviar()}>Cancelar {plural(valem.length, 'alvo', 'alvos')}</Button>
              </>
            ) : (
              <>
                <Button variant="ghost" onClick={onFechar}>Voltar</Button>
                <Button variant="danger" disabledReason={motivoSemRevisar} onClick={() => setPasso('confirmar')}>Revisar e cancelar</Button>
              </>
            )}>
      {passo === 'filtro' ? (
        <>
          <p>
            Escolha quais alvos cancelar. Os filtros se somam: o alvo precisa casar com todos os que você marcar. A operação continua com os
            outros alvos.
          </p>
          <fieldset className={styles.filtroDeCancelar}>
            <legend>Estado</legend>
            {ESTADOS_DO_ALVO.map((e) => (
              <Checkbox key={e} checked={filtro.estados.includes(e)} onChange={() => setFiltro((f) => ({ ...f, estados: alterna(f.estados, e) }))}
                        label={`${ROTULO_DO_ESTADO[e]} (${alvos.filter((a) => a.estado === e).length})`} aria-label={`Estado ${ROTULO_DO_ESTADO[e]}`} />
            ))}
          </fieldset>
          <fieldset className={styles.filtroDeCancelar}>
            <legend>Estágio em que está</legend>
            {estagiosPresentes.map((e) => (
              <Checkbox key={e.id} checked={filtro.estagios.includes(e.id)} onChange={() => setFiltro((f) => ({ ...f, estagios: alterna(f.estagios, e.id) }))}
                        label={`${e.rotulo} (${alvos.filter((a) => a.estagio === e.id).length})`} aria-label={`Estágio ${e.rotulo}`} />
            ))}
          </fieldset>
          {aparelhos.length ? (
            <fieldset className={styles.filtroDeCancelar}>
              <legend>Aparelho</legend>
              {aparelhos.map((i) => (
                <Checkbox key={i} checked={filtro.instance_ids.includes(i)} onChange={() => setFiltro((f) => ({ ...f, instance_ids: alterna(f.instance_ids, i) }))}
                          label={`${i} (${alvos.filter((a) => a.instance_id === i).length})`} aria-label={`Aparelho ${i}`} />
              ))}
            </fieldset>
          ) : null}
          <fieldset className={styles.filtroDeCancelar}>
            <legend>Persona</legend>
            {personas.map((p) => (
              <Checkbox key={p.id} checked={filtro.profile_ids.includes(p.id)} onChange={() => setFiltro((f) => ({ ...f, profile_ids: alterna(f.profile_ids, p.id) }))}
                        label={p.rotulo} aria-label={`Persona ${p.rotulo}`} />
            ))}
          </fieldset>
          <p role="status" data-atingidos>
            {filtroVazio(filtro) ? 'Nenhum filtro marcado.' : `O filtro atinge ${plural(atingidos.length, 'alvo', 'alvos')}${atingidos.length !== valem.length ? `, dos quais ${plural(valem.length, 'tem', 'têm')} execução aberta` : ''}.`}
          </p>
        </>
      ) : null}
      {passo === 'confirmar' ? (
        <>
          <p>
            Cancelar <strong>{plural(valem.length, 'alvo', 'alvos')}</strong>: as execuções abertas deles são canceladas, pelo mesmo caminho do
            cancelamento de uma execução, e isso não se desfaz. Os outros alvos da operação seguem.
          </p>
          {lista(atingidos)}
        </>
      ) : null}
      {passo === 'resultado' && resultado ? (
        <>
          <p role="status" data-resultado>
            <strong>{plural(resultado.cancelados.length, 'alvo cancelado', 'alvos cancelados')}</strong>
            {resultado.ignorados.length ? `; ${plural(resultado.ignorados.length, 'ignorado', 'ignorados')} pelo central` : ''}.
          </p>
          {resultado.cancelados.length ? (
            <ul className={styles.liberar} aria-label="Alvos cancelados">
              {resultado.cancelados.map((id) => <li key={id} data-cancelado={id}>{nomeDe(id)}</li>)}
            </ul>
          ) : null}
          {resultado.ignorados.length ? (
            <ul className={styles.liberar} aria-label="Alvos ignorados">
              {resultado.ignorados.map((x) => <li key={x.profile_id} data-ignorado={x.profile_id}>{nomeDe(x.profile_id)}<span className={styles.mudo}> · {MOTIVO_DO_IGNORADO[x.motivo] ?? x.motivo}</span></li>)}
            </ul>
          ) : null}
        </>
      ) : null}
    </Dialog>
  );
}
