import { CircleCheck, CircleX, FlaskConical, Hourglass, Play, ShieldCheck, ShieldQuestion, ShieldX, type LucideIcon } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { Badge } from '../../components/Badge';
import { Banner } from '../../components/Banner';
import { EmptyState } from '../../components/EmptyState';
import { Field, Select } from '../../components/Field';
import { Page } from '../../components/Page';
import { cx, formatInt } from '../../lib/format';
import type { Tone } from '../../lib/status';
import styles from './Operacao.module.css';
import {
  agregadoPorApp, contarPorEstado, ESTADOS_DO_ALVO, ESTAGIOS, estagiosAlcancados, estagioDeParada, isEstadoDoAlvo, isEstagio, lerOperacao,
  ROTULO_DA_VERIFICACAO, ROTULO_DO_ESTADO, rotuloDaSessao, rotuloDoEstagio, type Alvo, type EstadoDoAlvo, type EstagioId, type Operacao,
  type Verificada,
} from './modelo';
import { OPERACAO_DE_EXEMPLO } from './operacaoDeExemplo';

/**
 * A tela "Operação" (prova de 07/10, FULL INSTAGRAM): objetivo → agentes → estado de cada um → conhecimento → ação → evidência →
 * falha → resultado. Uma linha por ALVO (persona + conta + aparelho), nunca por aparelho; o pipeline do Instagram aparece em 14
 * estágios na ordem do dono; no topo, a capacidade (solicitados, contas, sessões, disponíveis, concluídas, bloqueadas e os
 * motivos): o déficit aparece, não se esconde. Só leitura. Até o adendo v1.94 a fonte é o exemplo fixo, e a tela diz isso.
 */

/** A fonte da operação. Trocar por `GET /api/operacoes/{id}` é mudar só esta função. */
function useOperacao(): Operacao | null {
  return useMemo(() => lerOperacao(OPERACAO_DE_EXEMPLO, true), []);
}

const TOM_DO_ESTADO: Record<EstadoDoAlvo, Tone> = {
  na_fila: 'muted', em_andamento: 'info', concluido: 'success', bloqueado: 'warning', falhou: 'danger', cancelado: 'muted',
};
const ICONE_DO_ESTADO: Record<EstadoDoAlvo, LucideIcon> = {
  na_fila: Hourglass, em_andamento: Play, concluido: CircleCheck, bloqueado: ShieldQuestion, falhou: CircleX, cancelado: CircleX,
};
const ICONE_DA_VERIFICACAO: Record<Verificada, { icone: LucideIcon; tom: Tone }> = {
  sim: { icone: ShieldCheck, tom: 'success' }, nao: { icone: ShieldX, tom: 'danger' }, nao_conferida: { icone: ShieldQuestion, tom: 'warning' },
};

const numero = (n: number | null): string => (n === null ? 'não informado' : formatInt(n));

function FaixaDeCapacidade({ op }: { op: Operacao }) {
  const c = op.capacidade;
  const celulas: { rotulo: string; valor: number | null; tom?: Tone }[] = [
    { rotulo: 'solicitados', valor: c.solicitados ?? op.alvos.length },
    { rotulo: 'contas existentes', valor: c.contas_existentes },
    { rotulo: 'sessões válidas', valor: c.sessoes_validas },
    { rotulo: 'disponíveis', valor: c.disponiveis },
    { rotulo: 'concluídas', valor: c.concluidas, tom: 'success' },
    { rotulo: 'bloqueadas', valor: c.bloqueadas, tom: 'warning' },
  ];
  return (
    <section className={styles.faixa} aria-labelledby="operacao-capacidade">
      <h2 id="operacao-capacidade" className={styles.subtitulo}>Capacidade</h2>
      <dl className={styles.celulas}>
        {celulas.map((x) => (
          <div key={x.rotulo} className={cx(styles.celula, x.tom === 'success' && styles.celulaOk, x.tom === 'warning' && styles.celulaAtencao)}>
            <dd className={styles.celulaValor}>{numero(x.valor)}</dd>
            <dt className={styles.celulaRotulo}>{x.rotulo}</dt>
          </div>
        ))}
      </dl>
      {c.motivos.length ? (
        <div>
          <h3 className={styles.subtitulo}>Motivos dos bloqueios</h3>
          <ul className={styles.motivos}>
            {c.motivos.map((m) => <li key={m.motivo}><strong>{formatInt(m.n)}</strong> {m.motivo}</li>)}
          </ul>
        </div>
      ) : null}
    </section>
  );
}

