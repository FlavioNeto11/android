import { BookOpenCheck, TriangleAlert } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { Badge } from '../../components/Badge';
import { Banner } from '../../components/Banner';
import { Button } from '../../components/Button';
import { EmptyState } from '../../components/EmptyState';
import { Checkbox, Field, Select } from '../../components/Field';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import { formatInt } from '../../lib/format';
import { hashDe } from '../../lib/rotas';
import { formatDateTime } from '../../lib/time';
import { licoesDaOperacao, type ItemAprendido, type LeituraDoAprendizado } from './aprendizadoDaOperacao';
import { apiOperacoes } from './api';
import styles from './Operacao.module.css';
import { type Operacao } from './modelo';
import { semArroba } from './relatorio';

/**
 * A aba "Aprendizado" da operação (31.166): as 10 perguntas do dono respondidas pelas execuções dela (adendo v1.96), as
 * lições reforçadas e as contestadas com a contagem efetiva, e os avisos de conhecimento que não ficou registrado. Só
 * leitura, sem IA. A persona aparece pelo rótulo que a tela já usa; quem não tem rótulo conhecido vira "uma persona".
 */

const CONFIANCA: Record<'confirmado' | 'hipotese', { rotulo: string; tom: 'success' | 'warning' }> = {
  confirmado: { rotulo: 'Confirmado', tom: 'success' }, hipotese: { rotulo: 'Hipótese', tom: 'warning' },
};
const ROTULO_DO_ESCOPO: Record<string, string> = { app: 'do app', processo: 'do processo', persona: 'da persona', operacao: 'da operação', falha: 'de uma falha' };

const hrefDaExecucao = (runId: string): string => hashDe('execucoes', { segmentos: [runId] });
const numero = (n: number | null): string => (n === null ? '?' : formatInt(n));

function Contagem({ i }: { i: ItemAprendido }) {
  if (i.a_favor === null && i.contra === null) return null;
  return <span className={styles.mudo}>{numero(i.a_favor)} a favor · {numero(i.contra)} contra</span>;
}

function Item({ i, rotulos }: { i: ItemAprendido; rotulos: ReadonlyMap<string, string> }) {
  const conf = i.confianca ? CONFIANCA[i.confianca] : null;
  return (
    <li className={styles.itemDaLista}>
      <span className={styles.etiquetas}>
        <span className={styles.refMono}>{i.ref}</span>
        {conf ? <Badge tone={conf.tom} size="sm">{conf.rotulo}</Badge> : null}
        {i.escopo ? <span className={styles.mudo}>{ROTULO_DO_ESCOPO[i.escopo] ?? i.escopo}</span> : null}
        {i.persona ? <span className={styles.mudo}>· {rotulos.get(i.persona) ?? 'uma persona'}</span> : null}
        {i.inferida ? <Badge tone="muted" size="sm" title="O backlog de falhas não guarda a execução: o vínculo foi deduzido por app, ação e tipo de falha.">Inferida</Badge> : null}
        <Contagem i={i} />
      </span>
      {i.resumo ? <span>{semArroba(i.resumo)}</span> : null}
      {i.motivo ? <span className={styles.mudo}><strong>Por quê:</strong> {semArroba(i.motivo)}</span> : null}
      {i.fontes.length > 0 ? (
        <ul className={styles.lista} aria-label={`Fontes de ${i.ref}`}>
          {i.fontes.map((f) => <li key={f.ref}><span className={styles.refMono}>{f.ref}</span>{f.resumo ? <> — {semArroba(f.resumo)}</> : null}</li>)}
        </ul>
      ) : null}
    </li>
  );
}

function Licoes({ titulo, itens, rotulos }: { titulo: string; itens: ItemAprendido[]; rotulos: ReadonlyMap<string, string> }) {
  return (
    <section aria-label={titulo}>
      <h3 className={styles.subtitulo}>{titulo} ({formatInt(itens.length)})</h3>
      {itens.length === 0 ? <p className={styles.mudo}>Nenhuma.</p> : <ul className={styles.lista}>{itens.map((i) => <Item key={i.ref} i={i} rotulos={rotulos} />)}</ul>}
    </section>
  );
}