function PorApp({ alvos }: { alvos: readonly Alvo[] }) {
  const grupos = agregadoPorApp(alvos);
  return (
    <section aria-labelledby="operacao-por-app">
      <h2 id="operacao-por-app" className={styles.subtitulo}>Por aplicativo</h2>
      <ul className={styles.porApp}>
        {grupos.map((g) => (
          <li key={g.app}>
            <strong>{g.app}</strong>: {formatInt(g.alvos)} alvos, {formatInt(g.concluidos)} concluídos, {formatInt(g.verificados)} verificados,{' '}
            {formatInt(g.bloqueados)} bloqueados, {formatInt(g.falhos)} falhos
          </li>
        ))}
      </ul>
    </section>
  );
}

/** Os 14 estágios como pontos: cheios até onde chegou, o da parada marcado. A leitura de tela recebe a frase, não os pontos. */
function Pipeline({ alvo }: { alvo: Alvo }) {
  const feitos = estagiosAlcancados(alvo);
  const parada = alvo.estado === 'concluido' ? null : estagioDeParada(alvo);
  const frase = alvo.estado === 'concluido'
    ? `Concluiu até “${rotuloDoEstagio(alvo.estagio)}” (${feitos} de ${ESTAGIOS.length})`
    : `${feitos} de ${ESTAGIOS.length} estágios; parou em “${rotuloDoEstagio(parada)}”`;
  return (
    <div className={styles.pipeline} role="img" aria-label={frase} title={frase}>
      {ESTAGIOS.map((e, i) => (
        <span key={e.id} className={cx(styles.ponto, i < feitos && styles.pontoFeito, e.id === parada && alvo.estado !== 'em_andamento' && styles.pontoParada)} />
      ))}
    </div>
  );
}

function Linha({ alvo }: { alvo: Alvo }) {
  const estado = alvo.estado;
  const Icone = estado ? ICONE_DO_ESTADO[estado] : null;
  const ver = alvo.verificada ? ICONE_DA_VERIFICACAO[alvo.verificada] : null;
  return (
    <tr data-alvo={alvo.id}>
      <th scope="row" className={styles.persona}>{alvo.persona ?? 'Persona não informada'}</th>
      <td>{alvo.conta ?? <span className={styles.mudo}>sem conta</span>}</td>
      <td>{rotuloDaSessao(alvo.sessao)}</td>
      <td>{alvo.instance_id ? <>{alvo.instance_id}{alvo.servidor ? <span className={styles.mudo}> · {alvo.servidor}</span> : null}</> : <span className={styles.mudo}>sem aparelho</span>}</td>
      <td><Pipeline alvo={alvo} /></td>
      <td>
        {estado && Icone ? <Badge tone={TOM_DO_ESTADO[estado]} icon={Icone} size="sm">{ROTULO_DO_ESTADO[estado]}</Badge> : <span className={styles.mudo}>não informado</span>}
      </td>
      <td className={styles.acao}>
        {alvo.bloqueio ? <span><strong>Parou em {rotuloDoEstagio(alvo.bloqueio.estagio)}:</strong> {alvo.bloqueio.motivo}</span> : null}
        {!alvo.bloqueio && alvo.acao ? <span>{alvo.acao}</span> : null}
        {!alvo.bloqueio && !alvo.acao ? <span className={styles.mudo}>—</span> : null}
        {alvo.conhecimento_n !== null ? <span className={styles.mudo}> · {formatInt(alvo.conhecimento_n)} itens de conhecimento</span> : null}
      </td>
      <td>
        {alvo.verificada && ver ? <Badge tone={ver.tom} icon={ver.icone} size="sm">{ROTULO_DA_VERIFICACAO[alvo.verificada]}</Badge> : <span className={styles.mudo}>—</span>}
        {alvo.evidencia_id !== null ? <span className={styles.mudo}> · evidência {alvo.evidencia_id}</span> : null}
      </td>
    </tr>
  );
}

export function OperacaoPage() {
  const op = useOperacao();
  const [estado, setEstado] = useState<EstadoDoAlvo | ''>('');
  const [parou, setParou] = useState<EstagioId | ''>('');
  useEffect(() => { document.title = 'Operação · Central de Aparelhos'; }, []);

  if (!op) {
    return (
      <Page title="Operação" lead="Acompanhe cada agente da operação do início ao fim.">
        <EmptyState icon={FlaskConical} title="Não deu para ler a operação" hint="O corpo recebido não é uma operação (falta o id)." />
      </Page>
    );
  }
  const contagem = contarPorEstado(op.alvos);
  const alvos = op.alvos.filter((a) => (!estado || a.estado === estado) && (!parou || estagioDeParada(a) === parou));
  return (
    <Page title="Operação" lead="Acompanhe cada agente da operação do início ao fim: persona, conta, aparelho, estado, conhecimento, ação e resultado verificado.">
      {op.exemplo ? (
        <Banner tone="info" icon={FlaskConical} title="Dados de exemplo" role="status">
          O contrato das operações (adendo v1.94) ainda não está no ar: esta tela lê um exemplo fixo, com personas e contas inventadas.
          Nada aqui veio do parque.
        </Banner>
      ) : null}
      <section aria-labelledby="operacao-objetivo">
        <h2 id="operacao-objetivo" className={styles.subtitulo}>Objetivo</h2>
        <p className={styles.objetivo}>{op.objetivo || 'Sem objetivo informado.'}</p>
      </section>
      <FaixaDeCapacidade op={op} />
      <PorApp alvos={op.alvos} />
      <section aria-labelledby="operacao-agentes">
        <h2 id="operacao-agentes" className={styles.subtitulo}>Agentes ({formatInt(alvos.length)} de {formatInt(op.alvos.length)})</h2>
        <div className={styles.filtros}>
          <Field label="Estado">
            {({ id }) => (
              <Select id={id} small value={estado} onChange={(e) => setEstado(isEstadoDoAlvo(e.target.value) ? e.target.value : '')}>
                <option value="">Todos</option>
                {ESTADOS_DO_ALVO.map((s) => <option key={s} value={s}>{ROTULO_DO_ESTADO[s]} ({contagem[s]})</option>)}
              </Select>
            )}
          </Field>
          <Field label="Parou em">
            {({ id }) => (
              <Select id={id} small value={parou} onChange={(e) => setParou(isEstagio(e.target.value) ? e.target.value : '')}>
                <option value="">Qualquer estágio</option>
                {ESTAGIOS.map((e) => <option key={e.id} value={e.id}>{e.rotulo}</option>)}
              </Select>
            )}
          </Field>
        </div>
        {alvos.length === 0 ? (
          <EmptyState icon={FlaskConical} compact title="Nenhum agente com este filtro" hint="Limpe o filtro para ver todos." />
        ) : (
          <div className={styles.rolagem}>
            <table className={styles.tabela}>
              <caption className="sr-only">Um agente por linha: persona, conta, sessão, aparelho, pipeline do Instagram, estado, ação ou motivo e verificação.</caption>
              <thead>
                <tr>
                  <th scope="col">Persona</th><th scope="col">Conta</th><th scope="col">Sessão</th><th scope="col">Aparelho</th>
                  <th scope="col">Pipeline do Instagram</th><th scope="col">Estado</th><th scope="col">Ação final ou motivo</th><th scope="col">Resultado</th>
                </tr>
              </thead>
              <tbody>{alvos.map((a) => <Linha key={a.id} alvo={a} />)}</tbody>
            </table>
          </div>
        )}
      </section>
    </Page>
  );
}