export function AprendizadoDaOperacaoTab({ op }: { op: Operacao }) {
  const [persona, setPersona] = useState('');
  const [simulados, setSimulados] = useState(false);
  const [leitura, setLeitura] = useState<LeituraDoAprendizado | null>(null);
  const [tentativa, setTentativa] = useState(0);

  // Persona só pelo rótulo que a tela já usa; o `profile_id` fica no valor da caixa, que não aparece.
  const personas = useMemo(() => {
    const vistos = new Map<string, string>();
    for (const a of op.alvos) if (a.profile_id && !vistos.has(a.profile_id)) vistos.set(a.profile_id, a.persona ?? 'Persona não informada');
    return vistos;
  }, [op.alvos]);

  useEffect(() => {
    if (op.exemplo) return undefined;
    const ctl = new AbortController();
    setLeitura(null);
    void apiOperacoes.aprendizado(op.id, ctl.signal, { persona, simulados }).then((l) => { if (!ctl.signal.aborted) setLeitura(l); });
    return () => ctl.abort();
  }, [op.id, op.exemplo, persona, simulados, tentativa]);

  const licoes = useMemo(() => (leitura?.situacao === 'lido' ? licoesDaOperacao(leitura.aprendizado.perguntas) : null), [leitura]);

  return (
    <div className={styles.blocoDoAprendizado}>
      <div className={styles.filtros}>
        <Field label="Persona">
          {({ id: campo }) => (
            <Select id={campo} small value={persona} disabled={op.exemplo || personas.size === 0} onChange={(e) => setPersona(e.target.value)}>
              <option value="">Todas e a operação inteira</option>
              {[...personas].map(([id, rotulo]) => <option key={id} value={id}>{rotulo}</option>)}
            </Select>
          )}
        </Field>
        <Checkbox label="Incluir simulados" checked={simulados} onChange={(e) => setSimulados(e.target.checked)} />
      </div>
      {op.exemplo ? (
        <EmptyState icon={BookOpenCheck} compact title="É um exemplo" hint="O aprendizado vem das execuções reais da operação." />
      ) : leitura === null ? (
        <LoadingRegion label="Lendo o aprendizado da operação"><Skeleton height={120} /></LoadingRegion>
      ) : leitura.situacao === 'indisponivel' ? (
        <EmptyState icon={BookOpenCheck} compact title="Aprendizado não disponível" hint={semArroba(leitura.motivo)}
                    actions={<Button size="sm" variant="outline" onClick={() => setTentativa((n) => n + 1)}>Ler de novo</Button>} />
      ) : (
        <>
          {leitura.aprendizado.avisos.length > 0 ? (
            <Banner tone="warning" icon={TriangleAlert} role="status" title={`${formatInt(leitura.aprendizado.avisos.length)} ${leitura.aprendizado.avisos.length === 1 ? 'aviso' : 'avisos'} sobre o conhecimento que o texto recebeu`}>
              <ul className={styles.lista}>
                {leitura.aprendizado.avisos.map((a, n) => (
                  <li key={`${a.run_id}-${a.step_id}-${n}`}>
                    {a.run_id ? <>Execução <a className={styles.link} href={hrefDaExecucao(a.run_id)}>{a.run_id}</a>{a.step_id ? <>, etapa <span className={styles.refMono}>{a.step_id}</span></> : null}: </> : null}
                    {semArroba(a.aviso)}
                  </li>
                ))}
              </ul>
            </Banner>
          ) : null}
          {licoes ? (
            <>
              <Licoes titulo="Lições reforçadas" itens={licoes.reforcadas} rotulos={personas} />
              <Licoes titulo="Lições contestadas" itens={licoes.contestadas} rotulos={personas} />
            </>
          ) : null}
          {leitura.aprendizado.perguntas.map((p) => (
            <section key={p.chave} aria-label={p.titulo}>
              <h3 className={styles.subtitulo}>{p.titulo} ({formatInt(p.itens.length)})</h3>
              {!p.veio ? <p className={styles.mudo}>O central não respondeu esta pergunta.</p>
                : p.itens.length === 0 ? <p className={styles.mudo}>Nada nesta operação.</p>
                  : <ul className={styles.lista}>{p.itens.map((i) => <Item key={i.ref} i={i} rotulos={personas} />)}</ul>}
            </section>
          ))}
          {leitura.aprendizado.nao_coberto.length > 0 ? (
            <section aria-label="O que esta leitura não cobre">
              <h3 className={styles.subtitulo}>O que esta leitura não cobre</h3>
              <ul className={styles.lista}>{leitura.aprendizado.nao_coberto.map((n) => <li key={n.chave}>{semArroba(n.motivo)}</li>)}</ul>
            </section>
          ) : null}
          {leitura.aprendizado.gerado_em ? <p className={styles.mudo}>Lido em {formatDateTime(leitura.aprendizado.gerado_em)}; não se atualiza sozinho.</p> : null}
        </>
      )}
    </div>
  );
}
